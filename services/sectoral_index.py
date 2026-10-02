# import pandas as pd
# from _utils.logger_config import *
# from _utils.calculate_indicators import *
# from services.call_accumilation import *
# from services.temp_strategy import *
# from _utils.accumilation_usa import *
# from _utils.entry import *
# import sys
# setup_logging()
# logging.getLogger('yfinance').setLevel(logging.CRITICAL)
# log = logging.getLogger(__name__)

# pd.set_option('display.max_rows', None)
# pd.set_option('display.max_columns', None)

# # tickers = ["^CNXAUTO","^NSEBANK","^CNXENERGY","^CNXFIN","^CNXFMCG","^CNXIT","^CNXMEDIA","^CNXMETAL","^CNXPHARMA","^CNXPSUBANK","^CNXREALTY","^CNXCONSUM"]

# tickers = ["^CNXAUTO"]

# nifty="^NSEI"

# for i in range(len(tickers)):
#     ticker = tickers[i]

#     df=fetch_prices(tickers[i],'2026-07-10','2026-07-18','1d')

#     pc_gain=df['Close'].iloc[-1]-df['Open'].iloc[0]

#     log.info(df)

#     pc_gain=pc_gain/df['Open'].iloc[0]*100

#     log.info(f'{ticker} is {pc_gain}')












import os
import requests

base_url = "https://cascade.ibex.bg/"
username = os.environ.get("SECTORAL_INDEX_USERNAME")
password = os.environ.get("SECTORAL_INDEX_PASSWORD")

