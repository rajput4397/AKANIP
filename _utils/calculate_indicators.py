import yfinance as yf
import pandas as pd
import logging
import os
from _utils.logger_config import setup_logging
setup_logging()
log = logging.getLogger(__name__)



def fetch_prices(ticker, start, end, interval): 
    df = yf.download(
        ticker,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=False,
        progress=False,
    )

    if df.empty:
        return None
        # raise ValueError(f"No data fetched for ticker: {ticker}")

    # Handle MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")
    # log.info(df)
    return df



def check_growth_threshold(df, column_name='Close', threshold_pct=50,min_avg_vol=100000):
    """
    Checks if the growth from the first to the last row is >= threshold_pct.
    Formula: (Last - First) / First
    """
    # Ensure the dataframe isn't empty to avoid errors
    if df.empty:
        return False
    if df['Volume'].mean()<min_avg_vol:
        return False
    
    # Grab the 0th row and the last row using positional indexing (.iloc)
    first_val = df[column_name].iloc[0]
    last_val = df[column_name].iloc[-1]
    
    # Avoid division by zero if the first value is 0
    if first_val == 0:
        return False
        
    # Calculate the percentage change
    # (Last - First) / First
    change = (last_val - first_val) / first_val
    
    # Compare against the threshold (convert 50 to 0.50)
    return change >= (threshold_pct / 100)



def heikin_ashi(df):
    """
    Appends:
    ha_open, ha_high, ha_low, ha_close
    """
    ha_close = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4

    ha_open = [ (df["Open"].iat[0] + df["Close"].iat[0]) / 2 ]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + ha_close.iat[i-1]) / 2)

    ha_open = pd.Series(ha_open, index=df.index)

    ha_high = pd.concat(
        [df["High"], ha_open, ha_close], axis=1
    ).max(axis=1)

    ha_low = pd.concat(
        [df["Low"], ha_open, ha_close], axis=1
    ).min(axis=1)

    df["ha_open"] = ha_open
    df["ha_high"] = ha_high
    df["ha_low"] = ha_low
    df["ha_close"] = ha_close

    return df


def rma(series, period):
    """
    Wilder's Moving Average
    """
    return series.ewm(alpha=1/period, adjust=False).mean()


def true_range_ha(df):
    prev_close = df["ha_close"].shift(1)

    tr = pd.concat([
        df["ha_high"] - df["ha_low"],
        (df["ha_high"] - prev_close).abs(),
        (df["ha_low"] - prev_close).abs()
    ], axis=1).max(axis=1)

    return tr


def compute_supertrend(df, period=10, multiplier=3):
    """
    SuperTrend calculated ONLY using Heikin Ashi candles
    Appends:
    ATR, Final_UB, Final_LB, SuperTrend, ST_dir
    """

    # HA price source
    high = df["ha_high"]
    low = df["ha_low"]
    close = df["ha_close"]

    hl2 = (high + low) / 2
    tr = true_range_ha(df)
    atr = rma(tr, period)

    basic_ub = hl2 + multiplier * atr
    basic_lb = hl2 - multiplier * atr

    final_ub = basic_ub.copy()
    final_lb = basic_lb.copy()

    for i in range(1, len(df)):
        if (basic_ub.iat[i] < final_ub.iat[i-1]) or (close.iat[i-1] > final_ub.iat[i-1]):
            final_ub.iat[i] = basic_ub.iat[i]
        else:
            final_ub.iat[i] = final_ub.iat[i-1]

        if (basic_lb.iat[i] > final_lb.iat[i-1]) or (close.iat[i-1] < final_lb.iat[i-1]):
            final_lb.iat[i] = basic_lb.iat[i]
        else:
            final_lb.iat[i] = final_lb.iat[i-1]

    supertrend = pd.Series(index=df.index, dtype="float64")
    direction = pd.Series(index=df.index, dtype="int8")

    # init
    direction.iat[0] = 1
    supertrend.iat[0] = final_lb.iat[0]

    for i in range(1, len(df)):
        if close.iat[i] > final_ub.iat[i-1]:
            direction.iat[i] = 1
        elif close.iat[i] < final_lb.iat[i-1]:
            direction.iat[i] = -1
        else:
            direction.iat[i] = direction.iat[i-1]

        supertrend.iat[i] = final_lb.iat[i] if direction.iat[i] == 1 else final_ub.iat[i]

    df["ATR"] = atr
    df["Final_UB"] = final_ub
    df["Final_LB"] = final_lb
    df["SuperTrend"] = supertrend
    df["ST_dir"] = direction

    return df



def add_macd(df,price_col="Close",fast_period=20,slow_period=26,signal_period=9):
    """
    Appends MACD columns to the given DataFrame.
    """

    # EMA calculations
    df[f"EMA_{fast_period}"] = (
        df[price_col].ewm(span=fast_period, adjust=False).mean()
    )
    df[f"EMA_{slow_period}"] = (
        df[price_col].ewm(span=slow_period, adjust=False).mean()
    )

    # MACD line
    df[f"MACD_{fast_period}_{slow_period}"] = (
        df[f"EMA_{fast_period}"] - df[f"EMA_{slow_period}"]
    )

    # Signal line
    df[f"MACD_signal_{signal_period}"] = (
        df[f"MACD_{fast_period}_{slow_period}"]
        .ewm(span=signal_period, adjust=False)
        .mean()
    )

    # Histogram
    df[f"MACD_hist_{fast_period}_{slow_period}_{signal_period}"] = (
        df[f"MACD_{fast_period}_{slow_period}"]
        - df[f"MACD_signal_{signal_period}"]
    )

    return df


def read_parquet_safe(TICKER):
    path=f"C:/Users/ADMIN/Documents/Audacity/data_all_5/{TICKER}.parquet"
    if not os.path.exists(path):
        return None
    return pd.read_parquet(path)

def market_cap():
    path=f"C:/Users/ADMIN/Documents/Audacity/data_all/market_cap.parquet"
    return pd.read_parquet(path)

def nifty_data():
    path=f"C:/Users/ADMIN/Documents/Audacity/data_all_5/nifty.parquet"
    return pd.read_parquet(path)
