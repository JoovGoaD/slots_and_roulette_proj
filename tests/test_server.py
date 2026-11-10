import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from casino.core.roulette import Roulette
from casino.backend.server import create_app
from casino.backend.server_store import BETTING_SECONDS, ROUND_SECONDS, ServerStore


class FixedWheel:
    def __init__(self):
        self.draws = 0

    def randrange(self, stop):
        self.draws += 1
        return 17


class ServerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.sqlite3"
        self.start = 1000 * ROUND_SECONDS
        self.now = self.start + 1
        self.rng = FixedWheel()
        self.store = ServerStore(self.path, clock=lambda: self.now, wheel=Roulette(self.rng))
        self.store.create_owner("owner", "ownerPassword1")
        self.client = TestClient(create_app(lambda: self.store))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.alice = self.register_and_login("alice")
        self.bob = self.register_and_login("bob")
        self.owner = self.login("owner", "ownerPassword1")

    def login(self, username, password):
        response = self.client.post("/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def register_and_login(self, username):
        password = "playerPassword1"
        response = self.client.post("/auth/register", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 201, response.text)
        return self.login(username, password)

    def test_owner_permissions_and_concurrent_adjustments(self):
        endpoint = "/admin/users/alice/balance"
        request = {"delta_cents": 75, "reason": "Play credit", "request_id": str(uuid4())}
        self.assertEqual(self.client.post(endpoint, json=request).status_code, 401)
        self.assertEqual(self.client.post(endpoint, headers=self.alice, json=request).status_code, 403)
        self.assertEqual(self.client.post("/auth/register", json={
            "username": "fake_owner", "password": "playerPassword1", "is_owner": True,
        }).status_code, 422)

        def adjust(index):
            body = request if index < 4 else {**request, "request_id": str(uuid4())}
            return self.client.post(endpoint, headers=self.owner, json=body)

        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(adjust, range(8)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(len({response.json()["balance_cents"] for response in responses[:4]}), 1)
        changed = self.client.post(endpoint, headers=self.owner, json={**request, "delta_cents": 1})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.store.get_totals("alice").balance_cents, 100375)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM balance_adjustments").fetchone()[0], 5)

    def test_one_shared_draw_and_separate_payouts(self):
        round_id = self.store.round_id()
        wager = {"round_id": round_id, "kind": "Straight", "label": "17", "stake_cents": 100}

        def submit(index):
            headers, body = (self.alice, wager) if index < 3 else (self.bob, {**wager, "label": "0"})
            return self.client.post("/roulette/bets", headers=headers, json=body)

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(submit, range(4)))
        self.assertTrue(all(response.status_code == 201 for response in responses))
        self.assertEqual(self.store.get_totals("alice").balance_cents, 99900)
        self.assertEqual(self.store.get_totals("bob").balance_cents, 99900)
        self.assertEqual(self.rng.draws, 0)

        self.now = self.start + BETTING_SECONDS
        closed = self.client.post("/roulette/bets", headers=self.owner, json=wager)
        self.assertEqual(closed.status_code, 409)
        self.now = self.start + ROUND_SECONDS

        # Two separate store objects model competing workers after restart.
        restarted = ServerStore(self.path, clock=lambda: self.now, wheel=Roulette(self.rng))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda store: store.advance_wheel(), (self.store, restarted)))
        self.assertEqual(self.rng.draws, 1)

        def state(headers):
            response = self.client.get(f"/roulette/state?round_id={round_id}", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        alice, bob, spectator = map(state, (self.alice, self.bob, self.owner))
        self.assertEqual(alice["history"], bob["history"])
        self.assertEqual(bob["history"], spectator["history"])
        self.assertEqual(alice["history"], [{"round_id": round_id, "number": 17}])
        self.assertEqual(alice["wager"]["number"], bob["wager"]["number"])
        self.assertEqual(alice["wager"]["payout"], 3600)
        self.assertEqual(bob["wager"]["payout"], 0)
        self.assertIsNone(spectator["wager"])
        self.assertEqual(alice["balance_cents"], 103500)
        self.assertEqual(bob["balance_cents"], 99900)

    def test_concurrent_slots_retry_and_password_change(self):
        body = {"stake_cents": 100, "request_id": str(uuid4())}

        def spin(_):
            return self.client.post("/slots/spins", headers=self.alice, json=body)

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(spin, range(4)))
        self.assertTrue(all(response.status_code == 200 for response in responses), [r.text for r in responses])
        first = responses[0].json()
        self.assertTrue(first["frames"])
        self.assertTrue(all(response.json() == first for response in responses))
        self.assertEqual(self.store.get_totals("alice").balance_cents, 99900 + first["payout_cents"])

        old_device = self.login("alice", "playerPassword1")
        changed = self.client.put("/me/password", headers=self.alice, json={
            "current": "playerPassword1", "new": "newPassword1",
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.client.get("/me", headers=old_device).status_code, 401)
        self.assertEqual(self.client.get("/me", headers=self.alice).status_code, 200)
        new_device = self.login("alice", "newPassword1")
        self.assertEqual(self.client.get("/me", headers=new_device).json()["balance_cents"],
                         first["balance_cents"])
