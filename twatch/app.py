"""Textual TUI for twatch."""
from __future__ import annotations

import asyncio
import subprocess
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Static, Tree
from textual.widgets.tree import TreeNode

from twatch import store as store_mod
from twatch import tmux


POLL_SECONDS = 0.5


class DetailsPane(Static):
    """Right-side details view for the highlighted session."""

    def compose(self) -> ComposeResult:
        yield Static("", id="details-title")
        yield Static("", id="details-meta")
        yield Static("", id="details-notes")
        yield Static("select a session", id="details-empty")

    def show(self, session: Optional[dict], meta: Optional[dict]) -> None:
        title = self.query_one("#details-title", Static)
        meta_w = self.query_one("#details-meta", Static)
        notes = self.query_one("#details-notes", Static)
        empty = self.query_one("#details-empty", Static)

        if session is None or meta is None:
            title.update("")
            meta_w.update("")
            notes.update("")
            empty.display = True
            return

        empty.display = False
        display_title = meta.get("title") or session["name"]
        title.update(f"{display_title}")

        status_bits = []
        if session.get("attached"):
            status_bits.append("[green]attached[/green]")
        if session.get("dead"):
            status_bits.append("[red]dead[/red]")
        status = "  ".join(status_bits) or "[dim]idle[/dim]"
        meta_w.update(
            f"[dim]session[/dim] {session['name']}\n"
            f"[dim]state[/dim]   {status}"
        )
        notes_text = meta.get("notes") or ""
        notes.update(f"[dim]notes[/dim]\n{notes_text}" if notes_text else "")


class NewSessionModal(ModalScreen[Optional[tuple[str, str]]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, default_name: str) -> None:
        super().__init__()
        self._default_name = default_name

    def compose(self) -> ComposeResult:
        with Vertical(id="new-session-box"):
            yield Static("new session", id="new-session-title")
            yield Static("name", classes="new-session-label")
            yield Input(
                value=self._default_name,
                placeholder="session name",
                id="new-session-name",
            )
            yield Static("command (optional)", classes="new-session-label")
            yield Input(placeholder="command", id="new-session-cmd")
            yield Static("enter to submit, esc to cancel", id="new-session-hint")

    def on_mount(self) -> None:
        self.query_one("#new-session-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "new-session-name":
            self.query_one("#new-session-cmd", Input).focus()
            return
        name_value = (
            self.query_one("#new-session-name", Input).value.strip()
            or self._default_name
        )
        cmd = self.query_one("#new-session-cmd", Input).value.strip()
        self.dismiss((name_value, cmd))

    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameSessionModal(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, old_name: str) -> None:
        super().__init__()
        self.old_name = old_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-session-box"):
            yield Static(f"rename session: {self.old_name}", id="rename-session-title")
            yield Static("new name", classes="rename-session-label")
            yield Input(value=self.old_name, id="rename-session-input")
            yield Static("enter to submit, esc to cancel", id="rename-session-hint")

    def on_mount(self) -> None:
        inp = self.query_one("#rename-session-input", Input)
        inp.focus()
        inp.select_all()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_name = event.input.value.strip()
        if not new_name or new_name == self.old_name:
            self.dismiss(None)
            return
        self.dismiss(new_name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TwatchApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "twatch"
    SUB_TITLE = "tmux session registry"

    BINDINGS = [
        Binding("n", "new_session", "New"),
        Binding("R", "rename_session", "Rename"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
        Binding("r", "refresh_now", "Refresh", show=False),
    ]

    sessions: reactive[list[dict]] = reactive(list, always_update=True)
    selected_name: reactive[Optional[str]] = reactive(None)
    selected_id: reactive[Optional[str]] = reactive(None)

    def __init__(self) -> None:
        super().__init__()
        self.store: dict = store_mod.load_store()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            tree: Tree[dict] = Tree("twatch", id="sidebar")
            tree.show_root = False
            tree.guide_depth = 2
            yield tree
            with Vertical(id="details"):
                yield DetailsPane(id="details-pane")
        yield Footer()

    def on_mount(self) -> None:
        fresh = tmux.list_sessions() or []
        for s in fresh:
            store_mod.ensure_entry(self.store, s["id"], s["name"])
        store_mod.cleanup_stale(self.store, {s["id"] for s in fresh})
        store_mod.save_store(self.store)
        self.sessions = fresh
        self.query_one("#sidebar", Tree).focus()
        self.poll_tmux()

    def watch_sessions(self, _old: list[dict], new: list[dict]) -> None:
        self._rebuild_tree(new)
        self._refresh_details()

    def watch_selected_id(self, _old: Optional[str], _new: Optional[str]) -> None:
        self._refresh_details()

    def _rebuild_tree(self, sessions: list[dict]) -> None:
        tree = self.query_one("#sidebar", Tree)
        previous_selected_id = self.selected_id

        tree.clear()
        root = tree.root

        reselect_node: Optional[TreeNode] = None

        for s in sorted(
            sessions,
            key=lambda s: (self.store["sessions"].get(s["id"], {}).get("title") or s["name"]).lower(),
        ):
            store_mod.ensure_entry(self.store, s["id"], s["name"])
            meta = self.store["sessions"].get(s["id"], {})
            title = meta.get("title") or s["name"]
            markers = []
            if s.get("attached"):
                markers.append("[green]●[/green]")
            if s.get("dead"):
                markers.append("[red]✗[/red]")
            prefix = (" ".join(markers) + " ") if markers else ""
            leaf = root.add_leaf(
                f"{prefix}{title}",
                data={"kind": "session", "id": s["id"], "name": s["name"]},
            )
            if previous_selected_id and s["id"] == previous_selected_id:
                reselect_node = leaf

        target_node = reselect_node
        if target_node is None and tree.root.children:
            target_node = tree.root.children[0]
        if target_node is not None:
            self.call_after_refresh(self._focus_node, target_node)

    def _focus_node(self, node: TreeNode) -> None:
        tree = self.query_one("#sidebar", Tree)
        if node.line >= 0:
            tree.cursor_line = node.line
        data = node.data or {}
        if data.get("kind") == "session":
            self.selected_id = data.get("id")
            self.selected_name = data.get("name")

    def _refresh_details(self) -> None:
        try:
            details = self.query_one("#details-pane", DetailsPane)
        except Exception:
            return
        sid = self.selected_id
        if not sid:
            details.show(None, None)
            return
        session = next((s for s in self.sessions if s["id"] == sid), None)
        meta = self.store["sessions"].get(sid) if session else None
        details.show(session, meta)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        node: TreeNode = event.node
        data = node.data
        if isinstance(data, dict):
            self.selected_id = data.get("id")
            self.selected_name = data.get("name")

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if isinstance(data, dict) and data.get("kind") == "session":
            self.selected_id = data.get("id")
            self.selected_name = data.get("name")
            self.action_attach()

    def action_attach(self) -> None:
        name = self.selected_name
        if not name:
            self.bell()
            return
        if tmux.in_tmux():
            r = tmux.attach(name)
            if r.returncode != 0:
                self.notify(
                    (r.stderr or "switch-client failed").strip()[:120],
                    severity="error",
                )
            else:
                self.exit()
            return
        with self.suspend():
            subprocess.run(["tmux", "attach", "-t", name])
        self.refresh_now()

    def action_refresh_now(self) -> None:
        self.refresh_now()

    def action_new_session(self) -> None:
        def after(result: Optional[tuple[str, str]]) -> None:
            if not result:
                return
            name, cmd = result
            if not name:
                return
            r = tmux.new_session(name, cmd)
            if r.returncode != 0:
                self.notify(
                    (r.stderr or "new-session failed").strip()[:120],
                    severity="error",
                )
                return
            self.refresh_now()
            self.notify(f"created {name}")

        existing = {s["name"] for s in self.sessions}
        default_name, _ = tmux.derive_session_name(existing=existing)
        self.push_screen(NewSessionModal(default_name), after)

    def action_rename_session(self) -> None:
        name = self.selected_name
        if not name:
            self.bell()
            return

        def after(result: Optional[str]) -> None:
            if not result:
                return
            new_name = result
            existing = {s["name"] for s in self.sessions}
            if new_name in existing:
                self.notify(
                    f"session {new_name!r} already exists",
                    severity="warning",
                )
                return
            r = tmux.rename_session(name, new_name)
            if r.returncode != 0:
                self.notify(
                    (r.stderr or "rename-session failed").strip()[:120],
                    severity="error",
                )
                return
            # Store is sid-keyed — no rekey needed. ensure_entry on next refresh will update .name.
            # But refresh is async; nudge the store now so the UI has correct state immediately.
            sid = self.selected_id
            if sid and sid in self.store["sessions"]:
                self.store["sessions"][sid]["name"] = new_name
            store_mod.save_store(self.store)
            self.selected_name = new_name
            self.refresh_now()
            self.notify(f"renamed to {new_name}")

        self.push_screen(RenameSessionModal(name), after)

    def refresh_now(self) -> None:
        fresh = tmux.list_sessions() or []
        for s in fresh:
            store_mod.ensure_entry(self.store, s["id"], s["name"])
        self.sessions = fresh

    async def poll_tmux_loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            fresh = await asyncio.to_thread(tmux.list_sessions)
            if fresh is None:
                continue
            for s in fresh:
                store_mod.ensure_entry(self.store, s["id"], s["name"])
            self.sessions = fresh

    def poll_tmux(self) -> None:
        self.run_worker(self.poll_tmux_loop(), exclusive=True, group="poll")
