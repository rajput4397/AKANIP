import argparse
import logging
import os
import time
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

# Upstox instrument_key for each Nifty sectoral index, confirmed against
# Upstox's published instrument master (assets.upstox.com/.../complete.csv.gz).
SECTOR_INDEX_INSTRUMENT_KEYS = {
    "AUTO": "NSE_INDEX|Nifty Auto",
    "BANK": "NSE_INDEX|Nifty Bank",
    "ENERGY": "NSE_INDEX|Nifty Energy",
    "FINSEREXBNK": "NSE_INDEX|Nifty FinSerExBnk",
    "FMCG": "NSE_INDEX|Nifty FMCG",
    "IT": "NSE_INDEX|Nifty IT",
    "MEDIA": "NSE_INDEX|Nifty Media",
    "METAL": "NSE_INDEX|Nifty Metal",
    "PHARMA": "NSE_INDEX|Nifty Pharma",
    "REALTY": "NSE_INDEX|Nifty Realty",
}

LOOKBACK_DAYS = 20
TRADING_DAYS_PER_YEAR = 252
TOP_N_SECTORS = 3
TOP_N_STOCKS = 3


def _get_access_token():
    access_token = os.getenv("UPSTOX_TOKEN", "").strip()
    if access_token:
        return access_token
    token_path = PROJECT_ROOT / "upstox_token.txt"
    if token_path.is_file():
        return token_path.read_text(encoding="utf-8").strip()
    return ""


def _fetch_history(instrument_key, access_token, lookback_days):
    today = datetime.today().date()
    to_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    # Generous calendar-day buffer so weekends/holidays still leave >= lookback_days rows.
    from_date = (today - timedelta(days=lookback_days * 3)).strftime("%Y-%m-%d")
    df = get_upstox_historical_data(instrument_key, from_date, to_date, access_token, interval="daily")
    if df is None or df.empty:
        return None
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
    scores = {}
    for sector, instrument_key in SECTOR_INDEX_INSTRUMENT_KEYS.items():
        df = _fetch_history(instrument_key, access_token, LOOKBACK_DAYS)
        if df is None:
            log.warning(f"Not enough data for sector index {sector} ({instrument_key})")
            continue
        var = _volatility_adjusted_return(df)
        if var is None:
            log.warning(f"Could not compute VAR for sector index {sector}")
            continue
        scores[sector] = var
        time.sleep(0.15)
    return pd.Series(scores, dtype=float)


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


def _rank_sector_stocks(sector, access_token):
    ranked = []
    for instrument_key, stock_name in _load_sector_constituents(sector):
        df = _fetch_history(instrument_key, access_token, LOOKBACK_DAYS)
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


def _format_sector_block(title, ranked_sectors, access_token):
    lines = [f"\n>>> {title} <<<"]
    for sector, z_score in ranked_sectors.items():
        lines.append(f"{sector} (Z={z_score:+.2f})")
        stocks = _rank_sector_stocks(sector, access_token)
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

    var_scores = _sector_var_scores(access_token)
    if len(var_scores) < 2:
        raise ValueError("Not enough sector index data to rank sectors")

    z_scores = _zscore(var_scores).sort_values(ascending=False)
    strongest = z_scores.head(TOP_N_SECTORS)
    weakest = z_scores.tail(TOP_N_SECTORS).sort_values()

    lines = ["SECTOR STRENGTH SUMMARY", "=" * 50]
    lines += _format_sector_block("STRONGEST SECTORS", strongest, access_token)
    lines += _format_sector_block("WEAKEST SECTORS", weakest, access_token)

    summary = "\n".join(lines)
    print(summary)
    log.info(summary)
    send_telegram_alert(summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Daily Nifty sector strength + top/bottom stock alert.")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
