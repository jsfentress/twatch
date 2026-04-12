"""Subprocess wrappers around the `tmux` CLI."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import unicodedata
from typing import Iterable, Optional

FMT = "#{session_name}|#{session_attached}|#{session_activity}"


def tmux_ok() -> bool:
    return shutil.which("tmux") is not None


def in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def claudify(cmd: str) -> str:
    """Force `claude` invocations to include --dangerously-skip-permissions."""
    s = cmd.strip()
    if not s:
        return s
    try:
        toks = shlex.split(s)
    except ValueError:
        return s
    if not toks or os.path.basename(toks[0]) != "claude":
        return s
    if "--dangerously-skip-permissions" in toks:
        return s
    toks.insert(1, "--dangerously-skip-permissions")
    return " ".join(shlex.quote(t) for t in toks)


def list_sessions() -> Optional[list[dict]]:
    """Return list of session dicts, or None if tmux isn't available."""
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", FMT],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return []
    dead = _dead_sessions()
    rows = []
    for line in out.stdout.splitlines():
        if not line:
            continue
        name, attached, activity = line.split("|", 2)
        rows.append({
            "name": name,
            "attached": int(attached) > 0,
            "activity": int(activity),
            "dead": name in dead,
        })
    return rows


def _dead_sessions() -> set[str]:
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}|#{pane_dead}"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return set()
    if out.returncode != 0:
        return set()
    dead = set()
    for line in out.stdout.splitlines():
        if not line:
            continue
        name, d = line.split("|", 1)
        if d == "1":
            dead.add(name)
    return dead


def sanitize_session_name(raw: str) -> str:
    """Turn arbitrary text into a tmux-safe session name."""
    s = raw.strip()
    out_chars = []
    for c in s:
        if c in (".", ":", "/") or (c.isascii() and c.isspace()) or unicodedata.category(c).startswith("C"):
            out_chars.append("-")
        else:
            out_chars.append(c)
    s = "".join(out_chars)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    return s or "session"


def derive_session_name(
    cwd: str | None = None,
    *,
    existing: Iterable[str] | None = None,
    attach_if_exists: bool = False,
) -> tuple[str, bool]:
    """Derive a unique tmux session name from `cwd`.

    Returns `(name, exists)` where `exists` indicates the base name was
    already taken. When `attach_if_exists` is True and the base collides,
    returns the base name with `exists=True` so callers can attach instead
    of creating a new session.
    """
    if cwd is None:
        cwd = os.getcwd()
    base = sanitize_session_name(os.path.basename(os.path.abspath(cwd)) or "session")
    if existing is None:
        rows = list_sessions() or []
        existing = [r["name"] for r in rows]
    existing_set = set(existing)
    if base not in existing_set:
        return (base, False)
    if attach_if_exists:
        return (base, True)
    n = 2
    while f"{base}-{n}" in existing_set:
        n += 1
    return (f"{base}-{n}", False)


def attach(name: str) -> subprocess.CompletedProcess:
    """Attach to a session. Uses switch-client when already inside tmux."""
    if in_tmux():
        return subprocess.run(
            ["tmux", "switch-client", "-t", name],
            capture_output=True, text=True,
        )
    return subprocess.run(["tmux", "attach", "-t", name])


def new_session(name: str, cmd: str = "") -> subprocess.CompletedProcess:
    args = ["tmux", "new-session", "-d", "-s", name]
    final = claudify(cmd)
    if final:
        args.append(f"/bin/sh -c {shlex.quote(final)}")
    return subprocess.run(args, capture_output=True, text=True)


def kill_session(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "kill-session", "-t", name],
        capture_output=True, text=True,
    )


def rename_session(old: str, new: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "rename-session", "-t", old, new],
        capture_output=True, text=True,
    )


def capture_pane(name: str) -> str:
    r = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", name],
        capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else ""


def current_session_name() -> Optional[str]:
    r = subprocess.run(
        ["tmux", "display-message", "-p", "#{session_name}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    name = r.stdout.strip()
    return name or None
