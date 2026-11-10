"""Desktop API adapter. Set CASINO_API_URL to use a shared casino."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

import numpy as np

from casino.core.accounts import AccountTotals
from casino.core.engine import SpecialEffect, SpinFrame


class ApiError(ValueError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RemoteAccounts:
    def __init__(self, url):
        parts = urlsplit(url.strip())
        loopback = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
                or not (parts.scheme == "https" or parts.scheme == "http" and loopback)):
            raise ValueError("Set CASINO_API_URL to an HTTPS URL or a local HTTP address.")
        self.url = url.strip().rstrip("/")
        self.token = None
        self.is_owner = False
        self.pending_slots = None
        self.pending_adjustment = None

    def request(self, method, route, body=None, *, retry=False, token=None):
        token = self.token if token is None else token
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(self.url + route, data=data, headers=headers, method=method)
        for attempt in range(2 if retry else 1):
            try:
                with build_opener(NoRedirect).open(request, timeout=5) as response:
                    return json.load(response)
            except HTTPError as error:
                with error:
                    try:
                        message = json.load(error).get("detail")
                    except (ValueError, AttributeError):
                        message = None
                failure = ApiError(
                    message if isinstance(message, str) else f"Server request failed ({error.code}).",
                    error.code,
                )
            except (URLError, OSError, ValueError):
                failure = ApiError("Could not confirm the server response. Check the connection and retry.")
            if not retry or attempt or failure.status not in (None, 502, 503, 504):
                raise failure

    def register(self, username, password):
        self.request("POST", "/auth/register", {"username": username.strip(), "password": password})

    def authenticate(self, username, password):
        try:
            response = self.request("POST", "/auth/login", {"username": username.strip(), "password": password})
        except ApiError as error:
            if error.status == 401:
                return None
            raise
        self.token = response["token"]
        return response["username"]

    def logout(self):
        token, self.token = self.token, None
        self.pending_slots = self.pending_adjustment = None
        self.is_owner = False
        if token:
            self.request("POST", "/auth/logout", token=token)

    def profile(self):
        response = self.request("GET", "/me")
        self.is_owner = response["is_owner"]
        return response

    def get_totals(self, username):
        data = self.profile()
        return AccountTotals(data["balance_cents"], data["deposit_cents"], data["withdrawal_cents"])

    def get_email(self, username):
        return self.profile()["email"]

    def save_email(self, username, email):
        return self.request("PUT", "/me/email", {"email": email})["email"]

    def change_password(self, username, current, new):
        self.request("PUT", "/me/password", {"current": current, "new": new})

    def wheel_state(self, round_id=None):
        query = "?" + urlencode({"round_id": round_id}) if round_id is not None else ""
        return self.request("GET", "/roulette/state" + query)

    def place_wager(self, round_id, kind, label, stake_cents):
        return self.request("POST", "/roulette/bets", {
            "round_id": round_id, "kind": kind, "label": label, "stake_cents": stake_cents,
        }, retry=True)

    def _financial_request(self, attr, route, body):
        pending = getattr(self, attr)
        if pending and pending[0:2] != (route, body):
            raise ApiError("Retry the previous request with the same inputs to confirm its outcome.")
        request_id = pending[2] if pending else str(uuid4())
        setattr(self, attr, (route, body, request_id))
        try:
            response = self.request("POST", route, {**body, "request_id": request_id}, retry=True)
        except ApiError as error:
            if error.status is not None and 400 <= error.status < 500:
                setattr(self, attr, None)
            raise
        setattr(self, attr, None)
        return response

    def adjust_balance(self, username, delta_cents, reason):
        return self._financial_request(
            "pending_adjustment", "/admin/users/" + quote(username, safe="") + "/balance",
            {"delta_cents": delta_cents, "reason": reason},
        )["balance_cents"]

    def spin_slots(self, stake_cents):
        data = self._financial_request("pending_slots", "/slots/spins", {"stake_cents": stake_cents})
        frames = []
        for frame in data["frames"]:
            board = np.array(frame["board"], dtype=int)
            multiplier = frame["multiplier"]
            if "position" in frame:
                frames.append(SpecialEffect(board, multiplier, frame["position"], frame["target"]))
            else:
                clusters = tuple(tuple(tuple(position) for position in group) for group in frame["clusters"])
                frames.append(SpinFrame(board, multiplier, frame["message"], clusters))
        return data["balance_cents"], data["payout_cents"], frames
