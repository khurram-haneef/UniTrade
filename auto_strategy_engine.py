"""State and execution rules for independently managed auto strategies.

The Streamlit interface owns the configuration wizard.  This small engine owns
the runtime state so each configured strategy can be evaluated independently.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


@dataclass
class StrategyRuntime:
    """Runtime state for one symbol/timeframe strategy."""
    strategy_id: str
    is_open: bool = False
    is_enabled: bool = True
    status: str = "Waiting for fresh signal"
    last_signal: Optional[str] = None


class AutoStrategyEngine:
    """Enforces the one-position, no-stale-signal trading rules.

    `process_signal` deliberately accepts an order callback.  The UI can use
    the existing futures or forex order engine without duplicating their
    exchange-specific order logic here.
    """

    def __init__(self):
        self.is_running = False
        self._strategies: Dict[str, StrategyRuntime] = {}

    def load_configuration(self, configuration: dict) -> None:
        self._strategies = {
            item["id"]: StrategyRuntime(
                strategy_id=item["id"], is_enabled=item.get("is_active", True)
            )
            for item in configuration.get("strategies", [])
        }

    def start(self, configuration: dict) -> None:
        self.load_configuration(configuration)
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False
        for runtime in self._strategies.values():
            runtime.status = "Stopped"

    def mark_position_closed(self, strategy_id: str) -> None:
        """Release a strategy only after its SL/TP or position-close event."""
        runtime = self._strategies.get(strategy_id)
        if runtime:
            runtime.is_open = False
            runtime.last_signal = None
            runtime.status = "Waiting for fresh signal"

    def set_strategy_enabled(self, strategy_id: str, enabled: bool) -> None:
        runtime = self._strategies.get(strategy_id)
        if runtime:
            runtime.is_enabled = enabled
            runtime.status = "Waiting for fresh signal" if enabled else "Disabled by user"

    def process_signal(self, strategy_id: str, signal: str,
                       place_order: Callable[[], dict]) -> dict:
        """Submit one fresh BUY/SELL signal only when this strategy is flat."""
        runtime = self._strategies.get(strategy_id)
        if not self.is_running or runtime is None:
            return {"status": False, "message": "Strategy is not running."}
        if not runtime.is_enabled:
            return {"status": False, "message": "Strategy is disabled."}
        if signal not in {"BUY", "SELL"}:
            runtime.status = "Waiting for fresh signal"
            return {"status": False, "message": "No actionable signal."}
        if runtime.is_open:
            # Intentionally discard the signal: it must not be held for later.
            runtime.status = "Position open — stale signals discarded"
            return {"status": False, "message": "Position already open."}

        result = place_order()
        if result.get("status"):
            runtime.is_open = True
            runtime.last_signal = signal
            runtime.status = "Position open — waiting for SL/TP"
        return result

    def status_for(self, strategy_id: str) -> str:
        runtime = self._strategies.get(strategy_id)
        return runtime.status if runtime else "Not initialized"
