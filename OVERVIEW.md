# AKANIP — Repository Overview

AKANIP is a stock/options screener for Indian (NSE) equities and US (Russell 3000)
equities. It scans instruments against a set of technical-indicator "strategies"
(EMA trend, volume/price spikes, bearish rejections, etc.) and sends match
summaries to Telegram via local Python scanners (`services/`, `_utils/`) that
fetch data from Upstox (NSE) or yfinance (US) and evaluate strategies with pandas.

A Cloudflare Worker reimplementation of this (JS port of the Upstox options
scans, running on Cloudflare Cron Triggers) was tried and removed: the options
scan needs ~294 Upstox subrequests per run (147 instruments × 2 requests each),
which exceeds the Workers Free plan's 50-subrequest-per-invocation limit, so
every scheduled run failed before it could send a Telegram alert. The repo
previously ran these same scans on GitHub Actions
(`.github/workflows/Options_Scan.yml` / `Options_Scan_15min.yml`, removed in
commit `ad09d0c`) before that Cloudflare experiment — that path has no
subrequest cap and is a plausible place to restore scheduled runs.

## Directory map

```
_utils/                   Python helpers: indicators, strategy functions, config loading
services/                 Python scan runners + Telegram alerting + misc scripts
config*.yaml               Scan definitions (which provider/CSV/strategy/interval)
*.csv                      Instrument universes (NSE equities, NSE options subset, Russell 3000, raw NSE codes)
requirements.txt           Python deps for the scanners
```

## Data flow

```
config.yaml / config_15min.yaml  --list of scans-->  services/run_scans.py
   each scan: {provider, csv_path, interval, strategies: [...]}
                      |
                      v
        provider == "upstox"            provider == "yfinance"
        services/upstock_api.py          services/yfinance.py
                      |                          |
          Upstox historical-candle API    yfinance.download()
          (+ optional intraday candle     (Open/High/Low/Close/Volume)
           appended as "today")
                      |                          |
                      +---------- per strategy -->
                      v
          _utils/all_conditions.py (check_* functions)
                      |
                      v
          services/telegram_alert.py  --> Telegram bot message
```

`services/run_scans.py` is the entry point: it reads a YAML config, dispatches
each scan to the matching provider runner (`upstock_api.run_scan` or
`yfinance.run_scan`), and sends a Telegram summary per scan plus one final
run summary. `_utils/scan_config.py` normalizes interval names
(`daily`/`weekly`/`15min`/`monthly`) and resolves each configured strategy
name to a function in `_utils/all_conditions.py`.

### Strategy functions (`_utils/all_conditions.py`)

All take a pandas DataFrame with `Date/Open/High/Low/Close/Volume` and return
`(matched: bool, price: float | None)`:

- `check_above_ema_20` — EMAs 20/50/100/200 all sloping up and stacked
  (20>50>100>200), price near the 20-EMA, volume above a floor, and a minimum
  day-over-day price increase; for daily data also requires the prior week and
  month to have closed green with a minimum monthly gain.
- `check_above_ema_20_stable_three_day_price` — same EMA/volume conditions but
  requires the close to be *stable* (small change) over the last 3 bars rather
  than spiking, plus a green prior week.
- `check_dynamic_price_volume_spike` — flags a bar whose price move and volume
  both exceed a multiplier of their trailing average (used for the options scans).
- `check_ema_touch_downtrend` — EMA 100/200 sloping down and price
  touching/near any of the 20/50/100/200 EMAs.
- `check_ema20_bearish_rejection` — EMAs 20/50/100 sloping down and stacked
  bearishly, and the latest candle either crosses down through or gets
  rejected from the 20-EMA.

`_utils/calculate_indicators.py` holds additional indicator helpers (Heikin
Ashi, SuperTrend, MACD, a yfinance price fetcher) that aren't currently wired
into any scan config but are available for future strategies. It also has
hardcoded Windows (`C:/Users/ADMIN/...`) parquet paths that only work on one
specific machine.

### Config files

- `config.yaml` — daily Russell 3000 scan (yfinance) via `check_above_ema_20`
  and `check_above_ema_20_stable_three_day_price`; an NSE/Upstox daily scan is
  present but commented out.
- `config_15min.yaml` — 15-minute Russell 3000 (yfinance) and NSE equities
  (Upstox) scans using `check_above_ema_20` and `check_volume_price_spike`.
  **Note:** `check_volume_price_spike` is not defined in
  `_utils/all_conditions.py` (only `check_dynamic_price_volume_spike` exists);
  `_utils/scan_config.py` just logs a warning and skips unknown strategy
  names, so this config currently only runs `check_above_ema_20` in practice.
- `config_options_daily.yaml` / `config_options_15min.yaml` — Upstox scans over
  `indian_options.csv` (147 instruments) using `check_dynamic_price_volume_spike`
  and `check_ema20_bearish_rejection`.

### CSV instrument lists

- `indian.csv` / `nse_codes.csv` — ~2,220 NSE equity instrument keys (`nse_codes.csv`
  looks like the raw source `indian.csv` was derived from, minus display names).
- `indian_options.csv` — a curated subset (147 instruments) used for the options scans.
- `russell3000.csv` — ~2,975 US tickers for the yfinance scans.

### Auth & secrets

- `services/upstock.py` is a one-off interactive script to complete Upstox's
  OAuth login flow and write the resulting access token to `upstox_token.txt`
  (gitignored). Needs `UPSTOX_API_KEY`/`UPSTOX_API_SECRET` env vars.
- `services/upstock_api.py` reads the token from `UPSTOX_TOKEN` env var first,
  falling back to `upstox_token.txt`.
- `services/telegram_alert.py` loads `BOT_TOKEN`/`CHAT_ID` from `.env` (see
  `.env.example`) and posts to the Telegram Bot API, with retry on timeout.
- `_utils/logger_config.py` sets up a single root file logger writing to
  `app3.log` at the repo root (gitignored).

### Loose ends / dead code

- `services/options.py` is an older, hardcoded (Windows path, `sys.exit` on
  missing files) standalone version of the Upstox daily scan, driven by
  `_utils/config.json` instead of the YAML configs. Superseded by
  `services/upstock_api.py` + `config_options_daily.yaml`, but still present.
- `services/sectoral_index.py` is mostly commented-out scratch code plus a
  few live lines reading `SECTORAL_INDEX_USERNAME`/`PASSWORD` env vars and a
  `base_url` that are never used — looks unfinished/abandoned.

## Running it

```sh
pip install -r requirements.txt
python -m services.run_scans --config config.yaml
```
(or `config_15min.yaml` / `config_options_daily.yaml` / `config_options_15min.yaml`)

To run on a schedule without a dedicated machine, a GitHub Actions workflow
(`on: schedule`, `workflow_dispatch`, `pip install -r requirements.txt`, then
`python -m services.run_scans --config <config>.yaml` with `UPSTOX_TOKEN`,
`BOT_TOKEN`, `CHAT_ID` passed as repo secrets) is the straightforward option —
this repo ran that way before the removed Cloudflare Worker experiment.
