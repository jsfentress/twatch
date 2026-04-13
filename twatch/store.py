"""JSON-backed persistent metadata for tmux sessions."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

STORE_DIR = Path.home() / ".twatch"
STORE_PATH = STORE_DIR / "sessions.json"
STORE_BAK = STORE_DIR / "sessions.json.bak"
STALE_SECONDS = 30 * 24 * 3600
SCHEMA_VERSION = 3


def _empty_store() -> dict:
    return {"version": SCHEMA_VERSION, "sessions": {}}


def load_store() -> dict:
    if not STORE_PATH.exists():
        return _empty_store()
    try:
        data = json.loads(STORE_PATH.read_text())
        if not isinstance(data, dict):
            raise ValueError("not a dict")
    except Exception:
        try:
            STORE_PATH.replace(STORE_BAK)
        except Exception:
            pass
        return _empty_store()

    version = data.get("version")
    if version == SCHEMA_VERSION:
        data.setdefault("sessions", {})
        return data

    migrated: Optional[dict] = None
    if "version" not in data:
        migrated = _migrate_v2_to_v3(_migrate_v1_to_v2(data))
    elif version == 2:
        migrated = _migrate_v2_to_v3(data)

    if migrated is not None:
        try:
            save_store(migrated)
        except Exception:
            pass
        return migrated

    # Unknown future version — don't crash, return empty.
    return _empty_store()


def _migrate_v1_to_v2(old: dict) -> dict:
    from twatch import tmux  # lazy to avoid circular import

    live = tmux.list_sessions() or []
    name_to_id = {s["name"]: s["id"] for s in live}

    new_sessions: dict = {}
    for old_name, entry in old.items():
        if old_name == "version" or not isinstance(entry, dict):
            continue
        sid = name_to_id.get(old_name)
        migrated = {
            "name": old_name,
            "title": entry.get("title", ""),
            "notes": entry.get("notes", ""),
            "last_seen": int(entry.get("last_seen", time.time())),
        }
        if sid is not None:
            new_sessions[sid] = migrated
        else:
            new_sessions[f"__orphan__:{old_name}"] = migrated
    return {"version": 2, "sessions": new_sessions}


def _migrate_v2_to_v3(old: dict) -> dict:
    sessions = {}
    for sid, entry in (old.get("sessions") or {}).items():
        if not isinstance(entry, dict):
            continue
        new_entry = dict(entry)
        new_entry.setdefault("group", "")
        sessions[sid] = new_entry
    return {"version": SCHEMA_VERSION, "sessions": sessions}


def save_store(store: dict) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True))
    os.replace(tmp, STORE_PATH)


def default_entry(name: str) -> dict:
    return {
        "name": name,
        "title": "",
        "notes": "",
        "group": "",
        "last_seen": int(time.time()),
    }


def set_group(store: dict, sid: str, group: str) -> None:
    sessions = store.setdefault("sessions", {})
    if sid in sessions:
        sessions[sid]["group"] = group.strip()


def ensure_entry(store: dict, sid: str, name: str) -> dict:
    sessions = store.setdefault("sessions", {})
    if sid not in sessions:
        sessions[sid] = default_entry(name)
    else:
        entry = sessions[sid]
        for k, v in default_entry(name).items():
            entry.setdefault(k, v)
        entry["name"] = name
    return sessions[sid]


def cleanup_stale(store: dict, alive_ids: set) -> None:
    sessions = store.setdefault("sessions", {})
    now = int(time.time())
    for sid in list(sessions.keys()):
        if sid in alive_ids:
            sessions[sid]["last_seen"] = now
            continue
        if now - int(sessions[sid].get("last_seen", now)) > STALE_SECONDS:
            sessions.pop(sid, None)


def id_for_name(store: dict, name: str) -> Optional[str]:
    for sid, entry in store.get("sessions", {}).items():
        if entry.get("name") == name:
            return sid
    return None
