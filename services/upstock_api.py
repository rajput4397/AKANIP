import argparse
import time
import logging
import requests
import urllib.parse
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

from _utils.scan_config import load_strategies, normalize_interval
from _utils.logger_config import *

setup_logging()
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# --- 3. Historical Data Fetcher ---
def get_upstox_historical_data(instrument_key, from_date, to_date, access_token, interval="daily", max_retries=3):
    interval_path = {
        "daily": "day",
        "weekly": "week",
        "monthly": "month",
        "15min": "1minute",
    }[interval]
    encoded_key = urllib.parse.quote(instrument_key, safe='')
    url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/{interval_path}/{to_date}/{from_date}"
    
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                candles = data.get('data', {}).get('candles', [])
                if not candles:
                    return None

                df = pd.DataFrame(candles, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
                df['Date'] = pd.to_datetime(df['Date'])
                df = df.sort_values('Date').reset_index(drop=True)
                return df

            elif response.status_code == 429:
                wait_time = 3 * (2 ** attempt)
                log.warning(f"Rate limited (429) for {instrument_key}. Retrying in {wait_time}s...")
                time.sleep(wait_time)

            else:
                log.error(f"HTTP {response.status_code} for {instrument_key}: {response.text}")
                return None

        except Exception as e:
            log.error(f"Request exception for {instrument_key}: {e}")
            time.sleep(1)

    return None

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
                log.warning(f"No intraday candles available for {instrument_key}")
                return None
            df_1m = pd.DataFrame(candles, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
            df_1m['Date'] = pd.to_datetime(df_1m['Date'])
            return df_1m.sort_values('Date').reset_index(drop=True)
        else:
            log.error(f"Intraday API HTTP {response.status_code} for {instrument_key}: {response.text}")
    except Exception as e:
        log.error(f"Failed to fetch intraday data for {instrument_key}: {e}")
    return None


def resample_to_15min(df):
    if df is None or df.empty:
        return df
    indexed = df.sort_values('Date').set_index('Date')
    aggregations = {
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
    }
    if 'OI' in indexed.columns:
        aggregations['OI'] = 'last'
    bars = indexed.resample(
        '15min', origin='start_day', offset='15min', label='left', closed='left'
    ).agg(aggregations).dropna(subset=['Open', 'High', 'Low', 'Close'])
    return bars.reset_index()


def append_today_live_candle(df, instrument_key, access_token, interval):
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

    if interval == 'daily':
        if not df.empty and pd.Timestamp(df['Date'].iloc[-1]).date() == today_row['Date'].date():
            df = df.iloc[:-1]
        return pd.concat([df, pd.DataFrame([today_row])], ignore_index=True)

    period_frequency = {'weekly': 'W-FRI', 'monthly': 'M'}.get(interval)
    if period_frequency is None:
        raise ValueError(f"Cannot append a daily candle to interval '{interval}'")

    target_period = pd.Timestamp(today_row['Date']).to_period(period_frequency)
    if not df.empty:
        latest_period = pd.Timestamp(df['Date'].iloc[-1]).to_period(period_frequency)
        if latest_period == target_period:
            latest_index = df.index[-1]
            df.loc[latest_index, 'High'] = max(df.loc[latest_index, 'High'], today_row['High'])
            df.loc[latest_index, 'Low'] = min(df.loc[latest_index, 'Low'], today_row['Low'])
            df.loc[latest_index, 'Close'] = today_row['Close']
            df.loc[latest_index, 'Volume'] += today_row['Volume']
            return df

    return pd.concat([df, pd.DataFrame([today_row])], ignore_index=True)


def run_scan(scan, config_path):
    scan_name = scan.get("name", scan["csv_path"])
    interval = normalize_interval(scan.get("interval", "daily"))
    append_live = scan.get("include_today_intraday", False)
    if not isinstance(append_live, bool):
        raise ValueError(f"Scan '{scan_name}': include_today_intraday must be true or false")
    strategies = load_strategies(scan.get("strategies", []), interval, log, scan_name)

    token_path = Path(scan.get("token_path", "upstox_token.txt")).expanduser()
    if not token_path.is_absolute():
        token_path = config_path.parent / token_path
    if not token_path.is_file():
        raise ValueError(f"Scan '{scan_name}': token file not found: {token_path}")
    access_token = token_path.read_text(encoding="utf-8").strip()
    if not access_token:
        raise ValueError(f"Scan '{scan_name}': token file is empty: {token_path}")

    csv_path = Path(scan["csv_path"]).expanduser()
    if not csv_path.is_absolute():
        csv_path = config_path.parent / csv_path
    df_csv = pd.read_csv(csv_path)
    key_col = scan.get("instrument_key_column")
    if key_col is None:
        key_col = next(
            (column for column in df_csv.columns if 'instrument' in column.lower() or 'key' in column.lower()),
            df_csv.columns[0]
        )
    if key_col not in df_csv.columns:
        raise ValueError(f"Scan '{scan_name}': column '{key_col}' not found in {csv_path}")

    stock_col = scan.get("stock_column")
    if stock_col is not None and stock_col not in df_csv.columns:
        raise ValueError(f"Scan '{scan_name}': column '{stock_col}' not found in {csv_path}")

    key_to_stock = {}
    for _, row in df_csv.iterrows():
        instrument_value = row.get(key_col)
        if pd.isna(instrument_value):
            continue
        instrument_key = str(instrument_value).strip()
        if not instrument_key:
            continue
        stock_value = row.get(stock_col) if stock_col else None
        display_name = str(stock_value).strip() if pd.notna(stock_value) and str(stock_value).strip() else instrument_key
        key_to_stock[instrument_key] = display_name

    instrument_keys = list(dict.fromkeys(key_to_stock.keys()))
    if not instrument_keys:
        raise ValueError(f"Scan '{scan_name}': no instrument keys found in {csv_path}")

    default_history_days = {"daily": 365, "weekly": 3650, "15min": 29, "monthly": 3650}
    maximum_history_days = {"daily": 365, "weekly": 3650, "15min": 29, "monthly": 3650}
    history_days = scan.get("history_days", default_history_days[interval])
    if not isinstance(history_days, int) or history_days <= 0:
        raise ValueError(f"Scan '{scan_name}': history_days must be a positive integer")
    if history_days > maximum_history_days[interval]:
        raise ValueError(
            f"Scan '{scan_name}': Upstox {interval} history is limited to "
            f"{maximum_history_days[interval]} days"
        )

    results = {function_name: [] for function_name, _, _ in strategies}
    today = datetime.today().date()
    to_date = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    from_date = (today - timedelta(days=history_days)).strftime('%Y-%m-%d')

    log.info(
        f"Starting Upstox scan '{scan_name}' with {len(instrument_keys)} instruments, "
        f"interval={interval}, history_days={history_days}, to_date={to_date}, "
        f"include_today_intraday={append_live}."
    )

    for key in instrument_keys:
        display_name = key_to_stock.get(key, key)
        df = get_upstox_historical_data(key, from_date, to_date, access_token, interval)
        if df is not None:
            if interval == "15min":
                if append_live:
                    intraday = get_upstox_intraday_data(key, access_token)
                    if intraday is not None:
                        df = pd.concat([df, intraday], ignore_index=True)
                df = resample_to_15min(df)
            elif append_live:
                df = append_today_live_candle(df, key, access_token, interval)

            for function_name, func, parameters in strategies:
                is_match, ltp = func(df, **parameters)
                if is_match and ltp is not None:
                    candle_time = pd.Timestamp(df['Date'].iloc[-1])
                    candle_label = candle_time.strftime('%Y-%m-%d %H:%M:%S')
                    log.info(
                        f"[MATCH FOUND] {scan_name}: {display_name} ({key}) passed {function_name} "
                        f"| LTP: {round(ltp, 2)} | Candle: {candle_label}"
                    )
                    results[function_name].append((display_name, round(ltp, 2), candle_time))
        time.sleep(0.15)

    summary_message = "\n" + "=" * 50 + "\n"
    summary_message += f"                 {scan_name} SUMMARY                 \n"
    summary_message += "=" * 50 + "\n"
    for func_name, matched_stocks in results.items():
        summary_message += f"\n>>> STRATEGY: {func_name.upper()} <<<\n"
        if not matched_stocks:
            summary_message += "No stocks matched this criteria.\n"
        else:
            for symbol, price, trigger_time in matched_stocks:
                if trigger_time is not None:
                    summary_message += f"  - {symbol} (LTP: {price}, Candle: {trigger_time.strftime('%Y-%m-%d %H:%M:%S')})\n"
                else:
                    summary_message += f"  - {symbol} (LTP: {price})\n"
        summary_message += "-" * 50 + "\n"
    print(summary_message)
    log.info(summary_message)
    return summary_message


def main():
    parser = argparse.ArgumentParser(description="Run configured Upstox scans.")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    args = parser.parse_args()
    from services.run_scans import run_scans
    run_scans(args.config, provider_filter="upstox")


if __name__ == "__main__":
    main()