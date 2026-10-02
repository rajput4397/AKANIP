import argparse
import logging
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path

# Import all conditions dynamically from utils
import _utils.all_conditions as conditions
from _utils.scan_config import load_strategies, normalize_interval
from _utils.logger_config import *

setup_logging()
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

log.info("hi")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def run_scan(scan, config_path):
    scan_name = scan.get("name", scan["csv_path"])
    interval = normalize_interval(scan.get("interval", "daily"))
    yf_intervals = {"daily": "1d", "weekly": "1wk", "15min": "15m", "monthly": "1mo"}
    strategies = load_strategies(scan.get("strategies", []), interval, log, scan_name)

    csv_path = Path(scan["csv_path"]).expanduser()
    if not csv_path.is_absolute():
        csv_path = config_path.parent / csv_path
    df_csv = pd.read_csv(csv_path)
    ticker_col = scan.get("ticker_column")
    if ticker_col is None:
        ticker_col = next(
            (column for column in df_csv.columns if 'ticker' in column.lower() or 'symbol' in column.lower()),
            df_csv.columns[0]
        )
    if ticker_col not in df_csv.columns:
        raise ValueError(f"Scan '{scan_name}': column '{ticker_col}' not found in {csv_path}")
    tickers = list(dict.fromkeys(df_csv[ticker_col].dropna().astype(str).str.strip().tolist()))
    if not tickers:
        raise ValueError(f"Scan '{scan_name}': no tickers found in {csv_path}")

    default_history_days = {"daily": 700, "weekly": 3650, "15min": 59, "monthly": 9125}
    history_days = scan.get("history_days", default_history_days[interval])
    if not isinstance(history_days, int) or history_days <= 0:
        raise ValueError(f"Scan '{scan_name}': history_days must be a positive integer")
    if interval == "15min" and history_days > 59:
        raise ValueError(f"Scan '{scan_name}': yfinance intraday history is limited to 60 days")

    results = {function_name: [] for function_name, _, _ in strategies}
    end_date = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    start_date = (datetime.today() - timedelta(days=history_days)).strftime('%Y-%m-%d')
    log.info(
        f"Starting yfinance scan '{scan_name}' with {len(tickers)} tickers, "
        f"interval={interval}, history_days={history_days}."
    )

    for ticker in tickers:
        try:
            df = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                interval=yf_intervals[interval],
                progress=False,
            )
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            date_col = next((column for column in df.columns if 'date' in str(column).lower()), df.columns[0])
            df = df.rename(columns={date_col: "Date"})
            df['Close'] = df['Close'].fillna(df['High'])
            cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
            stock_input = df[[column for column in cols if column in df.columns]].copy()

            for function_name, func, parameters in strategies:
                is_match, ltp = func(stock_input, **parameters)
                if is_match and ltp is not None:
                    log.info(f"[MATCH FOUND] {scan_name}: {ticker} passed {function_name} | LTP: {round(ltp, 2)}")
                    results[function_name].append((ticker, round(ltp, 2)))
        except Exception as e:
            log.error(f"Error processing {ticker} in scan '{scan_name}': {e}")

    summary_message = "\n" + "=" * 50 + "\n"
    summary_message += f"                 {scan_name} SUMMARY                 \n"
    summary_message += "=" * 50 + "\n"
    for func_name, matched_stocks in results.items():
        summary_message += f"\n>>> STRATEGY: {func_name.upper()} <<<\n"
        if not matched_stocks:
            summary_message += "No stocks matched this criteria.\n"
        else:
            for symbol, price in matched_stocks:
                summary_message += f"  - {symbol} (LTP: {price})\n"
        summary_message += "-" * 50 + "\n"
    print(summary_message)
    log.info(summary_message)


def main():
    parser = argparse.ArgumentParser(description="Run configured yfinance scans.")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    args = parser.parse_args()
    from services.run_scans import run_scans
    run_scans(args.config, provider_filter="yfinance")

if __name__ == "__main__":
    main()