"""
Amir Watchlist Screener V2 — Enhanced Configuration
Same universe/volume filters as V1, plus new scoring metrics:
  - Volume as % of Float
  - 52-week Range Position
  - SEC Offering Frequency Penalty
  - Short Interest data
  - Sector tags
  - Historical Support/Resistance levels
"""

# ============================================================
# UNIVERSE FILTERS (same as V1)
# ============================================================
MIN_PRICE = 0.50
MAX_MARKET_CAP = 500_000_000
MIN_MARKET_CAP = 500_000
MAX_FLOAT_SHARES = 50_000_000
MIN_FLOAT_SHARES = 500_000

# ============================================================
# VOLUME FILTERS (same as V1)
# ============================================================
VOLUME_AVG_PERIOD = 10
MIN_DAILY_VOLUME = 50_000
VOLUME_SPIKE_MULTIPLIER = 3.0
EXTREME_SPIKE_MULTIPLIER = 10.0
CONSECUTIVE_VOLUME_DAYS = 3

# ============================================================
# TECHNICAL INDICATORS (same as V1)
# ============================================================
SHORT_MA_PERIOD = 10
LONG_MA_PERIOD = 50

# ============================================================
# RISK / REWARD (same as V1)
# ============================================================
RISK_REWARD_RATIO = 5
MAX_DOWNSIDE_PCT = 0.10

# ============================================================
# OUTPUT (same as V1)
# ============================================================
MAX_CANDIDATES = 20
MIN_CANDIDATES = 5

# ============================================================
# DATA SOURCES (same as V1)
# ============================================================
FINNHUB_API_KEY = ""
SEC_EDGAR_USER_AGENT = "Amir Screener matthew@chaletteholdings.com"

# ============================================================
# V2 SCORING WEIGHTS (100 base, up to -15 penalty)
# ============================================================
# Volume Spike (reduced from 40 to 25 — was too aggressive in V1)
SCORE_VOLUME_SPIKE_MAX = 25

# Volume as % of Float (NEW — 15 pts)
# If today's volume is 10%+ of float, that's a massive signal
SCORE_VOL_FLOAT_MAX = 15
VOL_FLOAT_HIGH = 0.10       # 10% of float = max score
VOL_FLOAT_MID = 0.05        # 5% of float = mid score
VOL_FLOAT_LOW = 0.02        # 2% of float = min score

# Volume Trend (same as V1 — 20 pts)
SCORE_VOLUME_TREND_MAX = 20

# Low Float (same as V1 — 15 pts)
SCORE_LOW_FLOAT_MAX = 15

# VWAP Position (same as V1 — 10 pts)
SCORE_VWAP_MAX = 10

# 52-Week Range Position (NEW — 5 pts)
# Stocks in bottom 25% of 52w range with volume = more room to run
SCORE_RANGE_POS_MAX = 5
RANGE_POS_LOW_THRESHOLD = 0.25    # Bottom 25% of range
RANGE_POS_MID_THRESHOLD = 0.50    # Bottom 50% of range

# Small Cap (reduced from 10 to 5 — Amir cares more about float)
SCORE_SMALL_CAP_MAX = 5

# Momentum (same as V1 — 5 pts)
SCORE_MOMENTUM_MAX = 5

# Offering Frequency Penalty (NEW — up to -15 pts)
# Companies filing frequent S-1/S-3 offerings get penalized
OFFERING_PENALTY_MAX = 15
OFFERING_LOOKBACK_MONTHS = 12
OFFERING_PENALTY_THRESHOLD = 2   # 2+ offerings in 12 months = start penalizing

# ============================================================
# SUPPORT/RESISTANCE SETTINGS
# ============================================================
SUPPORT_RESISTANCE_LOOKBACK_DAYS = 120   # ~6 months of trading data
SUPPORT_RESISTANCE_NUM_LEVELS = 3        # Top 3 support and resistance levels

# ============================================================
# EMAIL DELIVERY (same credentials as V1)
# ============================================================
OUTPUT_DIR = "../watchlist-results-v2"
OUTPUT_FILE = "../watchlist-results-v2/watchlist_v2.html"
EMAIL_ENABLED = True
EMAIL_TO = "amiraviram@gmail.com"
EMAIL_CC = "matthew@chaletteholdings.com"
EMAIL_FROM = "matthewdavidov@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "matthewdavidov@gmail.com"
SMTP_PASS = "speh xunv pkbi mzuk"                       # Gmail App Password — paste your 16-char app password here (same as V1)
