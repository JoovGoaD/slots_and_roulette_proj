# Casino

Casino is a Python desktop app with slots and roulette. The desktop app can
play locally or connect to a shared FastAPI backend. Docker Compose runs the
backend on a VPS.

```text
casino/
├── casino/
│   ├── backend/       FastAPI routes and shared SQLite storage
│   ├── core/          Game engines and account storage
│   └── desktop/       Tk UI and API client
├── data/              Artwork and optional local desktop database
├── docs/              VPS deployment and operations
├── scripts/           Developer commands
├── tests/             Backend integration tests
├── main.py            Desktop entry point
├── Dockerfile         Backend image
├── compose.yaml       Backend service and persistent volume
├── pyproject.toml     Dependency declarations and optional extras
└── uv.lock            Locked dependencies
```

## Run the desktop app

From the project root with uv installed:

```sh
uv run --locked --extra desktop python main.py
```

Set `CASINO_API_URL` to connect to a shared backend. See the
[deployment guide](docs/deployment.md) for client setup, owner creation,
SQLite import, and backups.

## Run the backend tests

From the project root on any supported platform:

```sh
uv run --python 3.12 --locked --extra server --group test python -m unittest discover -s tests -v
```

On Linux, the same command is available via `sh scripts/test-backend.sh`.
The tests create temporary databases.
