import os
import sys
import time
import json
import logging
import requests
import urllib.parse
import pandas as pd
from datetime import datetime, timedelta

# Import the conditions file as a module so we can dynamically fetch functions from it
import _utils.all_conditions as conditions 
from _utils.logger_config import *

setup_logging()
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

# --- 2. Load Token ---
TOKEN_FILE = r"C:\Users\admin\Documents\Ninja\Audacity\AKANIP\upstox_token.txt"

if not os.path.exists(TOKEN_FILE):
    log.error(f"Token file '{TOKEN_FILE}' not found.")
    sys.exit(1)

with open(TOKEN_FILE, "r") as f:
    ACCESS_TOKEN = f.read().strip()

# --- 3. Historical Data Fetcher ---
def get_upstox_historical_data(instrument_key, from_date, to_date, max_retries=3):
    encoded_key = urllib.parse.quote(instrument_key, safe='')
    url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/day/{to_date}/{from_date}"
    
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {ACCESS_TOKEN}'
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

# --- 4. Strategy Conditions ---


def append_today_live_candle(df, instrument_key):
    """
    Fetches today's 1-minute intraday candles from Upstox public endpoint
    and aggregates them into today's synthetic daily candle up to the current minute.
    """
    encoded_key = urllib.parse.quote(instrument_key, safe='')
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{encoded_key}/1minute"
    
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {ACCESS_TOKEN}'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json().get('data', {})
            candles = data.get('candles', [])
            if not candles:
                log.warning(f"No intraday candles available for {instrument_key}")
                return df

            # Parse 1-minute candles
            df_1m = pd.DataFrame(candles, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
            df_1m['Date'] = pd.to_datetime(df_1m['Date'])
            df_1m = df_1m.sort_values('Date').reset_index(drop=True)

            # Extract today's aggregated OHLCV
            today_date = df_1m['Date'].iloc[-1].floor('D')
            
            today_row = pd.DataFrame([{
                'Date': today_date,
                'Open': df_1m['Open'].iloc[0],          # First candle open
                'High': df_1m['High'].max(),             # Today's high so far
                'Low': df_1m['Low'].min(),               # Today's low so far
                'Close': df_1m['Close'].iloc[-1],        # Current LTP
                'Volume': df_1m['Volume'].sum(),        # Total cumulative volume
                'OI': 0
            }])

            # Replace today's row if historical data already contains an incomplete entry
            if df['Date'].iloc[-1].strftime('%Y-%m-%d') == today_date.strftime('%Y-%m-%d'):
                df = df.iloc[:-1]

            df = pd.concat([df, today_row], ignore_index=True)
            
        else:
            log.error(f"Intraday API HTTP {response.status_code} for {instrument_key}: {response.text}")
            
    except Exception as e:
        log.error(f"Failed to fetch intraday data for {instrument_key}: {e}")
        
    return df

# --- 5. Main Execution ---
def main():
    log.info("=" * 60)
    log.info("Starting Execution Run")
    
    # --- Load Configuration ---
    config_path = r"_utils\config.json"
    if not os.path.exists(config_path):
        log.error(f"Config file not found at {config_path}")
        sys.exit(1)
        
    with open(config_path, "r") as f:
        config = json.load(f)
        
    append_live = config.get("settings", {}).get("append_today_prices", False)
    
    # --- Dynamically Load Active Functions ---
    active_functions = []
    for func_name, is_enabled in config.get("strategies", {}).items():
        if is_enabled:
            # Check if the function actually exists in all_conditions.py
            if hasattr(conditions, func_name):
                active_functions.append(getattr(conditions, func_name))
                log.info(f"Loaded strategy: {func_name}")
            else:
                log.warning(f"Strategy '{func_name}' enabled in config but not found in _utils.all_conditions.py")

    if not active_functions:
        log.error("No active strategies found to execute. Exiting.")
        sys.exit(1)

    # --- Initialize Results Dictionary ---
    # Creates a dictionary like: {'check_momentum_conditions': [], 'check_volume_price_spike': []}
    results = {func.__name__: [] for func in active_functions}

    # --- Load CSV ---
    csv_path = r"C:\Users\admin\Documents\Ninja\Audacity\AKANIP\indian_options.csv"
    try:
        df_csv = pd.read_csv(csv_path)
        key_col = next(
            (c for c in df_csv.columns if 'instrument' in c.lower() or 'key' in c.lower()), 
            df_csv.columns[0]
        )
        raw_keys = df_csv[key_col].dropna().astype(str).str.strip().tolist()
        instrument_keys = [k for k in raw_keys if k.startswith("NSE_EQ")]
        
        log.info(f"Loaded {len(instrument_keys)} instrument keys from CSV.")
    except Exception as e:
        log.error(f"Failed to load CSV at {csv_path}: {e}")
        sys.exit(1)

    if not instrument_keys:
        sys.exit(1)

    to_date = datetime.today().strftime('%Y-%m-%d')
    from_date = (datetime.today() - timedelta(days=700)).strftime('%Y-%m-%d')

    log.info(f"Starting Scan. Append Live Data is set to: {append_live}")

    # --- Sequential Loop ---
    for idx, key in enumerate(instrument_keys, start=1):
        df = get_upstox_historical_data(key, from_date, to_date)

        if df is not None:
            # Conditionally append live data based on config
            if append_live:
                df = append_today_live_candle(df, key)
            
            # Test the stock against EVERY active function
            for func in active_functions:
                is_match, ltp = func(df)
                if is_match:
                    log.info(f"[MATCH FOUND] {key} passed {func.__name__} | LTP: {round(ltp, 2)}")
                    results[func.__name__].append((key, round(ltp, 2)))
        
        time.sleep(0.15)

    log.info("Scan complete.")
    
    # --- Print Grouped Results ---
    summary_message = "\n" + "="*50 + "\n"
    summary_message += "                 FINAL SUMMARY                 \n"
    summary_message += "="*50 + "\n"
    
    for func_name, matched_stocks in results.items():
        summary_message += f"\n>>> STRATEGY: {func_name.upper()} <<<\n"
        
        if not matched_stocks:
            summary_message += "No stocks matched this criteria.\n"
        else:
            # Create a clean list of stocks for this specific function
            for key, price in matched_stocks:
                summary_message += f"  - {key} (LTP: {price})\n"
                
        summary_message += "-" * 50 + "\n"
        
    # This will print to your console AND write to your log file
    print(summary_message)
    log.info(summary_message)

if __name__ == "__main__":
    main()