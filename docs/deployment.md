# Shared casino backend

## Deploy on a VPS with Docker Compose

From this project directory on the VPS:

    docker compose build
    docker compose run --rm api python -m casino.backend.server create-owner YourOwnerUsername
    docker compose up -d
    docker compose ps
    curl http://127.0.0.1:8000/health

The owner command prompts for the password. Run it once before exposing public
registration. The image installs locked dependencies with uv, runs as a
non-root user, and uses /var/lib/casino/accounts.sqlite3 on the named
casino_data volume. Rebuilds and container replacement reuse that volume.
Keep the same Compose project name and volume when updating. Removing the
volume with docker compose down -v deletes the database.

The API is published on the VPS loopback interface. Put an HTTPS reverse proxy
on the VPS in front of 127.0.0.1:8000 with a trusted certificate. Rate-limit
/auth/login and /auth/register at the proxy. Restrict /docs and /openapi.json
there if the API reference should be private. CASINO_PORT overrides the
published host port if 8000 is occupied. The container listens on port 8000.

### Import an existing account database

To retain the accounts and balances from the original desktop app, create a
consistent SQLite backup of data/accounts.sqlite3 and transfer it privately
to the VPS. Before creating the owner or starting the service, import it from
the VPS shell:

    docker compose run --rm -T api python -c "import os, shutil, sys; f = open(os.environ['CASINO_DB'], 'xb'); shutil.copyfileobj(sys.stdin.buffer, f); f.close()" < accounts-backup.sqlite3

The command refuses to overwrite an existing database. Run create-owner
afterward and enter the imported account's current password if assigning it
the owner role. A fresh database starts with no player accounts. Shared
roulette starts a new wheel history. Imported personal history stays in
the database. Back up the volume with SQLite's backup API or while the
service is stopped. Keep the database on local VPS storage.

## Connect desktop clients

With uv installed, from the project directory in PowerShell:

    $env:CASINO_API_URL = "https://casino.example.com"
    uv run --locked --extra desktop python main.py

You can also set CASINO_API_URL in the PyCharm run configuration and launch
main.py using the existing desktop environment. Use the same URL on each
machine. Without the variable, main.py uses its original local mode and
local database. HTTP is accepted only for a loopback URL during development.

The owner signs in through the normal login screen and gets **Adjust balance**
on the Account page. Enter the target username, a signed dollar amount, and
a reason. Adjustments are audited. Players can update their own email and
password and place wagers from their own balance. Only the owner can make a
manual credit or debit. There is no HTTP route to grant the owner role.

The API reference is at /docs. POST /auth/login returns a bearer token that
expires after 12 hours. A password change revokes other sessions and retains
the current session.

## Run and test without Docker

    uv run --locked --extra server python -m casino.backend.server create-owner YourOwnerUsername
    uv run --locked --extra server uvicorn casino.backend.server:app --host 127.0.0.1 --port 8000
    uv run --locked --extra server --group test python -m unittest discover -s tests -v

Run the test command in a separate terminal. For the desktop client set
CASINO_API_URL to http://127.0.0.1:8000. A bare server creates
server_data/accounts.sqlite3 relative to its working directory unless
CASINO_DB is set.

## Shared roulette and concurrency

A round lasts 18 seconds. Players have the first 12 seconds to submit one
wager per account. Acceptance deducts the stake. At the end of the round the
server draws one pocket independently of any wager or account. It records
the pocket and credits all payouts in the same transaction. All players see
the same last 10 wheel results. Each player sees only their own bet and
payout. Pending wagers survive a restart and are settled from the database.

A repeat of the same roulette bet for an account and round returns the
original acceptance. Slots spins and owner adjustments use UUID request IDs.
Repeat the same request and ID after a lost response to get the original
result. Reusing an ID for different inputs returns 409.

The server uses separate SQLite connections, WAL, a five-second busy timeout,
and BEGIN IMMEDIATE for writes. Readers can continue during writes, while
SQLite serializes the writers. Unique keys and transactions keep concurrent
workers from charging a repeated wager or settling a round twice. One VPS
with moderate traffic is the intended configuration.
