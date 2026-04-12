#!/usr/bin/env python3
"""twatch — tmux session registry + TUI."""
import argparse
import curses
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

POLL_MS = 500
AMBER_HOLD_S = 2.0
FMT = "#{session_name}|#{session_attached}|#{session_activity}"
STORE_DIR = Path.home() / ".twatch"
STORE_PATH = STORE_DIR / "sessions.json"
STORE_BAK = STORE_DIR / "sessions.json.bak"
STALE_SECONDS = 30 * 24 * 3600


def tmux_ok():
    return shutil.which("tmux") is not None


def list_sessions():
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", FMT],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return []
    rows = []
    for line in out.stdout.splitlines():
        if not line:
            continue
        name, attached, activity = line.split("|", 2)
        rows.append({
            "name": name,
            "attached": int(attached) > 0,
            "activity": int(activity),
        })
    return rows


def load_store():
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


def save_store(store):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True))
    os.replace(tmp, STORE_PATH)


def default_entry(name):
    return {
        "title": name,
        "team": "",
        "notes": "",
        "created_by_twatch": False,
        "last_seen": int(time.time()),
    }


def ensure_entry(store, name):
    if name not in store:
        store[name] = default_entry(name)
    else:
        for k, v in default_entry(name).items():
            store[name].setdefault(k, v)
    return store[name]


def cleanup_stale(store, alive_names):
    now = int(time.time())
    for name in list(store.keys()):
        if name in alive_names:
            store[name]["last_seen"] = now
            continue
        if now - int(store[name].get("last_seen", now)) > STALE_SECONDS:
            store.pop(name, None)


def attach(name):
    curses.endwin()
    subprocess.run(["tmux", "attach", "-t", name])


def matches_filter(s, meta, q):
    if not q:
        return True
    q = q.lower()
    return (q in s["name"].lower()
            or q in meta.get("title", "").lower()
            or q in meta.get("team", "").lower()
            or q in meta.get("notes", "").lower())


def build_view(sessions, store, filt):
    """Return (rows, selectable_indices). rows items: ('header', text) or ('session', s, meta)."""
    groups = {}
    for s in sessions:
        meta = ensure_entry(store, s["name"])
        if not matches_filter(s, meta, filt):
            continue
        team = meta.get("team", "") or ""
        groups.setdefault(team, []).append((s, meta))
    rows = []
    selectable = []
    for team in sorted(groups.keys(), key=lambda t: (t == "", t.lower())):
        label = team if team else "(no team)"
        rows.append(("header", label))
        for s, meta in sorted(groups[team], key=lambda sm: sm[1].get("title", sm[0]["name"]).lower()):
            selectable.append(len(rows))
            rows.append(("session", s, meta))
    return rows, selectable


def prompt(stdscr, prefix, initial=""):
    """Inline bottom-line prompt. Enter submits, Esc cancels (returns None)."""
    curses.curs_set(1)
    h, w = stdscr.getmaxyx()
    buf = list(initial)
    while True:
        line = prefix + "".join(buf)
        stdscr.move(h - 1, 0)
        stdscr.clrtoeol()
        stdscr.addnstr(h - 1, 0, line, w - 1)
        stdscr.refresh()
        ch = stdscr.getch()
        if ch == 27:
            curses.curs_set(0)
            return None
        if ch in (curses.KEY_ENTER, 10, 13):
            curses.curs_set(0)
            return "".join(buf)
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf.pop()
        elif 32 <= ch < 127:
            buf.append(chr(ch))


def confirm(stdscr, question):
    ans = prompt(stdscr, question + " (y/N): ")
    return ans is not None and ans.strip().lower() in ("y", "yes")


def status(stdscr, msg):
    h, w = stdscr.getmaxyx()
    stdscr.move(h - 1, 0)
    stdscr.clrtoeol()
    stdscr.addnstr(h - 1, 0, msg, w - 1, curses.A_DIM)
    stdscr.refresh()


def draw(stdscr, rows, sel_row, amber_until, now, filt, status_msg):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    header = "twatch  ·  ↑/↓  Enter attach  n new  e edit  x kill  r register  / filter  ? help  q quit"
    stdscr.addnstr(0, 0, header, w - 1, curses.A_DIM)
    if filt:
        stdscr.addnstr(1, 0, f"filter: {filt}", w - 1, curses.A_DIM)
    if rows is None:
        stdscr.addnstr(3, 2, "tmux not found on PATH", w - 1)
    elif not rows:
        stdscr.addnstr(3, 2, "no tmux sessions", w - 1, curses.A_DIM)
    else:
        top = 2
        for i, row in enumerate(rows):
            y = top + i
            if y >= h - 1:
                break
            if row[0] == "header":
                stdscr.addnstr(y, 0, f"— {row[1]} —", w - 1, curses.A_BOLD)
                continue
            _, s, meta = row
            selected = (i == sel_row)
            row_attr = curses.A_REVERSE if selected else curses.A_NORMAL
            active = now < amber_until.get(s["name"], 0)
            dot_attr = curses.color_pair(1) if active else curses.A_DIM
            stdscr.addnstr(y, 2, "  ", 2, row_attr)
            stdscr.addnstr(y, 2, "●", 1, dot_attr | row_attr)
            title = meta.get("title") or s["name"]
            label = f" {title}"
            if s["attached"]:
                label += "  (attached)"
            team = meta.get("team", "")
            suffix = f"  · {team}" if team else ""
            full = (label + suffix).ljust(w - 5)
            stdscr.addnstr(y, 4, full, w - 5, row_attr | (0 if selected else curses.A_DIM if not active else 0))
    if status_msg:
        stdscr.addnstr(h - 1, 0, status_msg[: w - 1], w - 1, curses.A_DIM)
    stdscr.refresh()


HELP_TEXT = [
    "twatch keybinds",
    "",
    "  up/down    move selection",
    "  enter      attach to session",
    "  n          new session",
    "  e          edit metadata (title, team, notes)",
    "  x          kill session (confirm)",
    "  r          register selected session metadata",
    "  /          filter by substring",
    "  ?          this help",
    "  q / esc    quit",
    "",
    "press any key to dismiss",
]


def show_help(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    for i, line in enumerate(HELP_TEXT):
        if i >= h - 1:
            break
        stdscr.addnstr(i, 2, line, w - 3)
    stdscr.refresh()
    stdscr.nodelay(False)
    stdscr.getch()
    stdscr.nodelay(True)


def edit_metadata(stdscr, meta):
    title = prompt(stdscr, f"title [{meta.get('title','')}]: ")
    if title is None:
        return False
    if title.strip():
        meta["title"] = title.strip()
    team = prompt(stdscr, f"team [{meta.get('team','')}]: ")
    if team is None:
        return False
    if team.strip() or team == "":
        meta["team"] = team.strip()
    notes = prompt(stdscr, f"notes [{meta.get('notes','')}]: ")
    if notes is None:
        return False
    if notes.strip() or notes == "":
        meta["notes"] = notes.strip()
    return True


def new_session(stdscr, store):
    name = prompt(stdscr, "new session name: ")
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    cmd = prompt(stdscr, "initial command (optional): ")
    if cmd is None:
        return None
    args = ["tmux", "new-session", "-d", "-s", name]
    if cmd.strip():
        args.append(cmd)
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        return f"tmux error: {r.stderr.strip()[:80]}"
    entry = ensure_entry(store, name)
    entry["created_by_twatch"] = True
    save_store(store)
    return f"created {name}"


def kill_session(stdscr, name):
    if not confirm(stdscr, f"kill session '{name}'?"):
        return "cancelled"
    r = subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True, text=True)
    if r.returncode != 0:
        return f"kill failed: {r.stderr.strip()[:80]}"
    return f"killed {name}"


def run_tui(stdscr):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, 214, -1)
    stdscr.nodelay(True)
    stdscr.timeout(POLL_MS)

    store = load_store()
    sessions = list_sessions()
    if sessions is None:
        rows, selectable = None, []
    else:
        for s in sessions:
            ensure_entry(store, s["name"])
        cleanup_stale(store, {s["name"] for s in sessions})
        save_store(store)
        rows, selectable = build_view(sessions, store, "")

    prev_activity = {s["name"]: s["activity"] for s in (sessions or [])}
    amber_until = {}
    sel_idx = 0
    filt = ""
    status_msg = ""
    status_until = 0.0

    def set_status(msg, hold=2.0):
        nonlocal status_msg, status_until
        status_msg = msg
        status_until = time.monotonic() + hold

    while True:
        now = time.monotonic()
        if status_msg and now > status_until:
            status_msg = ""
        sel_row = selectable[sel_idx] if selectable and sel_idx < len(selectable) else -1
        draw(stdscr, rows, sel_row, amber_until, now, filt, status_msg)

        ch = stdscr.getch()
        if ch == ord("q"):
            return
        if ch == 27:
            if filt:
                filt = ""
            else:
                return
        elif ch == curses.KEY_UP and selectable:
            sel_idx = (sel_idx - 1) % len(selectable)
        elif ch == curses.KEY_DOWN and selectable:
            sel_idx = (sel_idx + 1) % len(selectable)
        elif ch in (curses.KEY_ENTER, 10, 13) and selectable and sessions:
            row = rows[selectable[sel_idx]]
            attach(row[1]["name"])
            stdscr.clear()
            stdscr.refresh()
        elif ch == ord("n") and sessions is not None:
            msg = new_session(stdscr, store)
            if msg:
                set_status(msg)
        elif ch == ord("e") and selectable:
            row = rows[selectable[sel_idx]]
            name = row[1]["name"]
            meta = ensure_entry(store, name)
            if edit_metadata(stdscr, meta):
                save_store(store)
                set_status(f"saved {name}")
            else:
                set_status("edit cancelled")
        elif ch == ord("x") and selectable:
            row = rows[selectable[sel_idx]]
            set_status(kill_session(stdscr, row[1]["name"]))
        elif ch == ord("r") and selectable:
            row = rows[selectable[sel_idx]]
            name = row[1]["name"]
            existing = store.get(name)
            has_meta = bool(existing and (
                (existing.get("title") and existing.get("title") != name)
                or existing.get("team") or existing.get("notes")
            ))
            if has_meta:
                set_status(f"{name} already registered")
            else:
                ensure_entry(store, name)
                save_store(store)
                set_status(f"registered {name}")
        elif ch == ord("/"):
            if filt:
                filt = ""
                set_status("filter cleared")
            else:
                q = prompt(stdscr, "filter: ")
                if q is not None:
                    filt = q.strip()
        elif ch == ord("?"):
            show_help(stdscr)

        fresh = list_sessions()
        if fresh is None:
            sessions = None
            rows, selectable = None, []
            continue
        sessions = fresh
        t = time.monotonic()
        for s in fresh:
            p = prev_activity.get(s["name"])
            if p is not None and s["activity"] > p:
                amber_until[s["name"]] = t + AMBER_HOLD_S
            prev_activity[s["name"]] = s["activity"]
        alive = {s["name"] for s in fresh}
        for gone in [n for n in list(prev_activity) if n not in alive]:
            prev_activity.pop(gone, None)
            amber_until.pop(gone, None)
        for s in fresh:
            ensure_entry(store, s["name"])
        rows, selectable = build_view(fresh, store, filt)
        if selectable:
            if sel_idx >= len(selectable):
                sel_idx = len(selectable) - 1
        else:
            sel_idx = 0


def cmd_list(args):
    if not tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    sessions = list_sessions() or []
    store = load_store()
    for s in sorted(sessions, key=lambda x: x["name"]):
        meta = ensure_entry(store, s["name"])
        team = meta.get("team", "") or "-"
        att = "attached" if s["attached"] else "detached"
        title = meta.get("title") or s["name"]
        print(f"{team}\t{s['name']}\t{title}\t{att}")
    return 0


def cmd_register(args):
    if not tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    if not os.environ.get("TMUX"):
        print("twatch: not inside a tmux session ($TMUX unset)", file=sys.stderr)
        return 2
    r = subprocess.run(
        ["tmux", "display-message", "-p", "#{session_name}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"twatch: tmux error: {r.stderr.strip()}", file=sys.stderr)
        return 2
    name = r.stdout.strip()
    if not name:
        print("twatch: could not resolve current session name", file=sys.stderr)
        return 2
    store = load_store()
    meta = ensure_entry(store, name)
    if args.title:
        meta["title"] = args.title
    if args.team is not None:
        meta["team"] = args.team
    if args.notes is not None:
        meta["notes"] = args.notes
    save_store(store)
    print(f"registered {name} (team={meta['team']!r}, title={meta['title']!r})")
    return 0


def cmd_create(args):
    if not tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    cmd = ["tmux", "new-session", "-d", "-s", args.name]
    if args.command:
        cmd.append(" ".join(args.command))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"twatch: tmux error: {r.stderr.strip()}", file=sys.stderr)
        return 2
    store = load_store()
    meta = ensure_entry(store, args.name)
    meta["created_by_twatch"] = True
    if args.title:
        meta["title"] = args.title
    if args.team is not None:
        meta["team"] = args.team
    save_store(store)
    print(f"created {args.name}")
    return 0


def self_check():
    d = default_entry("foo")
    assert d["title"] == "foo" and d["team"] == ""
    s = {"foo": default_entry("foo")}
    cleanup_stale(s, {"foo"})
    assert "foo" in s
    s["foo"]["last_seen"] = 0
    cleanup_stale(s, set())
    assert "foo" not in s
    print("self-check ok")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="twatch", description="tmux session registry + TUI")
    p.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="print one line per session")

    pr = sub.add_parser("register", help="register the current tmux session")
    pr.add_argument("--title")
    pr.add_argument("--team")
    pr.add_argument("--notes")

    pc = sub.add_parser("create", help="create a new tmux session")
    pc.add_argument("name")
    pc.add_argument("--title")
    pc.add_argument("--team")
    pc.add_argument("command", nargs=argparse.REMAINDER, help="optional -- command...")

    args = p.parse_args(argv)

    if args.self_check:
        return self_check()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "create":
        if args.command and args.command and args.command[0] == "--":
            args.command = args.command[1:]
        return cmd_create(args)

    if not tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    try:
        curses.wrapper(run_tui)
    except Exception as e:
        print(f"twatch: crashed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
