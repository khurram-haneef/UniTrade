# UniTrade Global Configuration Settings

APP_NAME = "UniTrade Terminal"
APP_VERSION = "1.0.0"

# Default Futures Settings
DEFAULT_LEVERAGE = 20
DEFAULT_MARGIN_MODE = "isolated"  # 'isolated' or 'cross'

# MEXC Specific API Codes Mapping
MEXC_OPEN_TYPE = {
    "isolated": 1,
    "cross": 2
}

MEXC_POSITION_TYPE = {
    "long": 1,
    "short": 2
}