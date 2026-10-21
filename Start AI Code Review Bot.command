#!/bin/zsh
cd "$(dirname "$0")"
./.venv/bin/python -m uvicorn reviewbot.app:app --host 127.0.0.1 --port 8020
