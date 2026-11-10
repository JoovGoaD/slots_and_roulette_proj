"""SQLite storage for the central casino server."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing

from casino.core.accounts import AccountStore, MAX_CENTS, _hash_password, _validate_password
from casino.core.engine import Casino, SpecialEffect
from casino.core.roulette import Roulette

ROUND_SECONDS = 18
BETTING_SECONDS = 12
SESSION_SECONDS = 12 * 60 * 60
WHEEL_HISTORY_LIMIT = 10


class Conflict(ValueError):
    pass


class Missing(ValueError):
    pass


class Forbidden(ValueError):
    pass


class ServerStore(AccountStore):
    def __init__(self, path, *, clock=time.time, wheel=None):
        super().__init__(path)
        self.clock = clock
        self.wheel = Roulette() if wheel is None else wheel
        self.bets = {
            (kind, bet.label): bet for kind in self.wheel.bet_types for bet in self.wheel.bets_for(kind)
        }
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS server_owner (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    username TEXT NOT NULL REFERENCES users(username)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL REFERENCES users(username),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wheel_settings (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    first_round INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wheel_results (
                    round_id INTEGER PRIMARY KEY,
                    number INTEGER NOT NULL CHECK (number BETWEEN 0 AND 36)
                );
                CREATE TABLE IF NOT EXISTS wheel_bets (
                    username TEXT NOT NULL COLLATE NOCASE REFERENCES users(username),
                    round_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    max_payout INTEGER NOT NULL,
                    payout INTEGER,
                    PRIMARY KEY (username, round_id)
                );
                CREATE INDEX IF NOT EXISTS pending_wheel_bets
                    ON wheel_bets(round_id) WHERE payout IS NULL;
                CREATE TABLE IF NOT EXISTS server_operations (
                    username TEXT NOT NULL REFERENCES users(username),
                    request_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    response TEXT NOT NULL,
                    PRIMARY KEY (username, request_id)
                );
                CREATE TABLE IF NOT EXISTS balance_adjustments (
                    id INTEGER PRIMARY KEY,
                    owner TEXT NOT NULL REFERENCES users(username),
                    username TEXT NOT NULL REFERENCES users(username),
                    delta_cents INTEGER NOT NULL,
                    balance_cents INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
            """)
            with db:
                db.execute("INSERT OR IGNORE INTO wheel_settings VALUES (1, ?)", (self.round_id(),))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def round_id(self, now=None):
        return int(self.clock() if now is None else now) // ROUND_SECONDS

    def create_owner(self, username, password):
        """Local maintenance command, never exposed as an HTTP endpoint."""
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            raise ValueError("Use 3-32 letters, digits, or underscores for the username.")
        _validate_password(username, password)
        existing = self._get_credentials(username)
        if existing is None:
            salt = secrets.token_bytes(16)
            password_hash = _hash_password(password, salt)
        else:
            username, salt, password_hash = existing
            if not hmac.compare_digest(_hash_password(password, salt), password_hash):
                raise ValueError("Enter the existing account's password.")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM server_owner").fetchone():
                raise Conflict("The owner is already configured.")
            if existing is None:
                db.execute("INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                           (username, salt, password_hash))
            elif not db.execute(
                "SELECT 1 FROM users WHERE username = ? AND salt = ? AND password_hash = ?",
                (username, salt, password_hash),
            ).fetchone():
                raise Conflict("The account changed. Try again.")
            db.execute("INSERT INTO server_owner VALUES (1, ?)", (username,))

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def login(self, username, password):
        credentials = self._get_credentials(username)
        salt = credentials[1] if credentials else bytes(16)
        candidate = _hash_password(password, salt)
        if credentials is None or not hmac.compare_digest(candidate, credentials[2]):
            return None
        username, salt, password_hash = credentials
        token = secrets.token_urlsafe(32)
        now = int(self.clock())
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            inserted = db.execute("""
                INSERT INTO sessions (token_hash, username, expires)
                SELECT ?, username, ? FROM users
                WHERE username = ? AND salt = ? AND password_hash = ?
            """, (self.token_hash(token), now + SESSION_SECONDS, username, salt, password_hash))
            if inserted.rowcount != 1:
                return None
        return {"token": token, "username": username, "expires": now + SESSION_SECONDS}

    def identity(self, token):
        if len(token) > 128:
            return None
        with closing(self.connect()) as db:
            return db.execute("""
                SELECT sessions.username, EXISTS (
                    SELECT 1 FROM server_owner WHERE server_owner.username = sessions.username
                ) AS is_owner
                FROM sessions WHERE token_hash = ? AND expires > ?
            """, (self.token_hash(token), int(self.clock()))).fetchone()

    def logout(self, token):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(token),))

    def change_password(self, username, current_password, new_password, *, token=None):
        _validate_password(username, new_password)
        credentials = self._get_credentials(username)
        if credentials is None or not hmac.compare_digest(
            _hash_password(current_password, credentials[1]), credentials[2]
        ):
            raise ValueError("Incorrect current password.")
        _, old_salt, old_hash = credentials
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(new_password, salt)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute("""
                UPDATE users SET salt = ?, password_hash = ?
                WHERE username = ? AND salt = ? AND password_hash = ?
            """, (salt, password_hash, username, old_salt, old_hash))
            if updated.rowcount != 1:
                raise Conflict("The password changed. Sign in and try again.")
            db.execute("DELETE FROM sessions WHERE username = ? AND token_hash <> ?",
                       (username, self.token_hash(token) if token else ""))

    def advance_wheel(self):
        with closing(self.connect()) as db, db:
            active_round = self.round_id()
            latest_due = db.execute("""
                SELECT first_round <= ? AND NOT EXISTS (
                    SELECT 1 FROM wheel_results WHERE round_id = ?
                ) FROM wheel_settings
            """, (active_round - 1, active_round - 1)).fetchone()[0]
            if latest_due:
                db.execute("BEGIN IMMEDIATE")
                self._advance_wheel(db, self.round_id())

    def _advance_wheel(self, db, active_round):
        first = db.execute("SELECT first_round FROM wheel_settings").fetchone()[0]
        oldest = max(first, active_round - WHEEL_HISTORY_LIMIT)
        pending = db.execute("""
            SELECT DISTINCT round_id FROM wheel_bets WHERE payout IS NULL AND round_id < ?
        """, (active_round,)).fetchall()
        rounds = set(range(oldest, active_round)) | {row[0] for row in pending}
        for round_id in sorted(rounds):
            saved = db.execute("SELECT number FROM wheel_results WHERE round_id = ?", (round_id,)).fetchone()
            if saved is None:
                number = self.wheel.draw()
                db.execute("INSERT INTO wheel_results VALUES (?, ?)", (round_id, number))
            else:
                number = saved[0]
            bets = db.execute("""
                SELECT * FROM wheel_bets WHERE round_id = ? AND payout IS NULL
            """, (round_id,)).fetchall()
            for wager in bets:
                bet = self.bets[wager["kind"], wager["label"]]
                payout = self.wheel.result_for(number, bet, wager["stake"]).payout_cents
                db.execute("UPDATE users SET balance_cents = balance_cents + ? WHERE username = ?",
                           (payout, wager["username"]))
                db.execute("UPDATE wheel_bets SET payout = ? WHERE username = ? AND round_id = ?",
                           (payout, wager["username"], round_id))

    def wheel_state(self, username, requested_round=None):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            now = self.clock()
            round_id = self.round_id(now)
            starts = round_id * ROUND_SECONDS
            history = db.execute("""
                SELECT round_id, number FROM wheel_results ORDER BY round_id DESC LIMIT ?
            """, (WHEEL_HISTORY_LIMIT,)).fetchall()
            wager = db.execute("""
                SELECT b.round_id, b.kind, b.label, b.stake, b.payout, r.number
                FROM wheel_bets b LEFT JOIN wheel_results r USING (round_id)
                WHERE b.username = ? AND (? IS NULL OR b.round_id = ?)
                ORDER BY b.round_id DESC LIMIT 1
            """, (username, requested_round, requested_round)).fetchone()
            return {
                "round_id": round_id, "server_time": now, "bets_close": starts + BETTING_SECONDS,
                "reveals_at": starts + ROUND_SECONDS, "history": [dict(row) for row in history],
                "balance_cents": self._read_totals(db, username).balance_cents,
                "wager": dict(wager) if wager else None,
            }

    def place_wager(self, username, round_id, kind, label, stake):
        bet = self.bets.get((kind, label))
        if bet is None:
            raise ValueError("Choose a valid roulette bet.")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("""
                SELECT kind, label, stake FROM wheel_bets WHERE username = ? AND round_id = ?
            """, (username, round_id)).fetchone()
            if previous is not None:
                if tuple(previous) != (kind, label, stake):
                    raise Conflict("You already have a different bet in this round.")
                return {"round_id": round_id}
            now = self.clock()
            active_round = self.round_id(now)
            if (round_id != active_round or now >= round_id * ROUND_SECONDS + BETTING_SECONDS
                    or db.execute("SELECT 1 FROM wheel_results WHERE round_id = ?", (round_id,)).fetchone()):
                raise Conflict("Betting is closed for that round.")
            self._advance_wheel(db, active_round)
            balance = self._read_totals(db, username).balance_cents
            if stake > balance:
                raise ValueError("Insufficient balance for the bet.")
            max_payout = stake * (bet.odds + 1)
            self._check_balance(balance - stake, max_payout)
            db.execute("UPDATE users SET balance_cents = balance_cents - ? WHERE username = ?",
                       (stake, username))
            db.execute("""
                INSERT INTO wheel_bets (username, round_id, kind, label, stake, max_payout)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, round_id, kind, label, stake, max_payout))
        return {"round_id": round_id}

    @staticmethod
    def _check_balance(balance, reserved=0):
        if balance < 0:
            raise ValueError("Balance cannot be negative.")
        if balance + reserved > MAX_CENTS:
            raise ValueError("Balance would exceed the storage limit, including pending winnings.")

    @staticmethod
    def _reserved(db, username):
        return sum(int(row[0]) for row in db.execute(
            "SELECT max_payout FROM wheel_bets WHERE username = ? AND payout IS NULL", (username,)
        ))

    def _operation(self, actor, request_id, payload, action):
        payload = json.dumps(payload, sort_keys=True)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("""
                SELECT payload, response FROM server_operations WHERE username = ? AND request_id = ?
            """, (actor, request_id)).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise Conflict("Request ID was already used for different inputs.")
                return json.loads(previous["response"])
            response = action(db)
            db.execute("INSERT INTO server_operations VALUES (?, ?, ?, ?)",
                       (actor, request_id, payload, json.dumps(response, separators=(",", ":"))))
        return response

    def adjust_balance(self, actor, username, delta, reason, request_id):
        def apply(db):
            if not db.execute("SELECT 1 FROM server_owner WHERE username = ?", (actor,)).fetchone():
                raise Forbidden("Only the owner can adjust balances.")
            self._advance_wheel(db, self.round_id())
            try:
                balance = self._read_totals(db, username).balance_cents + delta
            except ValueError as error:
                raise Missing(str(error)) from error
            self._check_balance(balance, self._reserved(db, username))
            db.execute("UPDATE users SET balance_cents = ? WHERE username = ?", (balance, username))
            db.execute("""
                INSERT INTO balance_adjustments (owner, username, delta_cents, balance_cents, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (actor, username, delta, balance, reason, int(self.clock())))
            return {"balance_cents": balance}

        return self._operation(actor, request_id, ["adjust", username, delta, reason], apply)

    def spin_slots(self, username, stake, request_id):
        def apply(db):
            self._advance_wheel(db, self.round_id())
            balance = self._read_totals(db, username).balance_cents
            if stake > balance:
                raise ValueError("Insufficient balance for the bet.")
            frames = []
            game = Casino.Game()
            payout = round(game.roll(Casino.Player(balance / 100, stake / 100), record_frame=frames.append) * 100)
            balance = balance - stake + payout
            self._check_balance(balance, self._reserved(db, username))
            db.execute("UPDATE users SET balance_cents = ? WHERE username = ?", (balance, username))
            return {
                "balance_cents": balance, "payout_cents": payout,
                "frames": [
                    {"board": frame.board.tolist(), "multiplier": frame.multiplier,
                     **({"position": tuple(map(int, frame.position)),
                         "target": None if frame.target is None else tuple(map(int, frame.target))}
                        if isinstance(frame, SpecialEffect)
                        else {"message": frame.message, "clusters": frame.clusters})}
                    for frame in frames
                ],
            }

        return self._operation(username, request_id, ["slots", stake], apply)
