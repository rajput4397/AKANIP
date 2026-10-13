import os
import sys
import logging
from pathlib import Path
import requests
import json

# Root directory of AKANIP project
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from _utils.logger_config import setup_logging

setup_logging(filename="options_spread.log")

root_logger = logging.getLogger()
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
root_logger.addHandler(console_handler)
log = logging.getLogger("OptionsSpread")

# --- Configuration ---
TOKEN_FILE = PROJECT_ROOT / "upstox_token.txt"
INSTRUMENT_KEY = "NSE_EQ|INE002A01018"  
EXPIRY_DATE = "2026-10-27"             
STRIKE_STEP = 20                       


def load_access_token() -> str:
    env_token = os.getenv("UPSTOX_TOKEN", "").strip()
    if env_token:
        return env_token
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    return token

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


def calculate_spreads(instrument_key=INSTRUMENT_KEY, expiry_date=EXPIRY_DATE, strike_step=STRIKE_STEP):
    try:
        access_token = load_access_token()
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}", "Api-Version": "2.0"}

        spot_price = get_spot_price(instrument_key, headers)
        lot_size = get_lot_size(instrument_key, headers)
        
        atm_strike = round(spot_price / strike_step) * strike_step
        otm_call = atm_strike + strike_step
        otm_put = atm_strike - strike_step

        chain_data = get_option_chain(instrument_key, expiry_date, headers)

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
            raise ValueError("Could not find LTP data for all required target strikes.")

        def make_leg(key, txn):
            return {"instrument_key": key, "quantity": lot_size, "product": "D", "transaction_type": txn}

        # 1. Bull Call Spread (Debit)
        buc_leg1 = make_leg(atm_ce_key, "BUY")
        buc_leg2 = make_leg(otm_ce_key, "SELL")
        buc_combined_margin = get_margin(headers, [buc_leg1, buc_leg2])
        
        buc_risk = atm_ce_ltp - otm_ce_ltp
        buc_reward = strike_step - buc_risk
        buc_rr = buc_reward / buc_risk if buc_risk > 0 else 0

        # 2. Bear Put Spread (Debit)
        bp_leg1 = make_leg(atm_pe_key, "BUY")
        bp_leg2 = make_leg(otm_pe_key, "SELL")
        bp_combined_margin = get_margin(headers, [bp_leg1, bp_leg2])
        
        bp_risk = atm_pe_ltp - otm_pe_ltp
        bp_reward = strike_step - bp_risk
        bp_rr = bp_reward / bp_risk if bp_risk > 0 else 0

        summary_text = f"""
==================================================
                 BULL CALL SPREAD (Debit)
==================================================
Long Leg:  1x {atm_strike} CE @ ₹{atm_ce_ltp:.2f}
Short Leg: 1x {otm_call} CE @ ₹{otm_ce_ltp:.2f}
Reward/Risk: {buc_rr:.2f}
Combined Final Margin Needed: ₹{buc_combined_margin:,.2f}

==================================================
                 BEAR PUT SPREAD (Debit)
==================================================
Long Leg:  1x {atm_strike} PE @ ₹{atm_pe_ltp:.2f}
Short Leg: 1x {otm_put} PE @ ₹{otm_pe_ltp:.2f}
Reward/Risk: {bp_rr:.2f}
Combined Final Margin Needed: ₹{bp_combined_margin:,.2f}
==================================================
"""
        print(summary_text)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    calculate_spreads()