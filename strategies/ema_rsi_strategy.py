import pandas as pd
import pandas_ta as ta
from base_strategy import BaseStrategy

class EmaRsiCrossStrategy(BaseStrategy):
    def __init__(self, name="EMA_RSI_Cross", symbol="BTC/USDT", timeframe="5m", 
                 mode="fixed", fast_ema=None, slow_ema=None, use_rsi_filter=False, rsi_period=14):
        super().__init__(name=name, symbol=symbol, timeframe=timeframe)
        self.mode = mode.lower()
        self.use_rsi_filter = use_rsi_filter
        self.rsi_period = rsi_period
        
        # Configure EMAs based on Mode or Custom Selection
        if self.mode == "fixed":
            if timeframe == "1m":
                self.fast_ema_len = 20
                self.slow_ema_len = 200
            elif timeframe == "15m":
                self.fast_ema_len = 15
                self.slow_ema_len = 600
            else:  # Default 5m or fallback
                self.fast_ema_len = 180
                self.slow_ema_len = 600
        else:
            # Custom Parameters Selected by User
            self.fast_ema_len = fast_ema if fast_ema else 20
            self.slow_ema_len = slow_ema if slow_ema else 200

    def generate_signal(self, dataframe: pd.DataFrame) -> str:
        """
        Calculates EMA Crossover and optional RSI Filter.
        Returns: 'BUY', 'SELL', or 'HOLD'
        """
        if dataframe is None or len(dataframe) < max(self.slow_ema_len, self.rsi_period) + 2:
            return 'HOLD'

        df = dataframe.copy()

        # Calculate EMAs
        df['ema_fast'] = ta.ema(df['close'], length=self.fast_ema_len)
        df['ema_slow'] = ta.ema(df['close'], length=self.slow_ema_len)

        # Calculate RSI if Filter is Enabled
        if self.use_rsi_filter:
            df['rsi'] = ta.rsi(df['close'], length=self.rsi_period)

        # The newest OHLCV row is usually the candle still being formed.  Use
        # the two preceding, closed candles so a signal cannot disappear on
        # the next price tick.
        curr_fast = df['ema_fast'].iloc[-2]
        prev_fast = df['ema_fast'].iloc[-3]
        curr_slow = df['ema_slow'].iloc[-2]
        prev_slow = df['ema_slow'].iloc[-3]

        # Bullish and Bearish Crosses
        golden_cross = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
        death_cross = (prev_fast >= prev_slow) and (curr_fast < curr_slow)

        # Apply RSI Filter Rules
        if self.use_rsi_filter:
            curr_rsi = df['rsi'].iloc[-2]
            if golden_cross and curr_rsi >= 50:
                return 'BUY'
            elif death_cross and curr_rsi <= 50:
                return 'SELL'
        else:
            if golden_cross:
                return 'BUY'
            elif death_cross:
                return 'SELL'

        return 'HOLD'
