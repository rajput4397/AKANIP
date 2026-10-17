import os
import sys
import logging
import math
import requests
import json
import argparse
import pandas as pd
from pathlib import Path

# Root directory of AKANIP project
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from _utils.logger_config import setup_logging

setup_logging(filename="options_spread_scanner.log")
log = logging.getLogger("OptionsSpreadScanner")

def get_spot_price(instrument_key: str, headers: dict) -> float:
    url = "https://api.upstox.com/v2/market-quote/ltp"
    params = {"instrument_key": instrument_key}
    response = requests.get(url, params=params, headers=headers, timeout=10)
    data = response.json()
    if data.get("status") == "success":
        quote_data = data.get("data", {})
        if instrument_key in quote_data:
            return float(quote_data[instrument_key]["last_price"])
        elif quote_data:
            return float(next(iter(quote_data.values()))["last_price"])
    return 0.0

def get_option_chain(instrument_key: str, expiry_date: str, headers: dict) -> list:
    url = "https://api.upstox.com/v2/option/chain"
    params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
    response = requests.get(url, params=params, headers=headers, timeout=10)
    return response.json().get("data", [])

def get_lot_size(instrument_key: str, headers: dict) -> int:
    url = "https://api.upstox.com/v2/option/contract"
    params = {"instrument_key": instrument_key}
    response = requests.get(url, params=params, headers=headers, timeout=10)
    data = response.json()
    if data.get("status") == "success" and data.get("data"):
        return int(data["data"][0].get("lot_size", 1))
    return 1

def get_margin(headers: dict, legs: list) -> float:
    url = "https://api.upstox.com/v2/charges/margin"
    payload = {"instruments": legs}
    margin_headers = headers.copy()
    margin_headers["Content-Type"] = "application/json"
    
    response = requests.post(url, headers=margin_headers, data=json.dumps(payload), timeout=10)
    if response.status_code == 200:
        data = response.json()
        if data.get("status") == "success":
            return data.get("data", {}).get("margins", [{}])[0].get("total_margin", 0.0)
    return 0.0

def run_scan(scan: dict, config_path: Path) -> str:
    scan_name = scan.get("name", "Options Spread Scan")
    expiry_date = scan.get("expiry_date")
    default_strike_step = scan.get("strike_step", 20)
    
    if not expiry_date:
        raise ValueError(f"Scan '{scan_name}': must define 'expiry_date' in config.")

    # Token Loading
    access_token = os.getenv("UPSTOX_TOKEN", "").strip()
    token_path = Path(scan.get("token_path", "upstox_token.txt")).expanduser()
    if not token_path.is_absolute():
        token_path = config_path.parent / token_path
    if not access_token and token_path.is_file():
        access_token = token_path.read_text(encoding="utf-8").strip()
    if not access_token:
        raise ValueError(f"Scan '{scan_name}': configure UPSTOX_TOKEN or provide token file.")

    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}", "Api-Version": "2.0"}

    # CSV Loading
    csv_path = Path(scan["csv_path"]).expanduser()
    if not csv_path.is_absolute():
        csv_path = config_path.parent / csv_path
    df_csv = pd.read_csv(csv_path)
    
    key_col = scan.get("instrument_key_column", df_csv.columns[0])
    stock_col = scan.get("stock_column")
    strike_step_col = scan.get("strike_step_column") # Optional: pull unique strike steps from CSV

    key_to_stock = {}
    key_to_step = {}
    for _, row in df_csv.iterrows():
        instrument_value = row.get(key_col)
        if pd.isna(instrument_value) or not str(instrument_value).strip():
            continue
        instrument_key = str(instrument_value).strip()
        stock_value = row.get(stock_col) if stock_col else None
        display_name = str(stock_value).strip() if pd.notna(stock_value) and str(stock_value).strip() else instrument_key
        
        key_to_stock[instrument_key] = display_name
        key_to_step[instrument_key] = float(row.get(strike_step_col)) if strike_step_col and pd.notna(row.get(strike_step_col)) else default_strike_step

    instrument_keys = list(dict.fromkeys(key_to_stock.keys()))
    if not instrument_keys:
        raise ValueError(f"Scan '{scan_name}': no instrument keys found in {csv_path}")

    log.info(f"Starting Scan '{scan_name}' for {len(instrument_keys)} instruments (Expiry: {expiry_date})")
    
    final_summary = f"\n{'='*50}\n{' '*12}{scan_name.upper()} SUMMARY\n{'='*50}\n"

    for key in instrument_keys:
        display_name = key_to_stock[key]
        strike_step = key_to_step[key]
        
        try:
            spot_price = get_spot_price(key, headers)
            if spot_price == 0:
                log.warning(f"Failed to fetch spot for {display_name}. Skipping.")
                continue
                
            lot_size = get_lot_size(key, headers)
            chain_data = get_option_chain(key, expiry_date, headers)

            if not chain_data:
                log.warning(f"No chain data found for {display_name}. Skipping.")
                continue

            # --- NEW: Dynamically determine the strike step ---
            strikes = sorted(list(set(item.get("strike_price") for item in chain_data if item.get("strike_price") is not None)))
            if len(strikes) >= 2:
                dynamic_strike_step = strikes[1] - strikes[0]
            else:
                dynamic_strike_step = strike_step # Fallback to config default
                
            # Use the dynamic step to calculate legs
            atm_strike = math.floor(spot_price / dynamic_strike_step) * dynamic_strike_step
            otm_call = atm_strike + dynamic_strike_step
            otm_put = atm_strike - dynamic_strike_step

            atm_ce_ltp, atm_ce_key, otm_ce_ltp, otm_ce_key = None, None, None, None
            atm_pe_ltp, atm_pe_key, otm_pe_ltp, otm_pe_key = None, None, None, None

            for item in chain_data:
                strike = item.get("strike_price")
                if strike == atm_strike and "call_options" in item:
                    atm_ce_ltp = item["call_options"]["market_data"]["ltp"]
                    atm_ce_key = item["call_options"]["instrument_key"]
                elif strike == otm_call and "call_options" in item:
                    otm_ce_ltp = item["call_options"]["market_data"]["ltp"]
                    otm_ce_key = item["call_options"]["instrument_key"]
                    
                if strike == atm_strike and "put_options" in item:
                    atm_pe_ltp = item["put_options"]["market_data"]["ltp"]
                    atm_pe_key = item["put_options"]["instrument_key"]
                elif strike == otm_put and "put_options" in item:
                    otm_pe_ltp = item["put_options"]["market_data"]["ltp"]
                    otm_pe_key = item["put_options"]["instrument_key"]

            if None in (atm_ce_ltp, otm_ce_ltp, atm_pe_ltp, otm_pe_ltp):
                log.warning(f"Incomplete chain data for {display_name} (Step used: {dynamic_strike_step}). Skipping.")
                continue

            def make_leg(key_id, txn):
                return {"instrument_key": key_id, "quantity": lot_size, "product": "D", "transaction_type": txn}

            # 1. Bull Call Spread
            buc_combined_margin = get_margin(headers, [make_leg(atm_ce_key, "BUY"), make_leg(otm_ce_key, "SELL")])
            buc_risk = (atm_ce_ltp - otm_ce_ltp) * lot_size
            buc_reward = (dynamic_strike_step - (atm_ce_ltp - otm_ce_ltp)) * lot_size
            buc_rr = buc_reward / buc_risk if buc_risk > 0 else 0

            # 2. Bear Put Spread
            bp_combined_margin = get_margin(headers, [make_leg(atm_pe_key, "BUY"), make_leg(otm_pe_key, "SELL")])
            bp_risk = (atm_pe_ltp - otm_pe_ltp) * lot_size
            bp_reward = (dynamic_strike_step - (atm_pe_ltp - otm_pe_ltp)) * lot_size
            bp_rr = bp_reward / bp_risk if bp_risk > 0 else 0
            
            
# BULL CALL SPREAD (Debit)
# Long Leg:  1x {atm_strike} CE @ ₹{atm_ce_ltp:.2f}
# Short Leg: 1x {otm_call} CE @ ₹{otm_ce_ltp:.2f}
# Reward/Risk: {buc_rr:.2f}
# Combined Final Margin Needed: ₹{buc_combined_margin:,.2f}
            stock_summary= f"""
>>> {display_name} (Spot: {spot_price}) <<<



BEAR PUT SPREAD (Debit)
Long Leg:  1x {atm_strike} PE @ ₹{atm_pe_ltp:.2f}
Short Leg: 1x {otm_put} PE @ ₹{otm_pe_ltp:.2f}
Reward/Risk: {bp_rr:.2f}
Combined Final Margin Needed: ₹{bp_combined_margin:,.2f}
{'-'*50}
"""
            log.info(stock_summary)
            final_summary += stock_summary
        except Exception as e:
            log.exception(f"Error processing {display_name}: {e}")

    print(final_summary)
    return final_summary

def main():
    parser = argparse.ArgumentParser(description="Run configured Options Scans.")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config_options_daily.yaml")
    args = parser.parse_args()
    from services.run_scans import run_scans
    # This assumes run_scans is updated to route 'upstox_options' provider to this script
    run_scans(args.config, provider_filter="upstox_options")

if __name__ == "__main__":
    main()