"""Private per-user storage for UniTrade's Streamlit deployment.

Passwords are salted and hashed with scrypt. Exchange credentials are encrypted
with a server-only Fernet key; they are never written to a shared .env file.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class _PostgresConnection:
    """Small compatibility layer for the existing parameterized store queries."""
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            self._connection.rollback()
        else:
            self._connection.commit()
        self._connection.close()

    def execute(self, query, params=None):
        # All project queries use positional SQLite placeholders. Values are
        # always supplied separately, so this conversion remains parameterized.
        return self._connection.execute(query.replace("?", "%s"), params or ())


class AuthError(ValueError):
    pass


class UserStore:
    def __init__(self, database_path: str, encryption_key: str | None = None):
        self.database_path = database_path
        self.is_postgres = database_path.startswith(("postgres://", "postgresql://"))
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL requires psycopg. Install requirements.txt first.") from exc
            self._psycopg = psycopg
            self._dict_row = dict_row
            self._integrity_error = psycopg.IntegrityError
        else:
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
            self._integrity_error = sqlite3.IntegrityError
        self.cipher = Fernet(encryption_key.encode()) if encryption_key else None
        self._create_schema()

    def _connection(self):
        if self.is_postgres:
            return _PostgresConnection(self._psycopg.connect(self.database_path, row_factory=self._dict_row))
        # A Streamlit app can serve several browser sessions at once.  WAL and
        # a bounded wait greatly reduce transient "database is locked" errors
        # for the supported single-instance SQLite deployment.
        conn = sqlite3.connect(self.database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _create_schema(self):
        if self.is_postgres:
            self._create_postgres_schema()
            return
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    agreement_version TEXT NOT NULL,
                    agreed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_configs (
                    user_id INTEGER PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS exchange_credentials (
                    user_id INTEGER NOT NULL,
                    account_alias TEXT NOT NULL,
                    exchange_id TEXT NOT NULL,
                    encrypted_payload BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, account_alias),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS bot_states (
                    user_id INTEGER PRIMARY KEY,
                    is_running INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS strategy_runtime (
                    user_id INTEGER NOT NULL,
                    strategy_id TEXT NOT NULL,
                    is_open INTEGER NOT NULL DEFAULT 0,
                    last_candle_at INTEGER,
                    last_signal TEXT,
                    status TEXT NOT NULL DEFAULT 'Stopped',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, strategy_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)
            self._ensure_column(conn, "users", "full_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "users", "username", "TEXT")
            self._ensure_column(conn, "users", "mobile_number", "TEXT NOT NULL DEFAULT ''")
            email_verification_column_added = self._ensure_column(conn, "users", "email_verified_at", "TEXT")
            self._ensure_column(conn, "users", "pending_email", "TEXT")
            self._ensure_column(conn, "users", "otp_hash", "TEXT")
            self._ensure_column(conn, "users", "otp_expires_at", "TEXT")
            self._ensure_column(conn, "users", "otp_attempts", "INTEGER NOT NULL DEFAULT 0")
            # Accounts created before OTP support remain usable; only newly
            # created accounts must complete the new verification step.
            if email_verification_column_added:
                conn.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username) WHERE username IS NOT NULL")

    def _create_postgres_schema(self):
        """Schema for a durable PostgreSQL deployment on the VPS."""
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    email TEXT NOT NULL,
                    password_salt BYTEA NOT NULL,
                    password_hash BYTEA NOT NULL,
                    agreement_version TEXT NOT NULL,
                    agreed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    full_name TEXT NOT NULL DEFAULT '',
                    username TEXT,
                    mobile_number TEXT NOT NULL DEFAULT '',
                    email_verified_at TEXT,
                    pending_email TEXT,
                    otp_hash TEXT,
                    otp_expires_at TEXT,
                    otp_attempts INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(LOWER(email))")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username) WHERE username IS NOT NULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_configs (
                    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exchange_credentials (
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    account_alias TEXT NOT NULL,
                    exchange_id TEXT NOT NULL,
                    encrypted_payload BYTEA NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, account_alias)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_states (
                    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    is_running INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_runtime (
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    strategy_id TEXT NOT NULL,
                    is_open INTEGER NOT NULL DEFAULT 0,
                    last_candle_at BIGINT,
                    last_signal TEXT,
                    status TEXT NOT NULL DEFAULT 'Stopped',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, strategy_id)
                )
            """)

    @staticmethod
    def _ensure_column(conn, table: str, column: str, definition: str):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return True
        return False

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)

    @staticmethod
    def _validate_email(email: str) -> str:
        email = email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise AuthError("Enter a valid email address.")
        return email

    @staticmethod
    def _validate_password(password: str):
        if len(password) < 12:
            raise AuthError("Password must contain at least 12 characters.")

    @staticmethod
    def _validate_username(username: str) -> str | None:
        username = username.strip()
        if not username:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_]{3,30}", username):
            raise AuthError("Username must be 3-30 characters: letters, numbers, or underscore.")
        return username

    @staticmethod
    def _validate_mobile(mobile_number: str) -> str:
        mobile_number = mobile_number.strip()
        if mobile_number and not re.fullmatch(r"[+0-9()\- ]{7,25}", mobile_number):
            raise AuthError("Enter a valid mobile number or leave it blank.")
        return mobile_number

    def create_user(self, email: str, password: str, accepted_agreement: bool, full_name="", username="", mobile_number="",
                    agreement_version="2026-08-18", require_email_verification: bool = True) -> dict:
        email = self._validate_email(email)
        self._validate_password(password)
        username = self._validate_username(username)
        mobile_number = self._validate_mobile(mobile_number)
        if not accepted_agreement:
            raise AuthError("You must accept the trading-risk and API-security agreement.")
        salt = secrets.token_bytes(16)
        now = datetime.now(timezone.utc).isoformat()
        verified_at = None if require_email_verification else now
        try:
            with self._connection() as conn:
                query = "INSERT INTO users(email, password_salt, password_hash, agreement_version, agreed_at, created_at, full_name, username, mobile_number, email_verified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                if self.is_postgres:
                    query += " RETURNING id"
                cursor = conn.execute(
                    query,
                    (email, salt, self._hash_password(password, salt), agreement_version, now, now, full_name.strip(), username, mobile_number, verified_at),
                )
                user_id = cursor.fetchone()["id"] if self.is_postgres else cursor.lastrowid
                return {"id": user_id, "email": email}
        except self._integrity_error as exc:
            raise AuthError("This email or username is already registered.") from exc

    def authenticate(self, email: str, password: str, allow_unverified: bool = False) -> dict:
        email = self._validate_email(email)
        with self._connection() as conn:
            row = conn.execute("SELECT id, email, password_salt, password_hash, email_verified_at FROM users WHERE email = ?", (email,)).fetchone()
        if row is None or not hmac.compare_digest(self._hash_password(password, row["password_salt"]), row["password_hash"]):
            raise AuthError("Invalid email or password.")
        if not row["email_verified_at"] and not allow_unverified:
            raise AuthError("Verify your email with the OTP before logging in.")
        if not row["email_verified_at"] and allow_unverified:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE users SET email_verified_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
        return {"id": row["id"], "email": row["email"]}

    def get_profile(self, user_id: int) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT id, email, full_name, username, mobile_number, email_verified_at, pending_email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AuthError("Account not found.")
        return dict(row)

    def update_profile(self, user_id: int, full_name: str, username: str, mobile_number: str):
        username = self._validate_username(username)
        mobile_number = self._validate_mobile(mobile_number)
        try:
            with self._connection() as conn:
                conn.execute("UPDATE users SET full_name = ?, username = ?, mobile_number = ? WHERE id = ?", (full_name.strip(), username, mobile_number, user_id))
        except self._integrity_error as exc:
            raise AuthError("That username is already in use.") from exc

    @staticmethod
    def _otp_hash(user_id: int, code: str) -> str:
        return hashlib.sha256(f"{user_id}:{code}".encode()).hexdigest()

    def issue_email_otp(self, user_id: int) -> tuple[str, str]:
        """Create a short-lived verification code, returning (destination, code)."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = datetime.now(timezone.utc).timestamp() + 15 * 60
        with self._connection() as conn:
            row = conn.execute("SELECT email, pending_email FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthError("Account not found.")
            destination = row["pending_email"] or row["email"]
            conn.execute("UPDATE users SET otp_hash = ?, otp_expires_at = ?, otp_attempts = 0 WHERE id = ?", (self._otp_hash(user_id, code), str(expires_at), user_id))
        return destination, code

    def verify_email_otp(self, email: str, code: str) -> dict:
        email = self._validate_email(email)
        with self._connection() as conn:
            row = conn.execute("SELECT id, email, pending_email, otp_hash, otp_expires_at, otp_attempts FROM users WHERE email = ? OR pending_email = ?", (email, email)).fetchone()
            if row is None:
                raise AuthError("No pending verification was found for this email.")
            if int(row["otp_attempts"] or 0) >= 5:
                raise AuthError("Too many incorrect codes. Request a new OTP.")
            if not row["otp_expires_at"] or datetime.now(timezone.utc).timestamp() > float(row["otp_expires_at"]):
                raise AuthError("This OTP has expired. Request a new one.")
            if not hmac.compare_digest(row["otp_hash"] or "", self._otp_hash(row["id"], code.strip())):
                conn.execute("UPDATE users SET otp_attempts = otp_attempts + 1 WHERE id = ?", (row["id"],))
                raise AuthError("Incorrect OTP.")
            verified_email = row["pending_email"] or row["email"]
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE users SET email = ?, pending_email = NULL, email_verified_at = ?, otp_hash = NULL, otp_expires_at = NULL, otp_attempts = 0 WHERE id = ?", (verified_email, now, row["id"]))
        return {"id": row["id"], "email": verified_email}

    def resend_email_otp(self, email: str) -> tuple[str, str]:
        email = self._validate_email(email)
        with self._connection() as conn:
            row = conn.execute("SELECT id FROM users WHERE email = ? OR pending_email = ?", (email, email)).fetchone()
        if row is None:
            raise AuthError("No pending verification was found for this email.")
        return self.issue_email_otp(row["id"])

    def request_email_change(self, user_id: int, password: str, new_email: str) -> tuple[str, str]:
        new_email = self._validate_email(new_email)
        with self._connection() as conn:
            row = conn.execute("SELECT password_salt, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None or not hmac.compare_digest(self._hash_password(password, row["password_salt"]), row["password_hash"]):
                raise AuthError("Password confirmation failed.")
            conn.execute("UPDATE users SET pending_email = ?, email_verified_at = NULL WHERE id = ?", (new_email, user_id))
        return self.issue_email_otp(user_id)

    def load_strategy_config(self, user_id: int):
        with self._connection() as conn:
            row = conn.execute("SELECT config_json FROM strategy_configs WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(row["config_json"]) if row else None

    def save_strategy_config(self, user_id: int, config: dict):
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(config)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO strategy_configs(user_id, config_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET config_json=excluded.config_json, updated_at=excluded.updated_at",
                (user_id, payload, now),
            )

    def save_exchange_credentials(self, user_id: int, alias: str, exchange_id: str, api_key: str, api_secret: str, passphrase: str | None):
        if self.cipher is None:
            raise AuthError("Server encryption is not configured. Set APP_ENCRYPTION_KEY in Streamlit secrets first.")
        payload = json.dumps({"api_key": api_key, "api_secret": api_secret, "passphrase": passphrase})
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO exchange_credentials(user_id, account_alias, exchange_id, encrypted_payload, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, account_alias) DO UPDATE SET exchange_id=excluded.exchange_id, encrypted_payload=excluded.encrypted_payload, updated_at=excluded.updated_at",
                (user_id, alias, exchange_id, self.cipher.encrypt(payload.encode()), now),
            )

    def load_exchange_credentials(self, user_id: int):
        if self.cipher is None:
            return []
        with self._connection() as conn:
            rows = conn.execute("SELECT account_alias, exchange_id, encrypted_payload FROM exchange_credentials WHERE user_id = ?", (user_id,)).fetchall()
        result = []
        for row in rows:
            try:
                result.append({"alias": row["account_alias"], "exchange_id": row["exchange_id"], **json.loads(self.cipher.decrypt(row["encrypted_payload"]).decode())})
            except (InvalidToken, json.JSONDecodeError):
                continue
        return result

    def delete_exchange_credentials(self, user_id: int, alias: str):
        with self._connection() as conn:
            conn.execute("DELETE FROM exchange_credentials WHERE user_id = ? AND account_alias = ?", (user_id, alias))

    def clear_exchange_credentials(self, user_id: int):
        with self._connection() as conn:
            conn.execute("DELETE FROM exchange_credentials WHERE user_id = ?", (user_id,))

    def clear_strategy_config(self, user_id: int):
        with self._connection() as conn:
            conn.execute("DELETE FROM strategy_configs WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM strategy_runtime WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM bot_states WHERE user_id = ?", (user_id,))

    def set_bot_running(self, user_id: int, is_running: bool):
        """Persist bot state so a separate worker survives UI disconnects."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO bot_states(user_id, is_running, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET is_running=excluded.is_running, updated_at=excluded.updated_at",
                (user_id, int(is_running), now),
            )

    def is_bot_running(self, user_id: int) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT is_running FROM bot_states WHERE user_id = ?", (user_id,)).fetchone()
        return bool(row and row["is_running"])

    def running_bot_configurations(self) -> list[dict]:
        """Return only enabled users for the standalone worker process."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT b.user_id, s.config_json FROM bot_states b "
                "JOIN strategy_configs s ON s.user_id = b.user_id WHERE b.is_running = 1"
            ).fetchall()
        return [{"user_id": row["user_id"], "config": json.loads(row["config_json"])} for row in rows]

    def runtime_for(self, user_id: int, strategy_id: str) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT is_open, last_candle_at, last_signal, status FROM strategy_runtime "
                "WHERE user_id = ? AND strategy_id = ?", (user_id, strategy_id)
            ).fetchone()
        return dict(row) if row else {"is_open": 0, "last_candle_at": None, "last_signal": None, "status": "Waiting for candle"}

    def save_runtime(self, user_id: int, strategy_id: str, *, is_open: bool, last_candle_at: int | None,
                     last_signal: str | None, status: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO strategy_runtime(user_id, strategy_id, is_open, last_candle_at, last_signal, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, strategy_id) DO UPDATE SET "
                "is_open=excluded.is_open, last_candle_at=excluded.last_candle_at, last_signal=excluded.last_signal, "
                "status=excluded.status, updated_at=excluded.updated_at",
                (user_id, strategy_id, int(is_open), last_candle_at, last_signal, status, now),
            )

    def delete_user(self, user_id: int, password: str):
        """Permanently erase the user's app data after password confirmation."""
        with self._connection() as conn:
            row = conn.execute("SELECT password_salt, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None or not hmac.compare_digest(self._hash_password(password, row["password_salt"]), row["password_hash"]):
                raise AuthError("Password confirmation failed.")
            # Explicit deletes avoid relying on SQLite's optional foreign-key pragma.
            conn.execute("DELETE FROM exchange_credentials WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM strategy_configs WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM strategy_runtime WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM bot_states WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode()
