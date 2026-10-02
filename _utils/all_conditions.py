def check_above_ema_20(
    df,
    interval='daily',
    min_price_increase_pct=3,
    min_average_volume=500000,
    volume_lookback=70,
    ema_proximity_pct=1.5,
    min_previous_month_gain_pct=8,
):
    """Check EMA trend, price increase, and average volume on the input candle interval."""
    if df is None or df.empty:
        return False, None
    if volume_lookback <= 0:
        raise ValueError("volume_lookback must be positive")

    periods = [20, 50, 100, 200]
    minimum_rows = max(max(periods) + 1, volume_lookback + 1)
    if interval == 'daily':
        minimum_rows = max(minimum_rows, 250)
    if len(df) < minimum_rows:
        return False, None

    df = df.copy()
    for period in periods:
        df[f'EMA_{period}'] = df['Close'].ewm(span=period, adjust=False).mean()

    latest = df.iloc[-1]
    previous = df.iloc[-2]
    slope_positive = all(latest[f'EMA_{period}'] > previous[f'EMA_{period}'] for period in periods)
    ema_stacked = all(
        latest[f'EMA_{short_period}'] > latest[f'EMA_{long_period}']
        for short_period, long_period in zip(periods, periods[1:])
    )
    fastest_ema = periods[0]
    price_near_ema = latest['Close'] >= latest[f'EMA_{fastest_ema}'] * (1 - ema_proximity_pct / 100)
    average_volume = df['Volume'].iloc[-volume_lookback:].mean()
    volume_condition = average_volume >= min_average_volume
    price_increase_pct = (latest['Close'] - previous['Close']) / previous['Close'] * 100
    increase_condition = price_increase_pct >= min_price_increase_pct

    if not (slope_positive and ema_stacked and price_near_ema and volume_condition and increase_condition):
        return False, None

    if interval != 'daily':
        return True, latest['Close']

    df_weekly = df.resample('W-FRI', on='Date').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
    }).dropna()
    df_monthly = df.resample('ME', on='Date').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
    }).dropna()
    if len(df_weekly) < 2 or len(df_monthly) < 2:
        return False, None

    last_week = df_weekly.iloc[-2]
    last_month = df_monthly.iloc[-2]
    week_positive = last_week['Close'] > last_week['Open']
    month_positive = last_month['Close'] > last_month['Open']
    month_pct_gain = (last_month['Close'] - last_month['Open']) / last_month['Open'] * 100
    if week_positive and month_positive and month_pct_gain >= min_previous_month_gain_pct:
        return True, latest['Close']
    return False, None



def check_volume_price_spike(
    df,
    min_price_increase_pct=6,
    volume_multiplier=2,
    volume_lookback=44,
):
    """Check whether the latest candle's price gain and volume exceed configured thresholds."""
    if df is None or len(df) < volume_lookback + 1:
        return False, None

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price_increase_pct = (latest['Close'] - prev['Close']) / prev['Close'] * 100
    price_spike_cond = price_increase_pct > min_price_increase_pct

    average_volume = df['Volume'].iloc[-volume_lookback - 1:-1].mean()
    volume_spike_cond = latest['Volume'] >= volume_multiplier * average_volume

    if price_spike_cond and volume_spike_cond:
        return True, latest['Close']
        
    return False, None


def check_ema_touch_downtrend(df, proximity_threshold=0.015):
    """
    Evaluates if EMA 100 and 200 slopes are negative, and if today's price
    is touching or very close to the 20, 50, 100, or 200 EMA.

    Parameters:
    df (pd.DataFrame): Dataframe containing 'High', 'Low', and 'Close' columns.
    proximity_threshold (float): Max percentage difference to be considered
    "very close" (default 1.5%).
    """
    if df is None or len(df) < 200:
        return False, None

    df = df.copy()

    # 1. Calculate EMAs on daily data
    for period in [20, 50, 100, 200]:
        df[f'EMA_{period}'] = df['Close'].ewm(
            span=period,
            adjust=False
        ).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # Condition 1: EMA 100 and EMA 200 slopes are negative
    slope_negative = (
        latest['EMA_100'] < prev['EMA_100'] and
        latest['EMA_200'] < prev['EMA_200']
    )

    if not slope_negative:
        return False, None

    # Condition 2: Price is touching or very close to any EMA
    is_near_ema = False

    for period in [20, 50, 100, 200]:
        ema_val = latest[f'EMA_{period}']

        # Candle physically touches EMA
        touching = latest['Low'] <= ema_val <= latest['High']

        # Closing price is within proximity threshold
        pct_diff = abs(latest['Close'] - ema_val) / ema_val
        very_close = pct_diff <= proximity_threshold

        if touching or very_close:
            is_near_ema = True
            break

    if is_near_ema:
        return True, latest['Close']

    return False, None