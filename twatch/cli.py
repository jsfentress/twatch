"""CLI entry point. `twatch` launches the TUI; subcommands stay argparse-driven."""
from __future__ import annotations

import argparse
import os
import sys

from twatch import store as store_mod
from twatch import tmux


def cmd_list(args) -> int:
    if not tmux.tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    sessions = tmux.list_sessions() or []
    store = store_mod.load_store()
    for s in sorted(sessions, key=lambda x: x["name"]):
        meta = store_mod.ensure_entry(store, s["id"], s["name"])
        att = "attached" if s["attached"] else "detached"
        title = meta.get("title") or s["name"]
        print(f"{s['name']}\t{title}\t{att}")
    return 0


def cmd_register(args) -> int:
    if not tmux.tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    if not os.environ.get("TMUX"):
        print("twatch: not inside a tmux session ($TMUX unset)", file=sys.stderr)
        return 2
    sid = tmux.current_session_id()
    if sid is None:
        print("twatch: could not resolve current session id", file=sys.stderr)
        return 2
    name = tmux.current_session_name() or sid
    store = store_mod.load_store()
    meta = store_mod.ensure_entry(store, sid, name)
    if args.title:
        meta["title"] = args.title
    if args.notes is not None:
        meta["notes"] = args.notes
    store_mod.save_store(store)
    print(f"registered {name} (title={meta['title']!r})")
    return 0


def cmd_create(args) -> int:
    if not tmux.tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    cmd = " ".join(args.command) if args.command else ""
    if args.name is None:
        existing = {s["name"] for s in (tmux.list_sessions() or [])}
        name, reused = tmux.derive_session_name(
            existing=existing, attach_if_exists=(not args.command)
        )
        if reused:
            print(f"exists {name}")
            return 0
    else:
        name = args.name
    r = tmux.new_session(name, cmd)
    if r.returncode != 0:
        print(f"twatch: tmux error: {r.stderr.strip()}", file=sys.stderr)
        return 2
    store = store_mod.load_store()
    live = tmux.list_sessions() or []
    sid = next((s["id"] for s in live if s["name"] == name), None)
    if sid is not None:
        meta = store_mod.ensure_entry(store, sid, name)
        if args.title:
            meta["title"] = args.title
        store_mod.save_store(store)
    print(f"created {name}")
    return 0


def cmd_rename(args) -> int:
    if not tmux.tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2
    live = tmux.list_sessions() or []
    by_name = {s["name"]: s for s in live}
    if args.old not in by_name:
        print(f"twatch: no such session {args.old!r}", file=sys.stderr)
        return 2
    if args.new in by_name:
        print(f"twatch: session {args.new!r} already exists", file=sys.stderr)
        return 2
    r = tmux.rename_session(args.old, args.new)
    if r.returncode != 0:
        print(f"twatch: tmux error: {r.stderr.strip()}", file=sys.stderr)
        return 2
    sid = by_name[args.old]["id"]
    store = store_mod.load_store()
    store_mod.ensure_entry(store, sid, args.new)
    store_mod.save_store(store)
    print(f"renamed {args.old} -> {args.new}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="twatch", description="tmux session registry + TUI")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="print one line per session")

    pr = sub.add_parser("register", help="register the current tmux session")
    pr.add_argument("--title")
    pr.add_argument("--notes")

    pc = sub.add_parser("create", help="create a new tmux session")
    pc.add_argument("name", nargs="?")
    pc.add_argument("--title")
    pc.add_argument("command", nargs=argparse.REMAINDER, help="optional -- command...")

    prn = sub.add_parser("rename", help="rename a tmux session")
    prn.add_argument("old")
    prn.add_argument("new")

    args = p.parse_args(argv)

    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "create":
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        return cmd_create(args)
    if args.cmd == "rename":
        return cmd_rename(args)

    if not tmux.tmux_ok():
        print("twatch: tmux not found on PATH", file=sys.stderr)
        return 2

    from twatch.app import TwatchApp
    try:
        TwatchApp().run()
    except Exception as e:
        print(f"twatch: crashed: {e}", file=sys.stderr)
        return 1
    return 0
