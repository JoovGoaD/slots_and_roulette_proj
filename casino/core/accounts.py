import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PASSWORD_ITERATIONS = 600_000
STARTING_BALANCE_CENTS = 100_000
MAX_CENTS = 2**63 - 1
ROULETTE_HISTORY_LIMIT = 10


def parse_bet_cents(value):
    message = "Enter a positive bet in whole cents."
    try:
        amount = Decimal(value)
        if not amount.is_finite() or not 0 < amount <= Decimal(MAX_CENTS) / 100:
            raise ValueError(message)
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError(message)
        return int(amount * 100)
    except InvalidOperation as error:
        raise ValueError(message) from error


@dataclass(frozen=True)
class AccountTotals:
    balance_cents: int
    deposit_cents: int
    withdrawal_cents: int


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def _validate_password(username, password):
    if len(password) < 8:
        raise ValueError("Use at least 8 characters for the password.")
    if password.strip().casefold() == username.casefold():
        raise ValueError("Choose a password that differs from your username.")
    if not any(char.isalpha() for char in password) or not any(char.isdecimal() for char in password):
        raise ValueError("Include at least one letter and one digit in the password.")


class AccountStore:
    def __init__(self, path=None):
        if path is None:
            data_dir = DATA_DIR
            if getattr(sys, "frozen", False):
                local_app_data = os.environ.get("LOCALAPPDATA")
                profile = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
                data_dir = profile / "Casino"
            path = data_dir / "accounts.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY COLLATE NOCASE NOT NULL,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL
                )
            """)
            columns = {column[1] for column in connection.execute("PRAGMA table_info(users)")}
            for name, default in (
                ("balance_cents", STARTING_BALANCE_CENTS),
                ("deposit_cents", 0),
                ("withdrawal_cents", 0),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN {name} INTEGER NOT NULL "
                        f"DEFAULT {default} CHECK ({name} >= 0)"
                    )

            if "email" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN email TEXT")

            connection.execute("""
                CREATE TABLE IF NOT EXISTS roulette_spins (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE REFERENCES users(username),
                    number INTEGER NOT NULL CHECK (number BETWEEN 0 AND 36)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS roulette_spins_by_user
                ON roulette_spins (username, id DESC)
            """)

    def get_email(self, username):
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT email FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise ValueError("Account was not found.")
        return row[0]

    def save_email(self, username, email):
        email = email.strip()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+", email):
            raise ValueError("Enter a valid email address.")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            updated = connection.execute("UPDATE users SET email = ? WHERE username = ?", (email, username))
            if updated.rowcount != 1:
                raise ValueError("Account was not found.")
        return email

    def get_totals(self, username):
        with closing(sqlite3.connect(self.path)) as connection:
            return self._read_totals(connection, username)

    @staticmethod
    def _read_totals(connection, username):
        row = connection.execute(
            "SELECT balance_cents, deposit_cents, withdrawal_cents FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            raise ValueError("Account was not found.")
        return AccountTotals(*row)

    def settle_spin(self, username, bet_cents, play):
        """Run the spin and commit its result against the latest stored balance."""
        return self._settle_spin(username, bet_cents, play)

    def settle_roulette_spin(self, username, bet_cents, play):
        """Commit the balance and winning pocket together, returning the spin result."""
        return self._settle_spin(username, bet_cents, play, roulette=True)

    def get_roulette_history(self, username):
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT number FROM roulette_spins WHERE username = ? ORDER BY id DESC LIMIT ?",
                (username, ROULETTE_HISTORY_LIMIT),
            ).fetchall()
        return tuple(number for (number,) in rows)

    def _settle_spin(self, username, bet_cents, play, *, roulette=False):
        if type(bet_cents) is not int or not 0 < bet_cents <= MAX_CENTS:
            raise ValueError("Enter a positive bet in whole cents.")

        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            balance = self._read_totals(connection, username).balance_cents
            if bet_cents > balance:
                raise ValueError("Insufficient balance for the bet.")

            result = play(balance)
            payout = result.payout_cents if roulette else result
            if type(payout) is not int or payout < 0:
                raise ValueError("Invalid spin payout.")
            updated = balance - bet_cents + payout
            if updated > MAX_CENTS:
                raise ValueError("The new balance exceeds the storage limit.")
            connection.execute(
                "UPDATE users SET balance_cents = ? WHERE username = ?",
                (updated, username),
            )
            if roulette:
                connection.execute(
                    "INSERT INTO roulette_spins (username, number) VALUES (?, ?)",
                    (username, result.number),
                )
                connection.execute("""
                    DELETE FROM roulette_spins
                    WHERE username = ? AND id NOT IN (
                        SELECT id FROM roulette_spins WHERE username = ? ORDER BY id DESC LIMIT ?
                    )
                """, (username, username, ROULETTE_HISTORY_LIMIT))
        return updated, result

    def register(self, username, password):
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            raise ValueError("Use 3-32 letters, digits, or underscores for the username.")
        _validate_password(username, password)

        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        try:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute(
                    "INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                    (username, salt, password_hash),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("That username is already registered.") from error

    def _get_credentials(self, username):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT username, salt, password_hash FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()

    def authenticate(self, username, password):
        account = self._get_credentials(username)
        if account is None:
            return None

        stored_username, salt, password_hash = account
        if hmac.compare_digest(_hash_password(password, salt), password_hash):
            return stored_username
        return None

    def change_password(self, username, current_password, new_password):
        _validate_password(username, new_password)
        account = self._get_credentials(username)
        if account is None or not hmac.compare_digest(
            _hash_password(current_password, account[1]), account[2]
        ):
            raise ValueError("Incorrect current password.")

        stored_username, old_salt, old_hash = account
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(new_password, salt)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            updated = connection.execute(
                "UPDATE users SET salt = ?, password_hash = ? "
                "WHERE username = ? AND salt = ? AND password_hash = ?",
                (salt, password_hash, stored_username, old_salt, old_hash),
            )
            if updated.rowcount != 1:
                raise ValueError("The password changed. Enter the current password and try again.")
