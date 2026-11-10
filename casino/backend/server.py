"""Central FastAPI service. Start with uvicorn casino.backend.server:app."""

import argparse
import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from getpass import getpass
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from casino.core.accounts import MAX_CENTS
from casino.backend.server_store import Conflict, Forbidden, Missing, ServerStore

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def database_path():
    return Path(os.environ.get("CASINO_DB", "server_data/accounts.sqlite3")).resolve()


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Body):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class Email(Body):
    email: str = Field(max_length=254)


class Password(Body):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


class SlotsSpin(Body):
    stake_cents: int = Field(strict=True, gt=0, le=MAX_CENTS)
    request_id: UUID


class RouletteWager(Body):
    round_id: int = Field(strict=True, ge=0, le=MAX_CENTS)
    kind: str = Field(max_length=32)
    label: str = Field(max_length=64)
    stake_cents: int = Field(strict=True, gt=0, le=MAX_CENTS)


class Adjustment(Body):
    delta_cents: int = Field(strict=True, ge=-MAX_CENTS, le=MAX_CENTS)
    reason: str = Field(min_length=1, max_length=250)
    request_id: UUID


def create_app(store_factory=None):
    @asynccontextmanager
    async def lifespan(api):
        store = store_factory() if store_factory else ServerStore(database_path())
        api.state.store = store
        stop = asyncio.Event()

        async def wheel_worker():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(store.advance_wheel)
                except sqlite3.Error:
                    log.exception("Could not advance the roulette wheel")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

        worker = asyncio.create_task(wheel_worker())
        try:
            yield
        finally:
            stop.set()
            await worker

    api = FastAPI(title="Casino API", lifespan=lifespan)

    def store(request: Request):
        return request.app.state.store

    def current_user(
        db: Annotated[ServerStore, Depends(store)],
        auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        user = db.identity(auth.credentials) if auth else None
        if user is None:
            raise HTTPException(401, "Sign in again.", headers={"WWW-Authenticate": "Bearer"})
        return user

    Store = Annotated[ServerStore, Depends(store)]
    User = Annotated[sqlite3.Row, Depends(current_user)]

    @api.exception_handler(RequestValidationError)
    async def invalid_body(request, error):
        fields = [".".join(map(str, issue["loc"])) for issue in error.errors()]
        return JSONResponse({"detail": "Invalid request fields: " + ", ".join(fields)}, status_code=422)

    @api.exception_handler(ValueError)
    async def invalid(request, error):
        status = 409 if isinstance(error, Conflict) else 404 if isinstance(error, Missing) else 403 if isinstance(error, Forbidden) else 400
        return JSONResponse({"detail": str(error)}, status_code=status)

    @api.exception_handler(sqlite3.Error)
    async def database_error(request, error):
        log.exception("Database request failed", exc_info=error)
        return JSONResponse({"detail": "Database unavailable. Retry shortly."}, status_code=503,
                            headers={"Retry-After": "1"})

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.post("/auth/register", status_code=201)
    def register(body: Credentials, db: Store):
        db.register(body.username, body.password)
        return {"created": True}

    @api.post("/auth/login")
    def login(body: Credentials, db: Store):
        session = db.login(body.username, body.password)
        if session is None:
            raise HTTPException(401, "Incorrect username or password.")
        return session

    @api.post("/auth/logout")
    def logout(db: Store, auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if auth:
            db.logout(auth.credentials)
        return {"signed_out": True}

    @api.get("/me")
    def profile(db: Store, user: User):
        name = user["username"]
        return {"username": name, "is_owner": bool(user["is_owner"]), "email": db.get_email(name),
                **asdict(db.get_totals(name))}

    @api.put("/me/email")
    def email(body: Email, db: Store, user: User):
        return {"email": db.save_email(user["username"], body.email)}

    @api.put("/me/password")
    def password(body: Password, db: Store, user: User,
                 auth: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
        db.change_password(user["username"], body.current, body.new, token=auth.credentials)
        return {"changed": True}

    @api.post("/slots/spins")
    def slots(body: SlotsSpin, db: Store, user: User):
        return db.spin_slots(user["username"], body.stake_cents, str(body.request_id))

    @api.get("/roulette/state")
    def wheel(db: Store, user: User, round_id: int | None = None):
        if round_id is not None and not 0 <= round_id <= MAX_CENTS:
            raise ValueError("Invalid round ID.")
        return db.wheel_state(user["username"], round_id)

    @api.post("/roulette/bets", status_code=201)
    def wager(body: RouletteWager, db: Store, user: User):
        return db.place_wager(user["username"], body.round_id, body.kind, body.label, body.stake_cents)

    @api.post("/admin/users/{username}/balance")
    def adjust(username: str, body: Adjustment, db: Store, user: User):
        if not user["is_owner"]:
            raise HTTPException(403, "Only the owner can adjust balances.")
        if not body.delta_cents or not body.reason.strip():
            raise ValueError("Enter a nonzero adjustment and a reason.")
        return db.adjust_balance(user["username"], username, body.delta_cents, body.reason.strip(),
                                 str(body.request_id))

    return api


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local casino server maintenance")
    parser.add_argument("command", choices=["create-owner"])
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass("Owner password: ")
    if password != getpass("Repeat password: "):
        parser.error("Passwords differ.")
    try:
        ServerStore(database_path()).create_owner(args.username, password)
    except (ValueError, sqlite3.IntegrityError) as error:
        parser.error(str(error))
    print("Owner account created.")
