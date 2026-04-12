"""JSON-backed persistent metadata for tmux sessions."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STORE_DIR = Path.home() / ".twatch"
STORE_PATH = STORE_DIR / "sessions.json"
STORE_BAK = STORE_DIR / "sessions.json.bak"
STALE_SECONDS = 30 * 24 * 3600


def load_store() -> dict:
    if not STORE_PATH.exists():
        return {}
    try:
        data = json.loads(STORE_PATH.read_text())
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        return data
    except Exception:
        try:
            STORE_PATH.replace(STORE_BAK)
        except Exception:
            pass
        return {}


def save_store(store: dict) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True))
    os.replace(tmp, STORE_PATH)


def default_entry(name: str) -> dict:
    return {
        "title": name,
        "notes": "",
        "last_seen": int(time.time()),
    }


def ensure_entry(store: dict, name: str) -> dict:
    if name not in store:
        store[name] = default_entry(name)
    else:
        for k, v in default_entry(name).items():
            store[name].setdefault(k, v)
    return store[name]


def cleanup_stale(store: dict, alive_names: set) -> None:
    now = int(time.time())
    for name in list(store.keys()):
        if name in alive_names:
            store[name]["last_seen"] = now
            continue
        if now - int(store[name].get("last_seen", now)) > STALE_SECONDS:
            store.pop(name, None)
