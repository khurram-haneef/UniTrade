import streamlit as st
import pandas as pd
import pandas_ta as ta
import os
import datetime
import copy
import pytz
import ccxt
import time
from dotenv import load_dotenv
from config import APP_NAME, APP_VERSION
from exchange_manager import ExchangeManager
from order_engine import OrderEngine
from wallet_engine import WalletEngine
from dry_run_engine import DryRunEngine
from forex_engine import ForexEngine
from auto_strategy_engine import AutoStrategyEngine
from auth_store import AuthError, UserStore
from email_service import EmailDeliveryError, SmtpEmailService

# --- IMPORT EMA RSI STRATEGY ---
try:
    from strategies.ema_rsi_strategy import EmaRsiCrossStrategy
except ImportError:
    EmaRsiCrossStrategy = None

# --- LOAD ENVIRONMENT VARIABLES (.env) ---
load_dotenv()

# --- 1. PAGE SETUP ---
st.set_page_config(
    page_title="UniTrade Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- ALL SUPPORTED EXCHANGES ---
SUPPORTED_EXCHANGES = [
    "mexc", "binance", "bybit", "okx", "bingx", 
    "bitmart", "yubit", "blofin", "gateio", "kraken", 
    "bitget", "kucoin"
]

supported_coins = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", 
    "DOGE/USDT", "SUI/USDT", "NEAR/USDT", "AVAX/USDT", "LINK/USDT",
    "ADA/USDT", "TON/USDT", "UNI/USDT", "AAVE/USDT", "APT/USDT", 
    "LTC/USDT", "TRX/USDT", "DOT/USDT", "OP/USDT", "INJ/USDT"
]

# --- 2. PREMIUM DARK THEME CSS ---
st.markdown("""
<style>
    .stApp {
        background-color: #0b0e14;
        color: #e1e7ec;
        font-family: 'Inter', sans-serif;
    }
    .top-header {
        background-color: #121722;
        padding: 12px 20px;
        border-radius: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border: 1px solid #1e2638;
        margin-bottom: 20px;
    }
    .brand-title {
        font-size: 1.3rem;
        font-weight: 800;
        letter-spacing: 1px;
        color: #ffffff;
    }
    .brand-title span {
        color: #00d2ff;
    }
    .mode-badge-live {
        background-color: rgba(14, 203, 129, 0.15);
        color: #0ecb81;
        border: 1px solid #0ecb81;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .mode-badge-demo {
        background-color: rgba(246, 70, 93, 0.15);
        color: #f6465d;
        border: 1px solid #f6465d;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .dashboard-card {
        background-color: #141b27;
        border: 1px solid #1f2a3d;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 15px;
    }
    .card-title {
        font-size: 1rem;
        font-weight: 700;
        color: #ffffff;
        margin-bottom: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .calc-box {
        background-color: #1a2332;
        border: 1px solid #00d2ff;
        border-radius: 6px;
        padding: 12px;
        margin-top: 10px;
        margin-bottom: 15px;
    }
    .calc-box-title {
        font-size: 0.85rem;
        font-weight: 700;
        color: #00d2ff;
        margin-bottom: 6px;
    }
    .calc-row {
        display: flex;
        justify-content: space-between;
        font-size: 0.82rem;
        color: #c5d0de;
        padding: 2px 0;
    }
    div.stButton > button {
        border-radius: 6px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stColumn"] button[kind="primary"] {
        background: linear-gradient(180deg, #0ecb81 0%, #0ba86b 100%) !important;
        color: white !important;
        border: none !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
        width: 100% !important;
        padding: 10px !important;
    }
    div[data-testid="stColumn"] button[kind="secondary"] {
        background: linear-gradient(180deg, #f6465d 0%, #d43147 100%) !important;
        color: white !important;
        border: none !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
        width: 100% !important;
        padding: 10px !important;
    }
</style>
""", unsafe_allow_html=True)

# --- UTILITY FUNCTIONS ---
def add_alert(msg, alert_type="info"):
    if 'alerts' not in st.session_state:
        st.session_state.alerts = []
    pkt_now = datetime.datetime.now(pytz.timezone("Asia/Karachi")).strftime("%I:%M:%S %p")
    st.session_state.alerts.insert(0, {"msg": msg, "time": pkt_now, "type": alert_type})
    if len(st.session_state.alerts) > 5:
        st.session_state.alerts.pop()


def _server_setting(name):
    """Read deployment secrets without ever displaying them in the UI."""
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return value or os.getenv(name)


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _deliver_otp(user_store, email_service, user_id):
    destination, code = user_store.issue_email_otp(user_id)
    email_service.send_verification_otp(destination, code)


def _render_auth_gate(user_store, email_service, require_email_verification):
    st.title("UniTrade Account Access")
    st.caption("Each account has separate strategies and encrypted exchange credentials.")
    tab_labels = ["Log in", "Create account"]
    if require_email_verification:
        tab_labels.append("Verify email")
    tabs = st.tabs(tab_labels)
    login_tab, signup_tab = tabs[:2]
    with login_tab:
        with st.form("account_login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Log in", type="primary", use_container_width=True):
                try:
                    st.session_state.auth_user = user_store.authenticate(
                        email, password, allow_unverified=not require_email_verification,
                    )
                    st.rerun()
                except AuthError as exc:
                    st.error(str(exc))
    with signup_tab:
        st.warning("Trading involves risk. You are responsible for your account, strategy, and API-key security.")
        st.info("Create exchange API keys with withdrawals disabled. Never share your API secret or passphrase.")
        with st.form("account_signup_form"):
            full_name = st.text_input("Full name", key="signup_full_name")
            username = st.text_input("Username (optional)", key="signup_username")
            mobile_number = st.text_input("Mobile number (optional)", key="signup_mobile")
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password (minimum 12 characters)", type="password", key="signup_password")
            confirm = st.text_input("Confirm password", type="password", key="signup_confirm")
            agreed = st.checkbox(
                "I accept the trading-risk agreement and confirm that exchange API withdrawals are disabled.",
                key="signup_agreement",
            )
            if st.form_submit_button("Create secure account", type="primary", use_container_width=True):
                if password != confirm:
                    st.error("Passwords do not match.")
                else:
                    try:
                        user = user_store.create_user(
                            email, password, agreed, full_name, username, mobile_number,
                            require_email_verification=require_email_verification,
                        )
                        if require_email_verification:
                            _deliver_otp(user_store, email_service, user["id"])
                            st.success("Account created. A verification code was sent to your email.")
                        else:
                            st.success("Account created. Email verification is disabled on this server; you can log in now.")
                    except EmailDeliveryError as exc:
                        try:
                            user_store.delete_user(user["id"], password)
                        except (AuthError, UnboundLocalError):
                            pass
                        st.error(f"Account was not created because verification email delivery failed: {exc}")
                    except AuthError as exc:
                        st.error(str(exc))
    if require_email_verification:
        verify_tab = tabs[2]
        with verify_tab:
            st.caption("Enter the 6-digit OTP sent to your email. A code expires after 15 minutes.")
            with st.form("verify_email_form"):
                verification_email = st.text_input("Email", key="verify_email")
                verification_code = st.text_input("Verification code", max_chars=6, key="verify_code")
                if st.form_submit_button("Verify email", type="primary", use_container_width=True):
                    try:
                        user_store.verify_email_otp(verification_email, verification_code)
                        st.success("Email verified. You can now log in.")
                    except AuthError as exc:
                        st.error(str(exc))
            with st.form("resend_otp_form"):
                resend_email = st.text_input("Email for a new code", key="resend_email")
                if st.form_submit_button("Resend verification code"):
                    try:
                        destination, code = user_store.resend_email_otp(resend_email)
                        email_service.send_verification_otp(destination, code)
                        st.success("A new verification code was sent.")
                    except (AuthError, EmailDeliveryError) as exc:
                        st.error(str(exc))


_default_database_path = os.path.join(os.path.dirname(__file__), "data", "unitrade_users.db")
_auth_database_path = _server_setting("DATABASE_URL") or _server_setting("UNITRADE_DB_PATH") or _default_database_path
_auth_store = UserStore(_auth_database_path, _server_setting("APP_ENCRYPTION_KEY"))
_email_service = SmtpEmailService(
    _server_setting("SMTP_HOST"), _server_setting("SMTP_PORT"), _server_setting("SMTP_USERNAME"),
    _server_setting("SMTP_PASSWORD"), _server_setting("SMTP_SENDER") or _server_setting("SMTP_FROM"), _server_setting("SMTP_USE_TLS"),
)
_require_email_verification = _as_bool(_server_setting("REQUIRE_EMAIL_VERIFICATION"), default=False)
if _require_email_verification and not _email_service.configured:
    _require_email_verification = False
if "auth_user" not in st.session_state:
    _render_auth_gate(_auth_store, _email_service, _require_email_verification)
    st.stop()

if _auth_database_path == _default_database_path:
    st.sidebar.warning("Local SQLite storage is active. Configure DATABASE_URL for PostgreSQL before deploying this app.")

# --- 3. SESSION INITIALIZATION ---
if 'alerts' not in st.session_state:
    st.session_state.alerts = []

if 'exchange_mgr' not in st.session_state:
    st.session_state.exchange_mgr = ExchangeManager()
    # Never load shared .env API keys into a multi-user session.
    for credential in _auth_store.load_exchange_credentials(st.session_state.auth_user["id"]):
        st.session_state.exchange_mgr.connect_exchange(
            credential["alias"], credential["exchange_id"], credential["api_key"],
            credential["api_secret"], credential.get("passphrase"),
        )

if 'order_eng' not in st.session_state:
    st.session_state.order_eng = OrderEngine(st.session_state.exchange_mgr)
if 'wallet_eng' not in st.session_state:
    st.session_state.wallet_eng = WalletEngine(st.session_state.exchange_mgr)
if 'dry_run_eng' not in st.session_state:
    st.session_state.dry_run_eng = DryRunEngine(initial_virtual_balance=10000.0)
if 'forex_eng' not in st.session_state:
    st.session_state.forex_eng = ForexEngine(st.session_state.exchange_mgr)
if 'auto_strategy_eng' not in st.session_state:
    st.session_state.auto_strategy_eng = AutoStrategyEngine()
if 'auto_strategy_config' not in st.session_state:
    st.session_state.auto_strategy_config = _auth_store.load_strategy_config(st.session_state.auth_user["id"])
if 'auto_strategy_wizard' not in st.session_state:
    st.session_state.auto_strategy_wizard = {}


# --- AUTO STRATEGY SETUP WIZARD ---
AUTO_EXCHANGE_LABELS = {
    "mexc": "MEXC", "bingx": "BingX", "binance": "Binance", "bybit": "Bybit",
    "bitget": "Bitget", "okx": "OKX", "bitmart": "Bitmart", "yubit": "Yubit",
    "blofin": "Blofin", "gateio": "Gate.io", "kraken": "Kraken", "kucoin": "KuCoin",
}
AUTO_FOREX_ASSETS = ["Gold / XAU", "Brent Oil", "Silver / XAG"]


def _save_current_user_strategy_config():
    """Persist only the authenticated user's strategy configuration."""
    if st.session_state.auto_strategy_config is not None:
        _auth_store.save_strategy_config(
            st.session_state.auth_user["id"], st.session_state.auto_strategy_config
        )


def _auto_reset_wizard(existing_config=None):
    """Open a fresh wizard while retaining already-configured exchanges."""
    for key in list(st.session_state.keys()):
        is_wizard_widget = key == "auto_exchange_selection" or key.startswith((
            "auto_asset_", "auto_lev_", "auto_time", "auto_strategy_type", "auto_fixed_", "auto_mexc_",
            "auto_bingx_", "auto_binance_", "auto_bybit_", "auto_bitget_", "auto_okx_", "auto_bitmart_",
            "auto_yubit_", "auto_blofin_", "auto_gateio_", "auto_kraken_", "auto_kucoin_",
        ))
        if is_wizard_widget:
            del st.session_state[key]
    completed = {}
    selected = []
    if existing_config:
        completed = {item["exchange"]: copy.deepcopy(item) for item in existing_config.get("exchanges", [])}
        selected = list(completed)
    st.session_state.auto_strategy_wizard = {
        "step": "exchange", "completed": completed, "selected_exchanges": selected,
        "original_config": copy.deepcopy(existing_config),
    }


def _auto_cancel_setup(wizard):
    """Discard wizard edits; never delete a previously saved configuration."""
    st.session_state.auto_strategy_config = wizard.get("original_config")
    st.session_state.auto_strategy_wizard = {}
    if st.session_state.auto_strategy_config is None:
        st.session_state["fut_strat_toggle"] = False
        st.session_state["fx_strat_toggle"] = False
    st.rerun()


def _auto_toggle_changed(toggle_key):
    """Reopen setup when a user turns an already-configured bot back on."""
    if st.session_state.get(toggle_key) and st.session_state.auto_strategy_config:
        _auto_reset_wizard(st.session_state.auto_strategy_config)


def _auto_strategy_rows(exchange_config):
    # Fixed presets can be viewed, but only custom settings may be auto-traded.
    if exchange_config.get("strategy_type") != "custom":
        return []
    rows = []
    for asset, leverage in exchange_config.get("assets", {}).items():
        for timeframe in exchange_config.get("timeframes", []):
            for index, item in enumerate(exchange_config.get("strategy_parameters", {}).get(timeframe, []), 1):
                if item.get("enabled") and not item.get("removed"):
                    strategy_id = f"{exchange_config['exchange']}-{asset}-{timeframe}-{index}"
                    state = exchange_config.get("strategy_states", {}).get(strategy_id, {})
                    if state.get("removed"):
                        continue
                    rows.append({
                        "id": strategy_id,
                        "exchange": exchange_config["exchange"], "asset": asset,
                        "leverage": leverage, "timeframe": timeframe,
                        "parameter_index": index - 1, "type": "2 EMA",
                        "is_active": state.get("is_active", item.get("is_active", True)), **item,
                    })
    return rows


# Safety migration for configurations that were saved before fixed presets were
# made display-only: remove them from the executable strategy list immediately.
if st.session_state.auto_strategy_config:
    _custom_exchanges = {
        item["exchange"] for item in st.session_state.auto_strategy_config.get("exchanges", [])
        if item.get("strategy_type") == "custom"
    }
    _before_strategy_count = len(st.session_state.auto_strategy_config.get("strategies", []))
    st.session_state.auto_strategy_config["strategies"] = [
        item for item in st.session_state.auto_strategy_config.get("strategies", [])
        if item["exchange"] in _custom_exchanges
    ]
    if len(st.session_state.auto_strategy_config["strategies"]) != _before_strategy_count:
        st.session_state.auto_strategy_eng.stop()
        _auth_store.set_bot_running(st.session_state.auth_user["id"], False)
        add_alert("Fixed preset strategies were disabled; only custom strategies can run automatically.", "warning")


def _auto_update_strategy_source(strategy, **changes):
    """Keep the flattened summary and its exchange configuration in sync."""
    config = st.session_state.auto_strategy_config
    for exchange_config in config.get("exchanges", []):
        if exchange_config["exchange"] != strategy["exchange"]:
            continue
        exchange_config.setdefault("strategy_states", {}).setdefault(strategy["id"], {}).update(changes)
        break
    strategy.update(changes)
    _save_current_user_strategy_config()


def _auto_remove_strategy(strategy):
    """Remove exactly one symbol/timeframe/EMA strategy, without touching others."""
    _auto_update_strategy_source(strategy, removed=True, is_active=False)
    config = st.session_state.auto_strategy_config
    config["strategies"] = [item for item in config["strategies"] if item["id"] != strategy["id"]]
    st.session_state.auto_strategy_eng.set_strategy_enabled(strategy["id"], False)
    _save_current_user_strategy_config()
    add_alert(f"Removed {strategy['exchange'].upper()} {strategy['asset']} {strategy['timeframe']} strategy.", "info")


def _auto_finish_exchange(wizard, strategy_parameters):
    exchange = wizard["active_exchange"]
    new_config = {
        "exchange": exchange,
        "account_alias": wizard["account_alias"],
        "assets": wizard["assets"],
        "timeframes": wizard["timeframes"],
        "strategy_type": wizard["strategy_type"],
        "strategy_parameters": strategy_parameters,
    }
    # A second setup for the same exchange adds strategies instead of replacing
    # the exchange's original configuration.
    previous = wizard["completed"].get(exchange)
    if previous and previous.get("strategy_type") == "custom":
        merged_parameters = copy.deepcopy(previous.get("strategy_parameters", {}))
        for timeframe, entries in strategy_parameters.items():
            merged_parameters.setdefault(timeframe, []).extend(entries)
        new_config["assets"] = {**previous.get("assets", {}), **wizard["assets"]}
        new_config["timeframes"] = list(dict.fromkeys(previous.get("timeframes", []) + wizard["timeframes"]))
        new_config["strategy_parameters"] = merged_parameters
        new_config["strategy_states"] = copy.deepcopy(previous.get("strategy_states", {}))
    wizard["completed"][exchange] = new_config
    wizard["step"] = "exchange"
    wizard.pop("active_exchange", None)
    st.session_state.auto_strategy_wizard = wizard
    st.rerun()


@st.dialog("Exchange & Strategy Setup", width="large")
def show_auto_strategy_setup():
    if not st.session_state.auto_strategy_wizard:
        _auto_reset_wizard()
    wizard = st.session_state.auto_strategy_wizard
    step = wizard.get("step", "exchange")

    if step == "exchange":
        st.subheader("Step 1 — Select exchanges")
        selected = st.multiselect(
            "Choose one or more exchanges", SUPPORTED_EXCHANGES,
            default=wizard.get("selected_exchanges", []),
            format_func=lambda value: AUTO_EXCHANGE_LABELS[value], key="auto_exchange_selection",
        )
        wizard["selected_exchanges"] = selected
        completed = wizard.get("completed", {})
        if completed:
            st.caption("Configured: " + ", ".join(AUTO_EXCHANGE_LABELS[x] for x in completed))
        left, right, done_col = st.columns(3)
        if left.button("Cancel", key="auto_cancel"):
            _auto_cancel_setup(wizard)
        # If all are already configured, Next deliberately reopens the first
        # selected exchange so a user can add another strategy to it.
        next_exchange = next((x for x in selected if x not in completed), selected[0] if selected else None)
        if right.button("Configure / Add strategy", disabled=next_exchange is None, type="primary", key="auto_exchange_next"):
            wizard["active_exchange"] = next_exchange
            wizard["step"] = "assets"
            st.session_state.auto_strategy_wizard = wizard
            st.rerun()
        all_configured = bool(selected) and all(x in completed for x in selected)
        if done_col.button("Done", disabled=not all_configured, type="primary", key="auto_exchange_done"):
            exchange_configs = [completed[x] for x in selected]
            strategies = [row for config in exchange_configs for row in _auto_strategy_rows(config)]
            if not strategies:
                st.error("Select at least one enabled strategy before finishing.")
            else:
                st.session_state.auto_strategy_config = {"exchanges": exchange_configs, "strategies": strategies}
                st.session_state.auto_strategy_wizard = {}
                _save_current_user_strategy_config()
                add_alert("Auto strategy configuration saved.", "success")
                st.rerun()

    elif step == "assets":
        exchange = wizard["active_exchange"]
        st.subheader(f"Step 2 — Coins & leverage: {AUTO_EXCHANGE_LABELS[exchange]}")
        matching_accounts = [
            alias for alias, details in st.session_state.exchange_mgr.connected_exchanges.items()
            if details["exchange_id"].lower() == exchange
        ]
        if not matching_accounts:
            st.error(f"Connect a {AUTO_EXCHANGE_LABELS[exchange]} API account before configuring its auto strategy.")
        account_alias = st.selectbox(
            "Trading account", matching_accounts or ["No matching account connected"],
            key=f"auto_account_{exchange}",
        )
        assets = list(supported_coins)
        if exchange == "bingx":
            assets += AUTO_FOREX_ASSETS
        chosen = {}
        for asset in assets:
            c1, c2 = st.columns([1.4, 1])
            enabled = c1.checkbox(asset, key=f"auto_asset_{exchange}_{asset}")
            leverage = c2.text_input("Manual leverage (x)", disabled=not enabled,
                                     placeholder="e.g. 5x or 20", key=f"auto_lev_{exchange}_{asset}")
            if enabled:
                try:
                    leverage_value = int(leverage.strip().lower().removesuffix("x"))
                    if not 1 <= leverage_value <= 1000:
                        raise ValueError
                    chosen[asset] = leverage_value
                except ValueError:
                    c2.caption("Enter 1x–1000x")
        c1, c2, c3 = st.columns(3)
        if c1.button("Back", key="auto_assets_back"):
            wizard["step"] = "exchange"; st.session_state.auto_strategy_wizard = wizard; st.rerun()
        if c2.button("Cancel", key="auto_assets_cancel"):
            _auto_cancel_setup(wizard)
        if c3.button("Next", disabled=not chosen or not matching_accounts, type="primary", key="auto_assets_next"):
            wizard["assets"] = chosen; wizard["account_alias"] = account_alias; wizard["step"] = "timeframes"
            st.session_state.auto_strategy_wizard = wizard; st.rerun()

    elif step == "timeframes":
        st.subheader("Step 3 — Select timeframes")
        selected = st.multiselect("Timeframe(s)", ["1m", "3m", "5m", "15m"],
                                  default=wizard.get("timeframes", []), key="auto_timeframes")
        custom = st.text_input("Add custom timeframe (e.g. 30m, 1h)", key="auto_custom_timeframe").strip()
        if custom and custom not in selected:
            selected.append(custom)
        c1, c2, c3 = st.columns(3)
        if c1.button("Back", key="auto_time_back"):
            wizard["step"] = "assets"; st.session_state.auto_strategy_wizard = wizard; st.rerun()
        if c2.button("Cancel", key="auto_time_cancel"):
            _auto_cancel_setup(wizard)
        if c3.button("Next", disabled=not selected, type="primary", key="auto_time_next"):
            wizard["timeframes"] = selected; wizard["step"] = "strategy_type"
            st.session_state.auto_strategy_wizard = wizard; st.rerun()

    elif step == "strategy_type":
        st.subheader("Step 4 — Strategy type")
        strategy_type = "custom"
        st.info("Only Custom Strategy is eligible for auto trading. Fixed presets cannot place auto orders.")
        c1, c2, c3 = st.columns(3)
        if c1.button("Back", key="auto_type_back"):
            wizard["step"] = "timeframes"; st.session_state.auto_strategy_wizard = wizard; st.rerun()
        if c2.button("Cancel", key="auto_type_cancel"):
            _auto_cancel_setup(wizard)
        if c3.button("Next", type="primary", key="auto_type_next"):
            wizard["strategy_type"] = strategy_type
            wizard["step"] = "custom"
            st.session_state.auto_strategy_wizard = wizard; st.rerun()

    elif step == "fixed":
        st.subheader("Step 5 — Fixed strategy presets")
        st.info("Built-in 2 EMA preset: 20 fast EMA / 200 slow EMA. You may use it on every selected timeframe.")
        enabled = st.checkbox("Enable built-in 2 EMA strategy", value=True, key="auto_fixed_enabled")
        c1, c2, c3 = st.columns(3)
        if c1.button("Back", key="auto_fixed_back"):
            wizard["step"] = "strategy_type"; st.session_state.auto_strategy_wizard = wizard; st.rerun()
        if c2.button("Cancel", key="auto_fixed_cancel"):
            _auto_cancel_setup(wizard)
        if c3.button("Done", disabled=not enabled, type="primary", key="auto_fixed_done"):
            params = {tf: [{"enabled": True, "fast_ema": 20, "slow_ema": 200, "use_rsi": False}]
                      for tf in wizard["timeframes"]}
            _auto_finish_exchange(wizard, params)

    elif step == "custom":
        st.subheader("Step 5 — Custom 2 EMA parameters")
        params = {}
        any_enabled = False
        for timeframe in wizard["timeframes"]:
            st.markdown(f"#### Timeframe: {timeframe}")
            params[timeframe] = []
            for number in range(1, 4):
                key_base = f"auto_{wizard['active_exchange']}_{timeframe}_{number}"
                enabled = st.checkbox(f"2 EMA Strategy #{number}", key=f"{key_base}_enabled")
                c1, c2, c3 = st.columns(3)
                fast = c1.number_input("Fast EMA", min_value=1, value=10, step=1, disabled=not enabled, key=f"{key_base}_fast")
                slow = c2.number_input("Slow EMA", min_value=2, value=20, step=1, disabled=not enabled, key=f"{key_base}_slow")
                rsi = c3.toggle("RSI Filter", value=False, disabled=not enabled, key=f"{key_base}_rsi")
                valid = enabled and fast < slow
                if enabled and not valid:
                    st.caption("Fast EMA must be lower than Slow EMA.")
                any_enabled = any_enabled or valid
                params[timeframe].append({"enabled": valid, "fast_ema": int(fast), "slow_ema": int(slow), "use_rsi": rsi})
        c1, c2, c3 = st.columns(3)
        if c1.button("Back", key="auto_custom_back"):
            wizard["step"] = "strategy_type"; st.session_state.auto_strategy_wizard = wizard; st.rerun()
        if c2.button("Cancel", key="auto_custom_cancel"):
            _auto_cancel_setup(wizard)
        if c3.button("Done", disabled=not any_enabled, type="primary", key="auto_custom_done"):
            _auto_finish_exchange(wizard, params)


def render_auto_strategy_summary():
    """Show and control each configured strategy independently."""
    config = st.session_state.auto_strategy_config
    if not config:
        return
    st.markdown("### Step 6 - Active Strategies Summary & Risk Management")
    for exchange_config in config["exchanges"]:
        exchange = exchange_config["exchange"]
        st.markdown(f"#### {AUTO_EXCHANGE_LABELS[exchange]}")
        exchange_config["margin_mode"] = st.radio(
            "Exchange margin mode", ["isolated", "cross"], horizontal=True,
            key=f"auto_margin_{exchange}",
        )
        rows = [item for item in config["strategies"] if item["exchange"] == exchange]
        if not rows:
            st.caption("No active strategies for this exchange.")
        for item in rows:
            st.markdown("---")
            title_col, state_col, remove_col = st.columns([5, 1.3, 0.8])
            title_col.markdown(
                f"**{AUTO_EXCHANGE_LABELS[exchange]} | {item['asset']} | {item['timeframe']} | "
                f"2 EMA ({item['fast_ema']}/{item['slow_ema']})**"
            )
            active = state_col.toggle("ON", value=item.get("is_active", True), key=f"active_{item['id']}")
            if active != item.get("is_active", True):
                _auto_update_strategy_source(item, is_active=active)
                st.session_state.auto_strategy_eng.set_strategy_enabled(item["id"], active)
            if remove_col.button("X", key=f"remove_{item['id']}", help="Remove this strategy only"):
                _auto_remove_strategy(item)
                st.rerun()
            state_label = "ON - waiting for signal" if item.get("is_active", True) else "OFF"
            st.caption(f"Status: {state_label} | Leverage: {item['leverage']}x")
            c1, c2, c3 = st.columns(3)
            item["sl_type"] = c1.selectbox("SL", ["ATR", "Ratio"], key=f"sltype_{item['id']}")
            item["sl_value"] = c2.number_input("SL ATR period / ratio", min_value=0.1, value=3.0, key=f"slval_{item['id']}")
            item["tp_value"] = c3.number_input("TP ATR period / ratio", min_value=0.1, value=6.0, key=f"tpval_{item['id']}")
            item["margin_type"] = st.radio("Wallet margin", ["Fixed amount", "Wallet ratio"], horizontal=True, key=f"mtype_{item['id']}")
            item["margin_value"] = st.number_input("Margin USD / %", min_value=0.1, value=10.0, key=f"mval_{item['id']}")
    c1, c2 = st.columns(2)
    running = _auth_store.is_bot_running(st.session_state.auth_user["id"])
    if c1.button("RUN BOT", type="primary", disabled=running or not config["strategies"], key="auto_run_bot"):
        _save_current_user_strategy_config()
        st.session_state.auto_strategy_eng.start(config)
        _auth_store.set_bot_running(st.session_state.auth_user["id"], True)
        add_alert("Auto strategy bot is enabled. The server worker will continue after this browser closes.", "success")
        st.rerun()
    if c2.button("STOP BOT", disabled=not running, key="auto_stop_bot"):
        st.session_state.auto_strategy_eng.stop()
        _auth_store.set_bot_running(st.session_state.auth_user["id"], False)
        add_alert("Auto strategy bot stopped.", "warning")
        st.rerun()
    if running:
        st.success("Status: Running / Waiting for Signal")
    else:
        st.info("Status: Ready - configure risk settings, then run the bot.")

# --- LIVE PRICE FETCHING ---
def get_exchange_live_price(exchange_name, symbol, market_type="futures"):
    try:
        ex_id = exchange_name.lower()
        if hasattr(ccxt, ex_id):
            ex_class = getattr(ccxt, ex_id)
            if market_type == "futures":
                exchange = ex_class({'options': {'defaultType': 'swap'}})
                req_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
            else:
                exchange = ex_class({'options': {'defaultType': 'spot'}})
                req_symbol = symbol
            
            ticker = exchange.fetch_ticker(req_symbol)
            return float(ticker['last'])
    except Exception:
        try:
            binance = ccxt.binanceusdm() if market_type == "futures" else ccxt.binance()
            ticker = binance.fetch_ticker(symbol.replace("/", "") if market_type == "futures" else symbol)
            return float(ticker['last'])
        except Exception:
            return None

@st.fragment(run_every=2)
def display_live_price(exchange_name, symbol, market_type="futures"):
    price = get_exchange_live_price(exchange_name, symbol, market_type)
    label_text = f"{exchange_name.upper()} {market_type.capitalize()} Price"
    if price is None or price <= 0:
        st.metric(label=label_text, value="Unavailable")
        st.error("Live market price is unavailable. Trading is disabled until a valid price is received.")
        return None
    st.metric(label=label_text, value=f"${price:,.2f}")
    return price

# --- LOGIN MODAL DIALOG ---
@st.dialog("🔑 Sign In / Log In")
def show_login_dialog():
    st.write("Welcome to **UniTrade Terminal**")
    user_email = st.text_input("Email / Username", placeholder="user@example.com")
    user_pass = st.text_input("Password", type="password", placeholder="••••••••")
    col_l1, col_l2 = st.columns(2)
    with col_l1:
        if st.button("Log In", type="primary", use_container_width=True):
            st.success("Logged in successfully!")
            st.rerun()
    with col_l2:
        if st.button("Sign Up", use_container_width=True):
            st.info("Registration flow triggered.")

now_pkt = datetime.datetime.now(pytz.timezone("Asia/Karachi"))
time_str = now_pkt.strftime("%I:%M:%S %p PKT")

# --- 4. TOP BAR ---
mode_label = "DEMO SIMULATOR" if st.session_state.dry_run_eng.is_active else "REAL LIVE MODE"
mode_class = "mode-badge-demo" if st.session_state.dry_run_eng.is_active else "mode-badge-live"

col_top_left, col_top_mid, col_top_right = st.columns([2.5, 1, 1])

with col_top_left:
    st.markdown(f"""
    <div class="brand-title">⚡ UniTrade <span>v{APP_VERSION}</span> &nbsp; 
    <span class="{mode_class}">MODE: {mode_label}</span></div>
    """, unsafe_allow_html=True)

with col_top_mid:
    st.markdown(f"<div style='color: #848e9c; font-weight: 600; padding-top: 5px;'>🕒 {time_str}</div>", unsafe_allow_html=True)

with col_top_right:
    st.caption(st.session_state.auth_user["email"])
    if st.button("Log out", key="top_logout_btn", use_container_width=True):
        st.session_state.auto_strategy_eng.stop()
        st.session_state.clear()
        st.rerun()

st.markdown("---")

# --- 5. SIDEBAR NAVIGATION ---
st.sidebar.markdown("### 🎛️ Navigation")
nav_choice = st.sidebar.radio("Go to Panel", [
    "⚡ Futures Trading Terminal", 
    "🥇 BingX Forex & Commodities",
    "🛒 Spot Trading Terminal",
    "💼 Connected Wallets", 
    "🤖 Strategies Hub", 
    "👤 Profile",
    "🔑 Exchange Connections",
    "⚙️ Settings"
], label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.markdown("### 🧪 Demo Trading")
demo_toggle = st.sidebar.toggle("ACTIVATE DEMO WALLET ($10k)", value=st.session_state.dry_run_eng.is_active)
st.session_state.dry_run_eng.toggle_demo_mode(demo_toggle)

if demo_toggle:
    st.sidebar.info(f"Virtual Funds: ${st.session_state.dry_run_eng.virtual_balance:,.2f} USDT")

connected_list = list(st.session_state.exchange_mgr.connected_exchanges.keys())
if connected_list:
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🟢 Active Accounts")
    for acc in connected_list:
        ex_id = st.session_state.exchange_mgr.connected_exchanges[acc]["exchange_id"].upper()
        col_a, col_b = st.sidebar.columns([3, 1])
        col_a.caption(f"**{acc}** ({ex_id})")
        if col_b.button("❌", key=f"side_disc_{acc}"):
            st.session_state.exchange_mgr.disconnect_exchange(acc)
            _auth_store.delete_exchange_credentials(st.session_state.auth_user["id"], acc)
            st.rerun()

# --- 6. FUTURES TRADING TERMINAL VIEW ---
if nav_choice == "⚡ Futures Trading Terminal":
    col_manual, col_strategy_mid, col_alerts = st.columns([1.5, 1.5, 1.2])

    with col_manual:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Futures Trading Panel</div>
        """, unsafe_allow_html=True)
        
        selected_exchange = st.selectbox("Select Exchange Data Feed", SUPPORTED_EXCHANGES, key="fut_ex_select")
        active_accounts = list(st.session_state.exchange_mgr.connected_exchanges.keys())
        selected_account = st.selectbox("Select Account", active_accounts or ["No Account Connected"], key="fut_acc")
        
        if selected_account != "No Account Connected":
            bal = st.session_state.wallet_eng.get_balances(selected_account, wallet_type="futures")
            f_usdt = bal.get('free_usdt', 0.0) if bal['status'] else 0.0
            st.caption(f"💼 **Futures Wallet Balance:** `${f_usdt:,.2f} USDT`")
        else:
            st.caption("💼 **Futures Wallet Balance:** `$0.00 USDT`")

        selected_pair = st.selectbox("Futures Pair", supported_coins, key="fut_pair")
        current_live_p = display_live_price(selected_exchange, selected_pair, market_type="futures")

        base_asset = selected_pair.split("/")[0]
        max_leverage_limit = 400 if base_asset in ["BTC", "ETH"] else 200

        col_m1, col_m2 = st.columns([1, 2])
        with col_m1:
            margin_mode = st.selectbox("Margin Mode", ["isolated", "cross"])
        with col_m2:
            st.write(f"Leverage: **{max_leverage_limit}x Max**")
            leverage = st.slider("Select Leverage", min_value=1, max_value=max_leverage_limit, value=20, step=1, key="fut_lev_slider")

        sizing_mode = st.radio("Sizing Mode", ["By Cost (Margin USDT)", "By Total Position Value"], horizontal=True)
        amount_val = st.number_input("Order Amount (USDT)", min_value=0.5, value=10.0, step=1.0)
        
        col_sl, col_tp = st.columns(2)
        with col_sl:
            init_sl = st.number_input("Stop Loss Price ($) [Optional]", value=0.0, step=10.0)
        with col_tp:
            init_tp = st.number_input("Take Profit Price ($) [Optional]", value=0.0, step=10.0)

        if sizing_mode == "By Cost (Margin USDT)":
            user_margin_used = amount_val
            total_position_val = amount_val * leverage
        else:
            total_position_val = amount_val
            user_margin_used = amount_val / leverage

        crypto_quantity = total_position_val / current_live_p if current_live_p and current_live_p > 0 else 0.0

        st.markdown(f"""
        <div class="calc-box">
            <div class="calc-box-title">📊 Pre-Order Estimation Preview</div>
            <div class="calc-row"><span>Wallet Margin Required:</span> <b>${user_margin_used:,.2f} USDT</b></div>
            <div class="calc-row"><span>Total Position Value:</span> <b>${total_position_val:,.2f} USDT</b></div>
            <div class="calc-row"><span>Estimated Crypto Size:</span> <b>{crypto_quantity:.6f} {base_asset}</b></div>
            <div class="calc-row"><span>Applied Leverage:</span> <b>{leverage}x ({margin_mode.capitalize()})</b></div>
        </div>
        """, unsafe_allow_html=True)
        
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("🟢 Futures Long", type="primary", use_container_width=True, disabled=not current_live_p):
                if st.session_state.dry_run_eng.is_active:
                    st.session_state.dry_run_eng.execute_demo_order(selected_pair, "buy", crypto_quantity, current_live_p, leverage)
                    add_alert(f"Demo Long Filled: {crypto_quantity:.4f} {base_asset}", "success")
                else:
                    sizing_type_arg = "cost" if "By Cost" in sizing_mode else "quantity"
                    res = st.session_state.order_eng.place_futures_order(
                        selected_account, selected_pair, "buy", amount_val, margin_mode, leverage,
                        sizing_type=sizing_type_arg, current_price=current_live_p,
                        stop_loss=init_sl if init_sl > 0 else None,
                        take_profit=init_tp if init_tp > 0 else None
                    )
                    if res.get("status"):
                        add_alert(f"Futures Long Executed: {selected_pair}", "success")
                    else:
                        add_alert(f"Order failed: {res.get('message')}", "error")

        with col_b2:
            if st.button("🔴 Futures Short", type="secondary", use_container_width=True, disabled=not current_live_p):
                if st.session_state.dry_run_eng.is_active:
                    st.session_state.dry_run_eng.execute_demo_order(selected_pair, "sell", crypto_quantity, current_live_p, leverage)
                    add_alert(f"Demo Short Filled: {crypto_quantity:.4f} {base_asset}", "success")
                else:
                    sizing_type_arg = "cost" if "By Cost" in sizing_mode else "quantity"
                    res = st.session_state.order_eng.place_futures_order(
                        selected_account, selected_pair, "sell", amount_val, margin_mode, leverage,
                        sizing_type=sizing_type_arg, current_price=current_live_p,
                        stop_loss=init_sl if init_sl > 0 else None,
                        take_profit=init_tp if init_tp > 0 else None
                    )
                    if res.get("status"):
                        add_alert(f"Futures Short Executed: {selected_pair}", "success")
                    else:
                        add_alert(f"Order failed: {res.get('message')}", "error")

        st.markdown("</div>", unsafe_allow_html=True)

    with col_strategy_mid:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Futures Auto Trading Strategy</div>
        """, unsafe_allow_html=True)
        futures_auto_enabled = st.toggle("Futures Bot Status", value=False, key="fut_strat_toggle",
                                         on_change=_auto_toggle_changed, args=("fut_strat_toggle",))
        if futures_auto_enabled and st.session_state.auto_strategy_wizard:
            show_auto_strategy_setup()
        elif futures_auto_enabled and st.session_state.auto_strategy_config is None:
            show_auto_strategy_setup()
        elif st.session_state.auto_strategy_config:
            st.success(f"{len(st.session_state.auto_strategy_config['strategies'])} strategy instance(s) configured.")
            if futures_auto_enabled and st.button("Add / Edit Strategy", key="fut_auto_add_edit",
                                                   disabled=st.session_state.auto_strategy_eng.is_running):
                _auto_reset_wizard(st.session_state.auto_strategy_config)
                show_auto_strategy_setup()
            if st.session_state.auto_strategy_eng.is_running:
                st.caption("Stop the bot before changing its strategy configuration.")
        else:
            st.info("No Active Futures Strategy Engine.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_alerts:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Live Alerts</div>
        """, unsafe_allow_html=True)
        if st.session_state.alerts:
            for al in st.session_state.alerts:
                st.write(f"[{al['time']}] {al['msg']}")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    if futures_auto_enabled and st.session_state.auto_strategy_config:
        render_auto_strategy_summary()

    col_dash_title, col_dash_ref = st.columns([3, 1])
    with col_dash_title:
        st.markdown("### 📊 Open Positions Dashboard")
    with col_dash_ref:
        if st.button("🔄 Refresh Positions", key="fut_pos_refresh", use_container_width=True):
            st.rerun()

    if st.session_state.dry_run_eng.is_active:
        demo_positions = st.session_state.dry_run_eng.virtual_positions
        if demo_positions:
            st.dataframe(pd.DataFrame(demo_positions), use_container_width=True)
        else:
            st.info("No active demo positions.")
    else:
        if selected_account != "No Account Connected":
            positions = st.session_state.order_eng.fetch_live_positions(selected_account)
            if positions:
                df_positions = pd.DataFrame(positions)
                st.dataframe(df_positions[["Symbol", "Side", "Contracts", "Leverage", "EntryPrice", "MarkPrice", "LiquidationPrice", "UnrealizedPnL", "PnLPercent"]], use_container_width=True)
                
                st.markdown("#### ⚙️ Manage Selected Position Controls")
                m_col1, m_col2, m_col3 = st.columns(3)
                
                pos_symbols = [f"{p['Symbol']} ({p['Side']})" for p in positions]
                
                with m_col1:
                    st.markdown("**🛡️ Update SL / TP (Cancel & Replace)**")
                    selected_pos_str = st.selectbox("Select Target Position", pos_symbols, key="sl_tp_pos_target")
                    target_index = pos_symbols.index(selected_pos_str)
                    target_pos_tpsl = positions[target_index]
                    
                    sl_in = st.number_input("Stop Loss Price ($)", value=0.0, step=10.0, key="sl_val_in")
                    tp_in = st.number_input("Take Profit Price ($)", value=0.0, step=10.0, key="tp_val_in")
                    
                    tpsl_pct = st.select_slider(
                        "Target Position Volume (%)",
                        options=[25, 50, 75, 100],
                        value=100,
                        key="tpsl_vol_slider"
                    )
                    
                    if st.button("Set SL / TP Order", use_container_width=True):
                        pos_lev = int(target_pos_tpsl['Leverage'].replace('x', '')) if 'Leverage' in target_pos_tpsl else 20
                        
                        res = st.session_state.order_eng.set_position_tpsl(
                            account_alias=selected_account,
                            symbol=target_pos_tpsl['Symbol'],
                            side=target_pos_tpsl['Side'],
                            tp_price=tp_in if tp_in > 0 else None,
                            sl_price=sl_in if sl_in > 0 else None,
                            total_contracts=target_pos_tpsl['Contracts'],
                            percentage=tpsl_pct,
                            leverage=pos_lev
                        )
                        
                        if res['status']:
                            st.success(res['message'])
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(res['message'])

                with m_col2:
                    st.markdown("**🎯 Close Selected Trade**")
                    pos_to_close_str = st.selectbox("Select Position to Close", pos_symbols, key="pos_close_target")
                    close_target_index = pos_symbols.index(pos_to_close_str)
                    target_pos_close = positions[close_target_index]
                    
                    close_pct = st.select_slider(
                        "Close Volume (%)",
                        options=[25, 50, 75, 100],
                        value=100,
                        key="close_vol_slider"
                    )
                    
                    if st.button("Close Position Market Price", type="secondary", use_container_width=True):
                        close_qty = float(target_pos_close['Contracts']) * (close_pct / 100.0)
                        
                        is_bingx = st.session_state.exchange_mgr.connected_exchanges.get(selected_account, {}).get("exchange_id", "").lower() == "bingx"
                        
                        if is_bingx:
                            res = st.session_state.forex_eng.close_forex_position(
                                selected_account, target_pos_close['Symbol'], target_pos_close['Side'], close_pct
                            )
                        else:
                            res = st.session_state.order_eng.close_position(
                                selected_account, target_pos_close['Symbol'], target_pos_close['Side'], close_qty
                            )
                            
                        if res['status']:
                            st.success(res['message'])
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(res['message'])

                with m_col3:
                    st.markdown("**🚨 Emergency Close All**")
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("CLOSE ALL POSITIONS", type="secondary", use_container_width=True):
                        is_bingx = st.session_state.exchange_mgr.connected_exchanges.get(selected_account, {}).get("exchange_id", "").lower() == "bingx"
                        for p in positions:
                            if is_bingx:
                                st.session_state.forex_eng.close_forex_position(selected_account, p['Symbol'], p['Side'], 100)
                            else:
                                st.session_state.order_eng.close_position(selected_account, p['Symbol'], p['Side'], p['Contracts'])
                        st.warning("Executed Panic Close for All Positions!")
                        time.sleep(1)
                        st.rerun()
            else:
                st.info("No Open Futures Positions active on this exchange account.")
        else:
            st.info("Select an active exchange account to view open positions.")

# --- 7. BINGX FOREX & COMMODITIES VIEW ---
elif nav_choice == "🥇 BingX Forex & Commodities":
    col_manual, col_strategy_mid, col_alerts = st.columns([1.5, 1.5, 1.2])

    with col_manual:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">BingX Forex & Commodities Trading Panel</div>
        """, unsafe_allow_html=True)

        active_accounts = list(st.session_state.exchange_mgr.connected_exchanges.keys())
        bingx_accounts = [acc for acc in active_accounts if st.session_state.exchange_mgr.connected_exchanges[acc]["exchange_id"].lower() == "bingx"]

        selected_fx_acc = st.selectbox("Select BingX Account", bingx_accounts or ["No BingX Account Connected"], key="fx_acc")
        
        if selected_fx_acc != "No BingX Account Connected":
            bal = st.session_state.wallet_eng.get_balances(selected_fx_acc, wallet_type="futures")
            f_usdt = bal.get('free_usdt', 0.0) if bal['status'] else 0.0
            st.caption(f"💼 **BingX Futures Balance:** `${f_usdt:,.2f} USDT`")
        else:
            st.caption("💼 **BingX Futures Balance:** `$0.00 USDT`")

        fx_asset = st.selectbox("Select Commodity Asset", ["Gold", "Brent Oil", "Silver"], key="fx_asset_select")

        fx_symbol_map = {
            'Gold': 'XAUT/USDT:USDT',
            'Brent Oil': 'NCCO1OILBRENT2USD/USDT:USDT',
            'Silver': 'NCCOXAG2USD/USDT:USDT'
        }
        target_symbol = fx_symbol_map[fx_asset]
        current_fx_price = display_live_price("bingx", target_symbol, market_type="futures")

        max_fx_leverage = 1000 if fx_asset == "Gold" else 200

        col_fx_l1, col_fx_l2 = st.columns([1, 2])
        with col_fx_l1:
            margin_mode = st.selectbox("Margin Mode", ["isolated", "cross"], key="fx_margin_mode")
        with col_fx_l2:
            st.write(f"Leverage: **{max_fx_leverage}x Max**")
            fx_leverage = st.slider("Select Leverage", min_value=1, max_value=max_fx_leverage, value=20, step=1, key="fx_lev_slider")

        fx_sizing_mode = st.radio("Sizing Mode", ["By Cost (Margin USDT)", "By Total Position Value"], horizontal=True, key="fx_sizing_radio")
        fx_amount_val = st.number_input("Order Amount (USDT)", min_value=0.5, value=10.0, step=1.0, key="fx_amount_input")

        if fx_sizing_mode == "By Cost (Margin USDT)":
            user_margin_used = fx_amount_val
            total_position_val = fx_amount_val * fx_leverage
        else:
            total_position_val = fx_amount_val
            user_margin_used = fx_amount_val / fx_leverage

        est_contracts = total_position_val / current_fx_price if current_fx_price and current_fx_price > 0 else 0.0

        st.markdown(f"""
        <div class="calc-box">
            <div class="calc-box-title">📊 Pre-Order Estimation Preview ({fx_asset})</div>
            <div class="calc-row"><span>Wallet Margin Required:</span> <b>${user_margin_used:,.2f} USDT</b></div>
            <div class="calc-row"><span>Total Position Value:</span> <b>${total_position_val:,.2f} USDT</b></div>
            <div class="calc-row"><span>Estimated Contracts/Size:</span> <b>{est_contracts:.4f}</b></div>
            <div class="calc-row"><span>Applied Leverage:</span> <b>{fx_leverage}x ({margin_mode.capitalize()})</b></div>
        </div>
        """, unsafe_allow_html=True)

        col_fx_sl, col_fx_tp = st.columns(2)
        with col_fx_sl:
            fx_sl = st.number_input("Stop Loss Price ($) [Optional]", value=0.0, step=1.0, key="fx_sl_in")
        with col_fx_tp:
            fx_tp = st.number_input("Take Profit Price ($) [Optional]", value=0.0, step=1.0, key="fx_tp_in")

        col_fx_b1, col_fx_b2 = st.columns(2)
        with col_fx_b1:
            if st.button("🟢 BUY Forex (Long)", type="primary", use_container_width=True, key="fx_buy_btn", disabled=not current_fx_price):
                sizing_type_arg = "cost" if "By Cost" in fx_sizing_mode else "quantity"
                res = st.session_state.forex_eng.place_forex_order(
                    account_alias=selected_fx_acc,
                    symbol_key=fx_asset,
                    side="buy",
                    amount=fx_amount_val,
                    margin_mode=margin_mode,
                    leverage=fx_leverage,
                    sizing_type=sizing_type_arg,
                    current_price=current_fx_price,
                    stop_loss=fx_sl if fx_sl > 0 else None,
                    take_profit=fx_tp if fx_tp > 0 else None
                )
                if res["status"]:
                    add_alert(f"Forex Long Executed: {fx_asset}", "success")
                    st.success(res["message"])
                    time.sleep(1)
                    st.rerun()
                else:
                    add_alert(f"Forex Order Failed: {res['message']}", "error")
                    st.error(res["message"])

        with col_fx_b2:
            if st.button("🔴 SELL Forex (Short)", type="secondary", use_container_width=True, key="fx_sell_btn", disabled=not current_fx_price):
                sizing_type_arg = "cost" if "By Cost" in fx_sizing_mode else "quantity"
                res = st.session_state.forex_eng.place_forex_order(
                    account_alias=selected_fx_acc,
                    symbol_key=fx_asset,
                    side="sell",
                    amount=fx_amount_val,
                    margin_mode=margin_mode,
                    leverage=fx_leverage,
                    sizing_type=sizing_type_arg,
                    current_price=current_fx_price,
                    stop_loss=fx_sl if fx_sl > 0 else None,
                    take_profit=fx_tp if fx_tp > 0 else None
                )
                if res["status"]:
                    add_alert(f"Forex Short Executed: {fx_asset}", "success")
                    st.success(res["message"])
                    time.sleep(1)
                    st.rerun()
                else:
                    add_alert(f"Forex Order Failed: {res['message']}", "error")
                    st.error(res["message"])

        st.markdown("</div>", unsafe_allow_html=True)

    with col_strategy_mid:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Forex Auto Trading Strategy</div>
        """, unsafe_allow_html=True)
        forex_auto_enabled = st.toggle("Forex Bot Status", value=False, key="fx_strat_toggle",
                                       on_change=_auto_toggle_changed, args=("fx_strat_toggle",))
        if forex_auto_enabled and st.session_state.auto_strategy_wizard:
            show_auto_strategy_setup()
        elif forex_auto_enabled and st.session_state.auto_strategy_config is None:
            show_auto_strategy_setup()
        elif st.session_state.auto_strategy_config:
            st.success(f"{len(st.session_state.auto_strategy_config['strategies'])} strategy instance(s) configured.")
            if forex_auto_enabled and st.button("Add / Edit Strategy", key="fx_auto_add_edit",
                                                 disabled=st.session_state.auto_strategy_eng.is_running):
                _auto_reset_wizard(st.session_state.auto_strategy_config)
                show_auto_strategy_setup()
            if st.session_state.auto_strategy_eng.is_running:
                st.caption("Stop the bot before changing its strategy configuration.")
        else:
            st.info("No Active Forex Strategy Engine.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_alerts:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Live Alerts</div>
        """, unsafe_allow_html=True)
        if st.session_state.alerts:
            for al in st.session_state.alerts:
                st.write(f"[{al['time']}] {al['msg']}")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    if forex_auto_enabled and st.session_state.auto_strategy_config:
        render_auto_strategy_summary()

    col_fx_dash_title, col_fx_dash_ref = st.columns([3, 1])
    with col_fx_dash_title:
        st.markdown("### 📊 Open Positions Dashboard (BingX Forex)")
    with col_fx_dash_ref:
        if st.button("🔄 Refresh Positions", key="fx_pos_refresh", use_container_width=True):
            st.rerun()

    if selected_fx_acc != "No BingX Account Connected":
        fx_positions = st.session_state.order_eng.fetch_live_positions(selected_fx_acc)
        if fx_positions:
            df_fx_pos = pd.DataFrame(fx_positions)
            st.dataframe(df_fx_pos[["Symbol", "Side", "Contracts", "Leverage", "EntryPrice", "MarkPrice", "LiquidationPrice", "UnrealizedPnL", "PnLPercent"]], use_container_width=True)
            
            st.markdown("#### ⚙️ Manage Selected Forex Position Controls")
            m_col1, m_col2, m_col3 = st.columns(3)
            
            fx_pos_symbols = [f"{p['Symbol']} ({p['Side']})" for p in fx_positions]
            
            with m_col1:
                st.markdown("**🛡️ Update SL / TP**")
                selected_fx_pos_str = st.selectbox("Select Target Position", fx_pos_symbols, key="fx_sl_tp_pos_target")
                fx_target_index = fx_pos_symbols.index(selected_fx_pos_str)
                target_fx_pos_tpsl = fx_positions[fx_target_index]
                
                fx_sl_in = st.number_input("Stop Loss Price ($)", value=0.0, step=1.0, key="fx_sl_val_in")
                fx_tp_in = st.number_input("Take Profit Price ($)", value=0.0, step=1.0, key="fx_tp_val_in")
                
                fx_tpsl_pct = st.select_slider(
                    "Target Position Volume (%)",
                    options=[25, 50, 75, 100],
                    value=100,
                    key="fx_tpsl_vol_slider"
                )
                
                if st.button("Set SL / TP Order", key="fx_set_tpsl_btn", use_container_width=True):
                    res = st.session_state.forex_eng.set_forex_sl_tp(
                        account_alias=selected_fx_acc,
                        symbol_key=target_fx_pos_tpsl['Symbol'],
                        position_side=target_fx_pos_tpsl['Side'],
                        stop_loss=fx_sl_in if fx_sl_in > 0 else None,
                        take_profit=fx_tp_in if fx_tp_in > 0 else None
                    )
                    if res['status']:
                        st.success(res['message'])
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(res['message'])

            with m_col2:
                st.markdown("**🎯 Close Selected Trade**")
                fx_pos_close_str = st.selectbox("Select Position to Close", fx_pos_symbols, key="fx_pos_close_target")
                fx_close_target_index = fx_pos_symbols.index(fx_pos_close_str)
                target_fx_pos_close = fx_positions[fx_close_target_index]
                
                fx_close_pct = st.select_slider(
                    "Close Volume (%)",
                    options=[25, 50, 75, 100],
                    value=100,
                    key="fx_close_vol_slider"
                )
                
                if st.button("Close Position Market Price", type="secondary", key="fx_close_pos_btn", use_container_width=True):
                    res = st.session_state.forex_eng.close_forex_position(
                        selected_fx_acc, target_fx_pos_close['Symbol'], target_fx_pos_close['Side'], fx_close_pct
                    )
                    if res['status']:
                        st.success(res['message'])
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(res['message'])

            with m_col3:
                st.markdown("**🚨 Emergency Close All**")
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("CLOSE ALL FOREX POSITIONS", type="secondary", key="fx_close_all_btn", use_container_width=True):
                    for p in fx_positions:
                        st.session_state.forex_eng.close_forex_position(selected_fx_acc, p['Symbol'], p['Side'], 100)
                    st.warning("Executed Panic Close for All Forex Positions!")
                    time.sleep(1)
                    st.rerun()
        else:
            st.info("No Open Forex Positions active on this BingX account.")
    else:
        st.info("Connect an active BingX account to view open forex positions.")

# --- 8. SPOT TRADING TERMINAL VIEW ---
elif nav_choice == "🛒 Spot Trading Terminal":
    col_manual, col_strategy_mid, col_alerts = st.columns([1.5, 1.5, 1.2])

    with col_manual:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Spot Trading Panel</div>
        """, unsafe_allow_html=True)
        
        selected_exchange = st.selectbox("Select Exchange Data Feed", SUPPORTED_EXCHANGES, key="spot_ex_select")
        active_accounts = list(st.session_state.exchange_mgr.connected_exchanges.keys())
        selected_account = st.selectbox("Select Account", active_accounts or ["No Account Connected"], key="spot_acc")
        
        if selected_account != "No Account Connected":
            bal = st.session_state.wallet_eng.get_balances(selected_account, wallet_type="spot")
            s_usdt = bal.get('free_usdt', 0.0) if bal['status'] else 0.0
            st.caption(f"💼 **Spot Wallet Balance:** `${s_usdt:,.2f} USDT`")
        else:
            st.caption("💼 **Spot Wallet Balance:** `$0.00 USDT`")

        selected_pair = st.selectbox("Spot Pair", supported_coins, key="spot_pair")
        current_live_p = display_live_price(selected_exchange, selected_pair, market_type="spot")

        amount_val = st.number_input("Order Amount (USDT)", min_value=1.0, value=20.0, step=1.0)
        
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("🟢 Buy Spot", type="primary", use_container_width=True, disabled=not current_live_p):
                spot_qty = amount_val / current_live_p
                res = st.session_state.order_eng.place_spot_order(selected_account, selected_pair, "buy", spot_qty)
                if res.get("status"):
                    add_alert(f"Spot Buy Order Filled: {selected_pair}", "success")
                else:
                    add_alert(f"Spot Buy Error: {res.get('message')}", "error")

        with col_b2:
            if st.button("🔴 Sell Spot", type="secondary", use_container_width=True, disabled=not current_live_p):
                spot_qty = amount_val / current_live_p
                res = st.session_state.order_eng.place_spot_order(selected_account, selected_pair, "sell", spot_qty)
                if res.get("status"):
                    add_alert(f"Spot Sell Order Filled: {selected_pair}", "success")
                else:
                    add_alert(f"Spot Sell Error: {res.get('message')}", "error")

        st.markdown("</div>", unsafe_allow_html=True)

    with col_strategy_mid:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Spot Auto Trading Strategy</div>
        """, unsafe_allow_html=True)
        st.toggle("Spot Bot Status", value=False, key="spot_strat_toggle")
        st.info("No Active Spot Strategy Engine.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_alerts:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">Live Alerts</div>
        """, unsafe_allow_html=True)
        if st.session_state.alerts:
            for al in st.session_state.alerts:
                st.write(f"[{al['time']}] {al['msg']}")
        st.markdown("</div>", unsafe_allow_html=True)

# --- 9. STRATEGIES HUB VIEW (MERGED BOTH CODES HERE) ---
elif nav_choice == "🤖 Strategies Hub":
    st.title("🧩 Modular Strategy Engine")
    st.caption("EMA Crossover with Dynamic RSI Filtering")

    st.markdown("---")
    
    # 1. UI Controls Header
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("⚙️ EMA Settings")
        strat_mode = st.radio("Strategy Mode", ["fixed", "custom"], format_func=lambda x: "Fixed Mode" if x == "fixed" else "Custom Mode", key="strat_mode")
        timeframe = st.selectbox("Select Timeframe", ["1m", "5m", "15m", "1h", "4h"], index=1, key="strat_tf")
        
        if strat_mode == "custom":
            fast_ema = st.number_input("Fast EMA Length", min_value=1, max_value=500, value=20, key="fast_ema_val")
            slow_ema = st.number_input("Slow EMA Length", min_value=2, max_value=2000, value=200, key="slow_ema_val")
        else:
            fast_ema = None
            slow_ema = None
            st.info("💡 **Fixed Parameters:**\n- 1m: EMA 20 / 200\n- 5m: EMA 180 / 600\n- 15m: EMA 15 / 600")

    with col2:
        st.subheader("🎯 RSI Filter Options")
        use_rsi = st.toggle("Enable RSI Filter", value=False, key="rsi_toggle")
        rsi_period = st.number_input("RSI Period", min_value=2, max_value=100, value=14, key="rsi_period_val")
        
        st.markdown("""
        **RSI Logic:**
        - **BUY Signal:** Fast EMA crosses above Slow EMA **AND** RSI >= 50
        - **SELL Signal:** Fast EMA crosses below Slow EMA **AND** RSI <= 50
        """)

    st.markdown("---")

    # 2. Strategy Engine Initialization
    symbol = st.text_input("Trading Pair / Symbol", value="BTC/USDT", key="symbol_input")

    # Initialize Strategy Instance
    if EmaRsiCrossStrategy is not None:
        strategy_instance = EmaRsiCrossStrategy(
            name="EMA_RSI_Cross",
            symbol=symbol,
            timeframe=timeframe,
            mode=strat_mode,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            use_rsi_filter=use_rsi,
            rsi_period=rsi_period
        )

        st.subheader(f"📈 Signal Execution Check ({symbol} - {timeframe})")

        # Demo / Testing Button
        if st.button("Run Strategy Calculation"):
            import numpy as np
            
            dates = pd.date_range(end=pd.Timestamp.now(), periods=700, freq='5min')
            np.random.seed(42)
            close_prices = 60000 + np.random.randn(700).cumsum() * 10
            
            df = pd.DataFrame({'timestamp': dates, 'close': close_prices})

            # Calculate Signal
            signal = strategy_instance.generate_signal(df)

            # Output Display
            if signal == 'BUY':
                st.success(f"🚀 **BUY SIGNAL GENERATED** | Symbol: {symbol} | Timeframe: {timeframe}")
            elif signal == 'SELL':
                st.error(f"🔻 **SELL SIGNAL GENERATED** | Symbol: {symbol} | Timeframe: {timeframe}")
            else:
                st.warning(f"⏸️ **HOLD / NO SIGNAL** | Conditions not met on current candle.")

            # Show Current Applied Indicators Target
            fast_len = strategy_instance.fast_ema_len
            slow_len = strategy_instance.slow_ema_len
            
            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Fast EMA", f"{fast_len}")
            col_m2.metric("Slow EMA", f"{slow_len}")
            col_m3.metric("RSI Filter Status", "Active (50)" if use_rsi else "Disabled")
    else:
        st.error("⚠️ strategies/ema_rsi_strategy.py")

    st.markdown("---")
    
    # 3. Strategy Files Management Section
    STRATEGY_DIR = os.path.join(os.path.dirname(__file__), "strategies")

    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        st.markdown("### ⚡ Available Preset Strategies in Folder")
    with col_h2:
        if st.button("🔄 Scan / Refresh Folder", use_container_width=True):
            st.rerun()

    strat_files = [f for f in os.listdir(STRATEGY_DIR) if f.endswith('.py') and not f.startswith('__')]

    if strat_files:
        cols = st.columns(3)
        for idx, file_name in enumerate(strat_files):
            col_target = cols[idx % 3]
            strat_title = file_name.replace('.py', '').replace('_', ' ').upper()
            
            with col_target:
                st.markdown(f"""<div class="dashboard-card">
                    <div class="card-title">⚙️ {strat_title}</div>
                    <p style="font-size:0.8rem; color:#848e9c;">File: <code>strategies/{file_name}</code></p>
                """, unsafe_allow_html=True)
                
                if st.button(f"🚀 Link {file_name}", key=f"btn_act_{file_name}", type="primary", use_container_width=True):
                    st.session_state['active_strategy_file'] = file_name
                    add_alert(f"Activated Strategy: {file_name}", "success")
                    st.success(f"Strategy '{file_name}' Linked!")
                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.warning("No strategy files found in `strategies/` folder. Place a `.py` file inside the folder.")

    st.markdown("---")
    st.markdown("### 📂 Strategy Source Viewer")
    st.caption("Strategy files are read-only in the web application. Edit and review them through the deployment repository instead.")
    selected_file = st.selectbox("Select Strategy File to Inspect", strat_files or ["No Files Found"])
    
    if selected_file != "No Files Found":
        file_path = os.path.join(STRATEGY_DIR, selected_file)
        with open(file_path, "r", encoding="utf-8") as f:
            code_content = f.read()
            
        st.code(code_content, language="python")

# --- 10. PROFILE VIEW ---
elif nav_choice == "👤 Profile":
    st.title("My Profile")
    profile = _auth_store.get_profile(st.session_state.auth_user["id"])
    st.caption(f"Email verification: {'Verified' if profile['email_verified_at'] else 'Pending'}")
    st.write(f"**Account email:** {profile['email']}")
    st.write(f"**Member since:** {profile['created_at'][:10]}")

    with st.form("profile_details_form"):
        full_name = st.text_input("Full name", value=profile["full_name"] or "")
        username = st.text_input("Username", value=profile["username"] or "")
        mobile_number = st.text_input("Mobile number", value=profile["mobile_number"] or "")
        if st.form_submit_button("Save profile", type="primary"):
            try:
                _auth_store.update_profile(st.session_state.auth_user["id"], full_name, username, mobile_number)
                st.success("Profile updated.")
                st.rerun()
            except AuthError as exc:
                st.error(str(exc))

    st.markdown("---")
    st.subheader("Change email address")
    st.info("Changing your email requires your current password and verification of the new email address.")
    with st.form("request_email_change_form"):
        new_email = st.text_input("New email")
        current_password = st.text_input("Current password", type="password")
        if st.form_submit_button("Send verification code"):
            try:
                destination, code = _auth_store.request_email_change(
                    st.session_state.auth_user["id"], current_password, new_email
                )
                _email_service.send_verification_otp(destination, code)
                st.success("Verification code sent to the new email.")
            except (AuthError, EmailDeliveryError) as exc:
                st.error(str(exc))

    if profile["pending_email"]:
        st.warning(f"Pending email verification: {profile['pending_email']}")
        if st.button("Resend code to pending email", key="profile_resend_otp"):
            try:
                destination, code = _auth_store.resend_email_otp(profile["pending_email"])
                _email_service.send_verification_otp(destination, code)
                st.success("A new verification code was sent.")
            except (AuthError, EmailDeliveryError) as exc:
                st.error(str(exc))
        with st.form("confirm_email_change_form"):
            profile_otp = st.text_input("OTP sent to the new email", max_chars=6)
            if st.form_submit_button("Confirm new email"):
                try:
                    updated_user = _auth_store.verify_email_otp(profile["pending_email"], profile_otp)
                    st.session_state.auth_user = updated_user
                    st.success("New email verified and saved.")
                    st.rerun()
                except AuthError as exc:
                    st.error(str(exc))

# --- 11. CONNECTED WALLETS VIEW ---
elif nav_choice == "💼 Connected Wallets":
    col_w_title, col_w_ref = st.columns([3, 1])
    with col_w_title:
        st.title("💼 Connected Exchange Wallets")
    with col_w_ref:
        if st.button("🔄 Refresh Wallet Balances", use_container_width=True):
            st.rerun()

    if connected_list:
        for acc in connected_list:
            s_bal = st.session_state.wallet_eng.get_balances(acc, wallet_type="spot")
            f_bal = st.session_state.wallet_eng.get_balances(acc, wallet_type="futures")
            st.subheader(f"Account: {acc}")
            st.write(f"🛒 **Spot Balance:** ${s_bal.get('free_usdt', 0.0):,.2f} USDT")
            st.write(f"⚡ **Futures Balance:** ${f_bal.get('free_usdt', 0.0):,.2f} USDT")
    else:
        st.warning("No connected exchange accounts found. Go to 'Exchange Connections' panel to add your API keys.")

# --- 11. EXCHANGE CONNECTIONS VIEW ---
elif nav_choice == "🔑 Exchange Connections":
    st.title("🔑 API Key Manager")
    st.write("Link your exchange API credentials to trade live.")
    
    col_conn1, col_conn2 = st.columns(2)
    
    with col_conn1:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">➕ Connect New Exchange API</div>
        """, unsafe_allow_html=True)
        
        with st.form(key="api_key_form"):
            new_acc_name = st.text_input("Account Alias / Nickname", value="My_Main_Acc")
            new_exchange = st.selectbox("Exchange Platform", SUPPORTED_EXCHANGES)
            new_api_key = st.text_input("API Key", type="password", placeholder="Paste API Key here...")
            new_secret_key = st.text_input("API Secret", type="password", placeholder="Paste API Secret here...")
            new_passphrase = st.text_input("Passphrase / Password (If Required)", type="password", placeholder="Optional for Kucoin/OKX")
            
            submit_btn = st.form_submit_button("⚡ Connect & Save API", type="primary", use_container_width=True)
            
            if submit_btn:
                if new_api_key and new_secret_key:
                    if _auth_store.cipher is None:
                        st.error("Server encryption is not configured. API credentials cannot be saved safely.")
                    else:
                        res = st.session_state.exchange_mgr.connect_exchange(
                            new_acc_name, new_exchange, new_api_key, new_secret_key, new_passphrase or None
                        )
                        if res['status']:
                            try:
                                _auth_store.save_exchange_credentials(
                                    st.session_state.auth_user["id"], new_acc_name, new_exchange,
                                    new_api_key, new_secret_key, new_passphrase or None,
                                )
                                add_alert(f"Connected to {new_exchange.upper()} ({new_acc_name})", "success")
                                st.success(res['message'])
                                st.rerun()
                            except AuthError as exc:
                                st.error(str(exc))
                        else:
                            st.error(res['message'])
                else:
                    st.warning("Please provide both API Key and API Secret.")
        
        st.markdown("</div>", unsafe_allow_html=True)

    with col_conn2:
        st.markdown("""<div class="dashboard-card">
            <div class="card-title">🟢 Active Connected Accounts</div>
        """, unsafe_allow_html=True)
        
        if connected_list:
            for acc in connected_list:
                ex_id = st.session_state.exchange_mgr.connected_exchanges[acc]["exchange_id"].upper()
                c_a, c_b = st.columns([3, 1])
                c_a.info(f"**{acc}** ({ex_id}) — `CONNECTED`")
                if c_b.button("Disconnect", key=f"btn_disc_{acc}"):
                    st.session_state.exchange_mgr.disconnect_exchange(acc)
                    _auth_store.delete_exchange_credentials(st.session_state.auth_user["id"], acc)
                    st.rerun()
        else:
            st.info("No active API connections. Fill out the form on the left or use a `.env` file.")
        
        st.markdown("</div>", unsafe_allow_html=True)

# --- 12. GLOBAL SETTINGS VIEW ---
elif nav_choice == "⚙️ Settings":
    st.title("⚙️ Global Settings")
    st.write(f"App Name: **{APP_NAME}**")
    st.write(f"App Version: **{APP_VERSION}**")
    st.write("Theme: Premium Dark Mode (Default)")

    st.markdown("---")
    st.subheader("Privacy and account data")
    st.caption("Exchange trade history is held by the exchange itself. The controls below erase only UniTrade's saved data.")

    with st.expander("Clear strategy configuration"):
        st.write("Removes all saved strategies, risk settings, and auto-strategy configuration for your account.")
        if st.button("Clear my strategy data", key="clear_strategy_data", type="secondary"):
            st.session_state.auto_strategy_eng.stop()
            _auth_store.set_bot_running(st.session_state.auth_user["id"], False)
            _auth_store.clear_strategy_config(st.session_state.auth_user["id"])
            st.session_state.auto_strategy_config = None
            st.session_state.auto_strategy_wizard = {}
            add_alert("Saved strategy configuration was cleared.", "info")
            st.rerun()

    with st.expander("Clear saved exchange API credentials"):
        st.warning("This disconnects every exchange account in this browser and permanently removes its encrypted credentials from UniTrade.")
        if st.button("Clear my saved API credentials", key="clear_api_credentials", type="secondary"):
            for account_alias in list(st.session_state.exchange_mgr.connected_exchanges.keys()):
                st.session_state.exchange_mgr.disconnect_exchange(account_alias)
            _auth_store.clear_exchange_credentials(st.session_state.auth_user["id"])
            add_alert("Saved exchange credentials were cleared.", "info")
            st.rerun()

    with st.expander("Clear session activity"):
        st.write("Removes the alerts visible in this browser session. No app trade-history database is currently stored.")
        if st.button("Clear session alerts", key="clear_session_alerts"):
            st.session_state.alerts = []
            st.rerun()

    st.markdown("---")
    st.subheader("Delete account")
    st.error("This permanently deletes your login, saved strategies, and encrypted API credentials. It cannot be undone.")
    with st.form("delete_account_form"):
        deletion_password = st.text_input("Current password", type="password")
        deletion_confirmation = st.text_input("Type DELETE to confirm")
        submitted = st.form_submit_button("Permanently delete my account", type="secondary")
        if submitted:
            if deletion_confirmation != "DELETE":
                st.error("Type DELETE exactly to confirm account deletion.")
            else:
                try:
                    st.session_state.auto_strategy_eng.stop()
                    _auth_store.set_bot_running(st.session_state.auth_user["id"], False)
                    _auth_store.delete_user(st.session_state.auth_user["id"], deletion_password)
                    st.session_state.clear()
                    st.rerun()
                except AuthError as exc:
                    st.error(str(exc))
