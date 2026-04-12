# twatch

A single-file Python TUI and CLI for managing tmux sessions with persistent metadata (title, team, notes).

Wraps `tmux` with a curses interface that lists live sessions grouped by team, shows activity via an amber "recent output" indicator, and stores per-session metadata in `~/.twatch/sessions.json`.

## Requirements

- Python 3.8+
- `tmux` on `PATH`
- A terminal that supports 256-color curses

## Install

```sh
chmod +x twatch.py
ln -s "$PWD/twatch.py" ~/.local/bin/twatch
```

## Usage

### TUI

```sh
twatch
```

Keybinds:

| Key        | Action                                 |
|------------|----------------------------------------|
| `↑` / `↓`  | Move selection                         |
| `Enter`    | Attach to session                      |
| `n`        | New session (prompts for name + cmd)   |
| `e`        | Edit metadata (title / team / notes)   |
| `x`        | Kill session (confirmed)               |
| `r`        | Register selected session              |
| `/`        | Filter by substring; `/` again clears  |
| `?`        | Help                                   |
| `q` / `Esc`| Quit                                   |

### CLI

```sh
twatch list                                          # one tab-separated row per session
twatch register --title "..." --team ... --notes ... # register current session (needs $TMUX)
twatch create NAME --title "..." --team ... -- cmd   # create detached session
```

## Data

Metadata lives at `~/.twatch/sessions.json`. Entries not seen for 30 days are pruned on startup. Corrupt files are rotated to `sessions.json.bak`.

## Layout

Single file: [`twatch.py`](twatch.py). No dependencies beyond the stdlib.

## Specs

Foundational component specs live in [`specs/`](specs/) — one file per component (metadata store, tmux bridge, TUI shell, activity indicator, CLI) describing purpose, file map, and best practices.
