"""Persistent server-side execution worker for UniTrade strategies.

Run this process independently from Streamlit.  The Streamlit dashboard only
changes the persisted ON/OFF state; this worker keeps evaluating strategies
after a browser disconnects.  It must run on the same trusted server as the
database and must receive the same APP_ENCRYPTION_KEY.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv

from auth_store import UserStore
from exchange_manager import ExchangeManager
from forex_engine import FOREX_SYMBOLS, ForexEngine
from order_engine import OrderEngine
from strategies.ema_rsi_strategy import EmaRsiCrossStrategy
from wallet_engine import WalletEngine

POLL_SECONDS = max(15, int(os.getenv("BOT_POLL_SECONDS", "30")))
LOG = logging.getLogger("unitrade.worker")


def _database_path() -> str:
    return os.getenv("DATABASE_URL") or os.getenv("UNITRADE_DB_PATH") or os.path.join(os.path.dirname(__file__), "data", "unitrade_users.db")


def _closed_candle_frame(rows: list[list]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if len(frame) < 3:
        raise ValueError("Exchange returned too few candles.")
    return frame


def _risk_prices(strategy: dict, frame: pd.DataFrame, price: float, signal: str) -> tuple[float | None, float | None]:
    """Calculate a protective SL/TP from the risk controls stored in the UI."""
    sl_value = float(strategy.get("sl_value", 0) or 0)
    tp_value = float(strategy.get("tp_value", 0) or 0)
    if sl_value <= 0 or tp_value <= 0:
        return None, None
    if strategy.get("sl_type", "ATR") == "Ratio":
        sl_distance, tp_distance = price * sl_value / 100, price * tp_value / 100
    else:
        atr = ta.atr(frame["high"], frame["low"], frame["close"], length=14).iloc[-2]
        if pd.isna(atr) or atr <= 0:
            return None, None
        sl_distance, tp_distance = float(atr) * sl_value, float(atr) * tp_value
    if signal == "BUY":
        return price - sl_distance, price + tp_distance
    return price + sl_distance, price - tp_distance


class BotWorker:
    def __init__(self, store: UserStore):
        self.store = store

    def run_once(self) -> None:
        for bot in self.store.running_bot_configurations():
            try:
                self._run_user(bot["user_id"], bot["config"])
            except Exception:
                LOG.exception("Unhandled worker error for user %s", bot["user_id"])

    def _run_user(self, user_id: int, config: dict) -> None:
        manager = ExchangeManager()
        for credential in self.store.load_exchange_credentials(user_id):
            result = manager.connect_exchange(
                credential["alias"], credential["exchange_id"], credential["api_key"],
                credential["api_secret"], credential.get("passphrase"),
            )
            if not result["status"]:
                LOG.warning("Could not connect %s for user %s: %s", credential["alias"], user_id, result["message"])

        order_engine = OrderEngine(manager)
        forex_engine = ForexEngine(manager)
        wallet_engine = WalletEngine(manager)
        exchanges = {item["exchange"]: item for item in config.get("exchanges", [])}
        for strategy in config.get("strategies", []):
            exchange_config = exchanges.get(strategy.get("exchange"))
            if not strategy.get("is_active", True) or not exchange_config:
                continue
            self._run_strategy(user_id, strategy, exchange_config, manager, order_engine, forex_engine, wallet_engine)

    def _run_strategy(self, user_id, strategy, exchange_config, manager, order_engine, forex_engine, wallet_engine) -> None:
        strategy_id = strategy["id"]
        account_alias = exchange_config.get("account_alias")
        if not account_alias or account_alias not in manager.connected_exchanges:
            self.store.save_runtime(user_id, strategy_id, is_open=False, last_candle_at=None, last_signal=None,
                                    status="Account is not connected")
            return
        exchange = manager.get_exchange_instance(account_alias)
        raw_asset = strategy["asset"]
        symbol = FOREX_SYMBOLS.get(raw_asset, raw_asset)
        candle_limit = max(int(strategy["slow_ema"]), 14) + 5
        try:
            rows = exchange.fetch_ohlcv(symbol, timeframe=strategy["timeframe"], limit=candle_limit)
            frame = _closed_candle_frame(rows)
        except Exception as exc:
            self.store.save_runtime(user_id, strategy_id, is_open=False, last_candle_at=None, last_signal=None,
                                    status=f"Candle fetch failed: {exc}")
            return

        # Never execute from the candle currently forming.  It can change or
        # vanish before close, which would create unreliable repeated trades.
        candle_at = int(frame["timestamp"].iloc[-2])
        runtime = self.store.runtime_for(user_id, strategy_id)
        if runtime["last_candle_at"] == candle_at:
            return

        signal_engine = EmaRsiCrossStrategy(
            symbol=symbol, timeframe=strategy["timeframe"], mode="custom",
            fast_ema=int(strategy["fast_ema"]), slow_ema=int(strategy["slow_ema"]),
            use_rsi_filter=bool(strategy.get("use_rsi")),
        )
        signal = signal_engine.generate_signal(frame)
        if signal not in {"BUY", "SELL"}:
            self.store.save_runtime(user_id, strategy_id, is_open=bool(runtime["is_open"]), last_candle_at=candle_at,
                                    last_signal=None, status="Waiting for fresh signal")
            return
        if runtime["is_open"]:
            self.store.save_runtime(user_id, strategy_id, is_open=True, last_candle_at=candle_at,
                                    last_signal=signal, status="Position open; signal ignored")
            return

        price = float(frame["close"].iloc[-2])
        sl, tp = _risk_prices(strategy, frame, price, signal)
        if sl is None or tp is None:
            self.store.save_runtime(user_id, strategy_id, is_open=False, last_candle_at=candle_at,
                                    last_signal=signal, status="Blocked: configure valid SL and TP")
            return
        margin = float(strategy.get("margin_value", 0) or 0)
        if strategy.get("margin_type") == "Wallet ratio":
            balance = wallet_engine.get_balances(account_alias, wallet_type="futures")
            margin = float(balance.get("free_usdt", 0)) * margin / 100 if balance.get("status") else 0
        if margin <= 0:
            self.store.save_runtime(user_id, strategy_id, is_open=False, last_candle_at=candle_at,
                                    last_signal=signal, status="Blocked: invalid margin amount")
            return

        side = "buy" if signal == "BUY" else "sell"
        leverage = int(strategy["leverage"])
        if raw_asset in FOREX_SYMBOLS:
            result = forex_engine.place_forex_order(
                account_alias, raw_asset, side, margin, exchange_config.get("margin_mode", "isolated"),
                leverage, "cost", price, sl, tp,
            )
        else:
            result = order_engine.place_futures_order(
                account_alias, raw_asset, side, margin, exchange_config.get("margin_mode", "isolated"),
                leverage, "cost", price, sl, tp,
            )
        self.store.save_runtime(
            user_id, strategy_id, is_open=bool(result.get("status")), last_candle_at=candle_at,
            last_signal=signal, status=("Position open; waiting for SL/TP" if result.get("status") else f"Order failed: {result.get('message', 'unknown error')}"),
        )


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    store = UserStore(_database_path(), os.getenv("APP_ENCRYPTION_KEY"))
    if store.cipher is None:
        raise RuntimeError("APP_ENCRYPTION_KEY must be set for the persistent bot worker.")
    worker = BotWorker(store)
    LOG.info("UniTrade worker started; polling every %s seconds", POLL_SECONDS)
    while True:
        worker.run_once()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
