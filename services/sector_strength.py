import argparse
import logging
import os
import requests
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from _utils.logger_config import setup_logging
from services.telegram_alert import send_telegram_alert
from services.upstock_api import get_upstox_historical_data

setup_logging()
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECTORS_DIR = PROJECT_ROOT / "sectors"

SECTOR_INDEX_INSTRUMENT_KEYS = {
    "AUTO": "NSE_INDEX|Nifty Auto",
    "PSU_BANK": "NSE_INDEX|Nifty PSU Bank",
    "BANK": "NSE_INDEX|Nifty Bank",
    "ENERGY": "NSE_INDEX|Nifty Energy",
    "FINSERV": "NSE_INDEX|Nifty Fin Service",
    "FMCG": "NSE_INDEX|Nifty FMCG",
    "IT": "NSE_INDEX|Nifty IT",
    "METAL": "NSE_INDEX|Nifty Metal",
    "PHARMA": "NSE_INDEX|Nifty Pharma",
    "REALTY": "NSE_INDEX|Nifty Realty",
}

# --- DUAL WINDOW CONFIGURATION ---
TREND_LOOKBACK = 20    # 20-day baseline for medium-term trend
TRIGGER_LOOKBACK = 5   # 5-day trigger for immediate capital flow
TRADING_DAYS_PER_YEAR = 252
TOP_N_STOCKS = 3


def _get_access_token():
    access_token = os.getenv("UPSTOX_TOKEN", "").strip()
    if access_token:
        return access_token
    token_path = PROJECT_ROOT / "upstox_token.txt"
    if token_path.is_file():
        return token_path.read_text(encoding="utf-8").strip()
    return ""


def get_upstox_intraday_data(instrument_key, access_token):
    encoded_key = urllib.parse.quote(instrument_key, safe='')
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{encoded_key}/1minute"
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            candles = response.json().get('data', {}).get('candles', [])
            if not candles:
                return None
            df_1m = pd.DataFrame(candles, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
            df_1m['Date'] = pd.to_datetime(df_1m['Date'])
            # Strip timezone to prevent concatenation errors with historical data
            df_1m['Date'] = df_1m['Date'].dt.tz_localize(None)
            return df_1m.sort_values('Date').reset_index(drop=True)
        elif response.status_code == 429:
            log.warning(f"Rate limited on intraday for {instrument_key}")
    except Exception as e:
        log.error(f"Failed to fetch intraday data for {instrument_key}: {e}")
    return None


def append_today_live_candle(df, instrument_key, access_token):
    intraday = get_upstox_intraday_data(instrument_key, access_token)
    if intraday is None or intraday.empty:
        return df

    today_row = {
        'Date': intraday['Date'].iloc[-1].floor('D'),
        'Open': intraday['Open'].iloc[0],
        'High': intraday['High'].max(),
        'Low': intraday['Low'].min(),
        'Close': intraday['Close'].iloc[-1],
        'Volume': intraday['Volume'].sum(),
        'OI': intraday['OI'].iloc[-1] if 'OI' in intraday.columns else 0,
    }

    if not df.empty and pd.Timestamp(df['Date'].iloc[-1]).date() == today_row['Date'].date():
        # If a candle for today already exists in the historical data, overwrite it
        df = df.iloc[:-1]
    
    return pd.concat([df, pd.DataFrame([today_row])], ignore_index=True)


def _fetch_history(instrument_key, access_token, lookback_days):
    today = datetime.today().date()
    to_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    from_date = (today - timedelta(days=lookback_days * 3)).strftime("%Y-%m-%d")
    
    df = get_upstox_historical_data(instrument_key, from_date, to_date, access_token, interval="daily")
    if df is None or df.empty:
        return None
    
    # Ensure historical dates are timezone-naive
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
        
    # Append the live intraday action to update the final candle
    df = append_today_live_candle(df, instrument_key, access_token)
        
    df = df.tail(lookback_days + 1).reset_index(drop=True)
    if len(df) < lookback_days + 1:
        return None
    return df


def _cumulative_return(df):
    first_close = df["Close"].iloc[0]
    last_close = df["Close"].iloc[-1]
    if not first_close:
        return None
    return (last_close - first_close) / first_close


def _annualized_volatility(df):
    daily_returns = df["Close"].pct_change().dropna()
    if len(daily_returns) < 2:
        return None
    return daily_returns.std(ddof=1) * (TRADING_DAYS_PER_YEAR ** 0.5)


def _volatility_adjusted_return(df):
    cum_return = _cumulative_return(df)
    vol = _annualized_volatility(df)
    if cum_return is None or vol is None or vol == 0:
        return None
    return cum_return / vol


def _zscore(series):
    mean = series.mean()
    std = series.std(ddof=1)
    if not std:
        return pd.Series(0.0, index=series.index)
    return (series - mean) / std


def _sector_var_scores(access_token):
    long_scores = {}
    short_scores = {}
    
    for sector, instrument_key in SECTOR_INDEX_INSTRUMENT_KEYS.items():
        df_long = _fetch_history(instrument_key, access_token, TREND_LOOKBACK)
        if df_long is None:
            log.warning(f"Not enough data for sector index {sector} ({instrument_key})")
            continue
            
        df_short = df_long.tail(TRIGGER_LOOKBACK + 1).reset_index(drop=True)

        var_long = _volatility_adjusted_return(df_long)
        var_short = _volatility_adjusted_return(df_short)

        if var_long is not None:
            long_scores[sector] = var_long
        if var_short is not None:
            short_scores[sector] = var_short
            
        time.sleep(0.15)
        
    return pd.Series(long_scores, dtype=float), pd.Series(short_scores, dtype=float)


def _load_sector_constituents(sector):
    csv_path = SECTORS_DIR / f"{sector}.csv"
    if not csv_path.is_file():
        log.warning(f"No stock CSV found for sector {sector}: {csv_path}")
        return []
    df = pd.read_csv(csv_path)
    if df.empty:
        return []
    key_col = next(
        (column for column in df.columns if "instrument" in column.lower() or "key" in column.lower()),
        df.columns[0],
    )
    name_col = next((column for column in df.columns if column != key_col), key_col)
    constituents = []
    for _, row in df.iterrows():
        instrument_key = str(row.get(key_col, "")).strip()
        if not instrument_key:
            continue
        stock_name = str(row.get(name_col, instrument_key)).strip() or instrument_key
        constituents.append((instrument_key, stock_name))
    return constituents


def _rank_sector_stocks(sector, access_token, lookback):
    ranked = []
    for instrument_key, stock_name in _load_sector_constituents(sector):
        df = _fetch_history(instrument_key, access_token, lookback)
        if df is None:
            continue
        cum_return = _cumulative_return(df)
        if cum_return is None:
            continue
        ranked.append((stock_name, cum_return))
        time.sleep(0.15)
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _format_stock_list(stocks):
    return ", ".join(f"{name} ({pct * 100:+.1f}%)" for name, pct in stocks)


def _format_sector_block(title, df_subset, access_token):
    lines = [f"\n>>> {title} <<<"]
    if df_subset.empty:
        lines.append("  (No sectors currently match this setup)")
        return lines

    for sector, row in df_subset.iterrows():
        lines.append(f"{sector} (5d Z={row['Trigger_Z']:+.2f} | 20d Z={row['Trend_Z']:+.2f})")
        stocks = _rank_sector_stocks(sector, access_token, TRIGGER_LOOKBACK)
        
        if not stocks:
            lines.append("  (no stock data in sectors/" + sector + ".csv)")
            continue
            
        if len(stocks) <= TOP_N_STOCKS * 2:
            lines.append("  " + _format_stock_list(stocks))
        else:
            lines.append("  Strongest: " + _format_stock_list(stocks[:TOP_N_STOCKS]))
            lines.append("  Weakest:   " + _format_stock_list(stocks[-TOP_N_STOCKS:][::-1]))
    return lines


def run():
    access_token = _get_access_token()
    if not access_token:
        raise ValueError(
            "Configure UPSTOX_TOKEN or provide a token file at upstox_token.txt"
        )

    long_scores, short_scores = _sector_var_scores(access_token)
    if len(long_scores) < 2 or len(short_scores) < 2:
        raise ValueError("Not enough sector index data to rank sectors")

    z_long = _zscore(long_scores)
    z_short = _zscore(short_scores)

    df_scores = pd.DataFrame({
        "Trend_Z": z_long,
        "Trigger_Z": z_short
    })

    leaders = df_scores[(df_scores["Trend_Z"] > 0) & (df_scores["Trigger_Z"] > 0)].sort_values(by="Trigger_Z", ascending=False)
    reversals = df_scores[(df_scores["Trend_Z"] < 0) & (df_scores["Trigger_Z"] > 0.5)].sort_values(by="Trigger_Z", ascending=False)
    laggards = df_scores[df_scores["Trigger_Z"] < 0].sort_values(by="Trigger_Z", ascending=True)

    lines = ["DUAL-WINDOW SECTOR STRENGTH SUMMARY", "=" * 50]
    lines += _format_sector_block("TREND LEADERS (Strong Baseline + Immediate Bids)", leaders, access_token)
    lines += _format_sector_block("OVERSOLD REVERSALS (Weak Baseline + Sudden Bids)", reversals, access_token)
    lines += _format_sector_block("SECTOR WEAKNESS (Capital Outflow Last 5 Days)", laggards, access_token)

    summary = "\n".join(lines)
    print(summary)
    log.info(summary)
    send_telegram_alert(summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Dual-Window Nifty sector strength + stock alert.")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()