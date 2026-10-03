import pandas as pd


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

    working_df = df.copy()

    periods = [20, 50, 100, 200]
    minimum_rows = max(max(periods) + 1, volume_lookback + 1)
    if interval == 'daily':
        minimum_rows = max(minimum_rows, 250)
    if len(working_df) < minimum_rows:
        return False, None

    for period in periods:
        working_df[f'EMA_{period}'] = working_df['Close'].ewm(span=period, adjust=False).mean()

    latest = working_df.iloc[-1]
    previous = working_df.iloc[-2]
    slope_positive = all(latest[f'EMA_{period}'] > previous[f'EMA_{period}'] for period in periods)
    ema_stacked = all(
        latest[f'EMA_{short_period}'] > latest[f'EMA_{long_period}']
        for short_period, long_period in zip(periods, periods[1:])
    )
    fastest_ema = periods[0]
    price_near_ema = latest['Close'] >= latest[f'EMA_{fastest_ema}'] * (1 - ema_proximity_pct / 100)
    average_volume = working_df['Volume'].iloc[-volume_lookback:].mean()
    volume_condition = average_volume >= min_average_volume
    price_increase_pct = (latest['Close'] - previous['Close']) / previous['Close'] * 100
    increase_condition = price_increase_pct >= min_price_increase_pct

    if not (slope_positive and ema_stacked and price_near_ema and volume_condition and increase_condition):
        return False, None

    if interval != 'daily':
        return True, latest['Close']

    df_weekly = working_df.resample('W-FRI', on='Date').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
    }).dropna()
    df_monthly = working_df.resample('ME', on='Date').agg({
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



def check_dynamic_price_volume_spike(
    df,
    lookback=50,
    price_multiplier=2.2,
    volume_multiplier=2.2,
    interval='daily',
):
    """
    Evaluates if the latest candle's price movement and volume exceed the
    average of the past N candles by a configurable multiplier.
    The condition is evaluated on the latest candle for every interval.
    """
    if df is None or df.empty:
        return False, None

    working_df = df.copy()
    if 'Close' not in working_df.columns or 'Volume' not in working_df.columns:
        return False, None
    if len(working_df) < lookback + 2:
        return False, None

    def _evaluate_at_index(index):
        if index <= 0:
            return False, None

        prev_close = working_df['Close'].iloc[index - 1]
        if pd.isna(prev_close) or prev_close == 0:
            return False, None

        current_close = working_df['Close'].iloc[index]
        current_volume = working_df['Volume'].iloc[index]
        current_move = abs((current_close - prev_close) / prev_close)

        start_index = max(0, index - lookback)
        hist = working_df.iloc[start_index:index]
        if hist.empty:
            return False, None

        prev_closes = hist['Close']
        prev_close_series = prev_closes.shift(1).iloc[1:]
        current_closes = prev_closes.iloc[1:]
        price_moves = (current_closes - prev_close_series).abs() / prev_close_series.replace(0, pd.NA)
        avg_price_movement = price_moves.mean()
        avg_volume = hist['Volume'].mean()

        price_spike_cond = current_move >= (price_multiplier * avg_price_movement)
        volume_spike_cond = current_volume >= (volume_multiplier * avg_volume)
        if price_spike_cond and volume_spike_cond:
            return True, current_close
        return False, None

    matched, price = _evaluate_at_index(len(working_df) - 1)
    return (matched, price) if matched else (False, None)


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

    working_df = df.copy()

    # 1. Calculate EMAs on daily data
    for period in [20, 50, 100, 200]:
        working_df[f'EMA_{period}'] = working_df['Close'].ewm(
            span=period,
            adjust=False
        ).mean()

    latest = working_df.iloc[-1]
    prev = working_df.iloc[-2]

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

def check_ema20_bearish_rejection(
    df,
    proximity_threshold=0.015,
    interval='daily',
):
    """
    Evaluates if EMAs (20, 50, 100) are in a downtrend (sloping down and stacked
    100 > 50 > 20). Then checks if the current candle rejected the 20 EMA by either:
    1. Opening above and closing below the 20 EMA.
    2. Opening and closing below the 20 EMA, being a red candle, with the Open
       very close to the 20 EMA.

    The condition is evaluated on the latest candle for every interval.
    """
    if df is None or df.empty:
        return False, None

    working_df = df.copy()
    if len(working_df) < 100:
        return False, None

    def _evaluate_at_index(index):
        if index <= 0:
            return False, None

        price_window = working_df.iloc[: index + 1].copy()
        for period in [20, 50, 100]:
            price_window[f'EMA_{period}'] = price_window['Close'].ewm(span=period, adjust=False).mean()

        latest = price_window.iloc[-1]
        prev = price_window.iloc[-2]

        slopes_negative = (
            latest['EMA_20'] < prev['EMA_20'] and
            latest['EMA_50'] < prev['EMA_50'] and
            latest['EMA_100'] < prev['EMA_100']
        )
        bearish_stack = (latest['EMA_100'] > latest['EMA_50'] > latest['EMA_20'])

        if not (slopes_negative and bearish_stack):
            return False, None

        open_price = latest['Open']
        close_price = latest['Close']
        ema_20 = latest['EMA_20']

        crossed_down = (open_price > ema_20) and (close_price < ema_20)
        both_below = (open_price < ema_20) and (close_price < ema_20)
        is_red_candle = close_price < open_price
        pct_diff_open = abs(ema_20 - open_price) / ema_20
        open_is_close = pct_diff_open <= proximity_threshold
        rejected_from_below = both_below and is_red_candle and open_is_close

        if crossed_down or rejected_from_below:
            return True, close_price
        return False, None

    matched, price = _evaluate_at_index(len(working_df) - 1)
    return (matched, price) if matched else (False, None)
