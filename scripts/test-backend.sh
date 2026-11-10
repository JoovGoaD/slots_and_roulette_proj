#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
exec uv run --python 3.12 --locked --extra server --group test python -m unittest discover -s tests -v
