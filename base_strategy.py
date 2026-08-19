from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    def __init__(self, name, symbol, timeframe="1h"):
        self.name = name
        self.symbol = symbol
        self.timeframe = timeframe
        self.is_running = False

    @abstractmethod
    def generate_signal(self, dataframe):
        """
        Processes market candles (OHLCV) dataframe and generates trading signals.
        Must return: 'BUY', 'SELL', or 'HOLD'
        """
        pass

    def start_strategy(self):
        """Activates strategy execution loop."""
        self.is_running = True
        return f"Strategy {self.name} STARTED on {self.symbol} ({self.timeframe})"

    def stop_strategy(self):
        """Deactivates strategy execution loop."""
        self.is_running = False
        return f"Strategy {self.name} STOPPED."