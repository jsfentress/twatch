# twatch

A Python TUI and CLI for managing tmux sessions with persistent metadata (title, notes).

Wraps `tmux` with a [Textual](https://textual.textualize.io) interface that lists live sessions in a sidebar tree, shows per-session details on the right, and stores per-session metadata in `~/.twatch/sessions.json`.

## Requirements

- Python 3.9+
- `tmux` on `PATH`
- `textual>=0.60` (installed automatically)

## Install

```sh
pip install .
# or, for development:
pip install -e .
```

This creates a `twatch` entry point (see `[project.scripts]` in `pyproject.toml`).

## Usage

### TUI

```sh
twatch
```

Two-pane layout: a session tree on the left, a details pane on the right.

Keybinds:

| Key        | Action                                              |
|------------|-----------------------------------------------------|
| `↑` / `↓`  | Move selection in the tree                          |
| `Enter`    | Attach to selected session                          |
| `n`        | New session (modal prompts for name + command)      |
| `r`        | Refresh now                                         |
| `q` / `Esc`| Quit                                                |

When already inside tmux, attach uses `tmux switch-client`; otherwise the app suspends and runs `tmux attach`.

### CLI

```sh
twatch list                                          # one tab-separated row per session: name<TAB>title<TAB>attached|detached
twatch register --title "..." --notes "..."          # register the current session (requires $TMUX)
twatch create NAME --title "..." -- cmd...           # create a detached session, optionally running cmd
```

Exit codes: `0` success, `2` precondition failure (no tmux, not in a session), `1` TUI crash.

## Unified session naming

twatch derives a default session name from the current directory so sessions are recognizable at a glance. No configuration is required for the in-app behavior; the optional `.tmux.conf` hook below extends the same convention to sessions created outside twatch.

### In-app behavior

`twatch create` with no `NAME` argument derives a name from `basename($PWD)`, sanitized as follows:

- `.`, `:`, whitespace, and control characters are replaced with `-`
- An empty basename (or `/`) falls back to `session`
- On collision, a `-2`, `-3`, ... suffix is appended to find the next free name

Collision handling depends on whether a command was supplied:

- No command and the derived name already exists: `twatch create` prints `exists <name>` and exits `0`. It does not create a duplicate and does not attach (attaching is a future `twatch attach` job).
- Command supplied and the derived name collides: the new session is created under the next free `-N` suffix.

Explicit names (`twatch create my-name`) are passed through unchanged — no sanitization is applied, and tmux's duplicate-session error surfaces as it does today.

The TUI's "new session" modal pre-fills its name input with the same derived default. Press Enter to accept it, or edit before submitting.

### Optional: cover bare `tmux` too

For sessions created outside twatch (`tmux new`, `tmux new-session`, plugins, scripts), add this hook to `~/.tmux.conf` so tmux auto-renames numerically-defaulted sessions to the cwd basename:

```tmux
# Auto-name new sessions after $PWD basename when tmux assigned a numeric default
set-hook -g session-created 'if -F "#{m/r:^[0-9]+$,#{session_name}}" "run-shell \"tmux rename-session -t \\\"#{session_id}\\\" \\\"$(basename \\\"#{session_path}\\\" | tr \\\" .:\\\" \\\"---\\\")\\\" 2>/dev/null || true\""'
```

Notes:

- The hook only fires on sessions whose name is purely numeric (tmux's default), so it will never overwrite an explicit name from twatch or anywhere else.
- If the basename is already taken, the rename silently fails and the session keeps its numeric name.
- Reload the config with `tmux source-file ~/.tmux.conf` (or restart tmux) to pick up the hook.

## Data

Metadata lives at `~/.twatch/sessions.json`. Entries not seen for 30 days are pruned on startup. Corrupt files are rotated to `sessions.json.bak` and the store is reset to empty.

## Layout

Python package under [`twatch/`](twatch/):

- `twatch/cli.py` — argparse entry point and subcommands
- `twatch/app.py` — Textual `App` (TUI)
- `twatch/app.tcss` — Textual stylesheet
- `twatch/tmux.py` — subprocess wrappers around the `tmux` binary
- `twatch/store.py` — JSON-backed metadata store

## Specs

Foundational component specs live in [`specs/`](specs/) — one file per component (metadata store, tmux bridge, TUI shell, activity indicator, CLI) describing purpose, file map, and best practices.
