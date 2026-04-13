"""Textual TUI for twatch."""
from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Footer, Header, Input, Static, Tree
from textual.widgets.tree import TreeNode

from twatch import store as store_mod
from twatch import tmux


POLL_SECONDS = 0.5


class PathSuggester(Suggester):
    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        if value.startswith("/"):
            roots = [None]
        elif value.startswith("~"):
            roots = [None]
        else:
            roots = [os.path.expanduser("~"), "/"]
        for root in roots:
            if value.startswith("/"):
                expanded = value
            elif value.startswith("~"):
                expanded = os.path.expanduser(value)
            else:
                expanded = os.path.join(root, value)
            if expanded.endswith("/"):
                parent_dir = expanded if expanded == "/" else expanded.rstrip("/")
                prefix = ""
            else:
                parent_dir = os.path.dirname(expanded)
                if not parent_dir:
                    parent_dir = "/" if value.startswith("/") else (root or os.path.expanduser("~"))
                prefix = os.path.basename(expanded)
            try:
                entries = os.listdir(parent_dir)
            except OSError:
                continue
            prefix_lower = prefix.lower()
            for entry in sorted(entries, key=str.lower):
                if not entry.lower().startswith(prefix_lower):
                    continue
                if not os.path.isdir(os.path.join(parent_dir, entry)):
                    continue
                return value + entry[len(prefix):]
        return None


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


class NewSessionModal(ModalScreen[Optional[tuple[str, str, str]]]):
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
            yield Static("working directory (optional)", classes="new-session-label")
            yield Input(
                placeholder="Working directory (optional, tab to autocomplete)",
                id="new-session-cwd",
                suggester=PathSuggester(),
            )
            yield Static("command (optional)", classes="new-session-label")
            yield Input(placeholder="command", id="new-session-cmd")
            yield Static("enter to submit, esc to cancel", id="new-session-hint")

    def on_mount(self) -> None:
        self.query_one("#new-session-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "new-session-name":
            self.query_one("#new-session-cwd", Input).focus()
            return
        if event.input.id == "new-session-cwd":
            self.query_one("#new-session-cmd", Input).focus()
            return
        name_value = (
            self.query_one("#new-session-name", Input).value.strip()
            or self._default_name
        )
        cwd_input = self.query_one("#new-session-cwd", Input)
        cwd_raw = cwd_input.value.strip()
        cwd = ""
        if cwd_raw:
            cwd = os.path.abspath(os.path.expanduser(cwd_raw))
            if not os.path.isdir(cwd):
                self.app.notify(
                    f"not a directory: {cwd_raw}",
                    severity="error",
                    timeout=4,
                )
                cwd_input.focus()
                return
        cmd = self.query_one("#new-session-cmd", Input).value.strip()
        self.dismiss((name_value, cwd, cmd))

    def action_cancel(self) -> None:
        self.dismiss(None)


class GroupSessionModal(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, session_name: str, current_group: str) -> None:
        super().__init__()
        self.session_name = session_name
        self.current_group = current_group

    def compose(self) -> ComposeResult:
        with Vertical(id="group-session-box"):
            yield Static(f"group: {self.session_name}", id="group-session-title")
            yield Static("group name (blank to ungroup)", classes="group-session-label")
            yield Input(value=self.current_group, id="group-session-input")
            yield Static("enter to submit, esc to cancel", id="group-session-hint")

    def on_mount(self) -> None:
        inp = self.query_one("#group-session-input", Input)
        inp.focus()
        inp.select_all()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.input.value.strip())

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


class ConfirmKillModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "confirm", "Confirm", show=False),
        Binding("enter", "confirm", "Confirm", show=False),
        Binding("n", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, session_name: str, title: Optional[str]) -> None:
        super().__init__()
        self.session_name = session_name
        self.display_title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-kill-box"):
            yield Static(f"kill session: {self.session_name}", id="confirm-kill-title")
            if self.display_title and self.display_title != self.session_name:
                yield Static(self.display_title, classes="confirm-kill-label")
            yield Static("y / enter to confirm, n / esc to cancel", id="confirm-kill-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TwatchApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "twatch"
    SUB_TITLE = "tmux session registry"

    BINDINGS = [
        Binding("n", "new_session", "New"),
        Binding("R", "rename_session", "Rename"),
        Binding("g", "group_session", "Group"),
        Binding("x", "kill_session", "Kill"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
        Binding("r", "refresh_now", "Refresh", show=False),
        Binding("right", "tree_expand", "Expand", show=False),
        Binding("l", "tree_expand", "Expand", show=False),
        Binding("left", "tree_collapse", "Collapse", show=False),
        Binding("h", "tree_collapse", "Collapse", show=False),
    ]

    sessions: reactive[list[dict]] = reactive(list, always_update=True)
    selected_name: reactive[Optional[str]] = reactive(None)
    selected_id: reactive[Optional[str]] = reactive(None)

    def __init__(self) -> None:
        super().__init__()
        self.store: dict = store_mod.load_store()
        self._collapsed_groups: set[str] = set()
        self._cursor_group: Optional[str] = None

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
        self._capture_tree_state(tree)
        previous_selected_id = self.selected_id
        previous_cursor_group = self._cursor_group

        tree.clear()
        root = tree.root

        reselect_node: Optional[TreeNode] = None
        reselect_group_node: Optional[TreeNode] = None

        for s in sessions:
            store_mod.ensure_entry(self.store, s["id"], s["name"])

        def sort_key(s: dict) -> str:
            meta = self.store["sessions"].get(s["id"], {})
            return (meta.get("title") or s["name"]).lower()

        buckets: dict[str, list[dict]] = {}
        for s in sessions:
            group = self.store["sessions"].get(s["id"], {}).get("group") or ""
            buckets.setdefault(group, []).append(s)

        ordered_groups = sorted(buckets.keys(), key=lambda g: (g == "", g.lower()))

        for group in ordered_groups:
            if group:
                parent = root.add(
                    f"[bold cyan]{group}[/bold cyan]",
                    data={"kind": "group", "name": group},
                    expand=(group not in self._collapsed_groups),
                )
                if previous_cursor_group == group:
                    reselect_group_node = parent
            else:
                parent = root
            for s in sorted(buckets[group], key=sort_key):
                meta = self.store["sessions"].get(s["id"], {})
                title = meta.get("title") or s["name"]
                markers = []
                if s.get("attached"):
                    markers.append("[green]●[/green]")
                if s.get("dead"):
                    markers.append("[red]✗[/red]")
                prefix = (" ".join(markers) + " ") if markers else ""
                leaf = parent.add_leaf(
                    f"{prefix}{title}",
                    data={"kind": "session", "id": s["id"], "name": s["name"]},
                )
                if previous_selected_id and s["id"] == previous_selected_id:
                    reselect_node = leaf

        target_node = reselect_group_node or reselect_node
        if target_node is None:
            target_node = self._first_session_node(root)
        if target_node is not None:
            self.call_after_refresh(self._focus_node, target_node)

    def _capture_tree_state(self, tree: Tree) -> None:
        collapsed: set[str] = set()
        for child in tree.root.children:
            data = child.data or {}
            if data.get("kind") == "group" and not child.is_expanded:
                collapsed.add(data.get("name", ""))
        self._collapsed_groups = collapsed

        cursor_node = tree.cursor_node
        if cursor_node is not None:
            data = cursor_node.data or {}
            if data.get("kind") == "group":
                self._cursor_group = data.get("name")
                return
        self._cursor_group = None

    def _first_session_node(self, root: TreeNode) -> Optional[TreeNode]:
        for child in root.children:
            data = child.data or {}
            if data.get("kind") == "session":
                return child
            found = self._first_session_node(child)
            if found is not None:
                return found
        return None

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
            kind = data.get("kind")
            if kind == "session":
                self._cursor_group = None
                self.selected_id = data.get("id")
                self.selected_name = data.get("name")
            elif kind == "group":
                self._cursor_group = data.get("name")

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
        def after(result: Optional[tuple[str, str, str]]) -> None:
            if not result:
                return
            name, cwd, cmd = result
            if not name:
                return
            r = tmux.new_session(name, cmd, cwd=cwd or None)
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

    def action_tree_expand(self) -> None:
        tree = self.query_one("#sidebar", Tree)
        node = tree.cursor_node
        if node is None:
            return
        data = node.data or {}
        if data.get("kind") == "group":
            if not node.is_expanded:
                node.expand()
                self._collapsed_groups.discard(data.get("name", ""))
            elif node.children:
                self._focus_node(node.children[0])

    def action_tree_collapse(self) -> None:
        tree = self.query_one("#sidebar", Tree)
        node = tree.cursor_node
        if node is None:
            return
        data = node.data or {}
        kind = data.get("kind")
        if kind == "group" and node.is_expanded:
            node.collapse()
            self._collapsed_groups.add(data.get("name", ""))
            return
        if kind == "session":
            parent = node.parent
            if parent is not None and (parent.data or {}).get("kind") == "group":
                self._focus_node(parent)

    def action_group_session(self) -> None:
        sid = self.selected_id
        name = self.selected_name
        if not sid or not name:
            self.bell()
            return
        current = (self.store["sessions"].get(sid, {}) or {}).get("group", "")

        def after(result: Optional[str]) -> None:
            if result is None:
                return
            store_mod.set_group(self.store, sid, result)
            store_mod.save_store(self.store)
            self._rebuild_tree(self.sessions)
            if result:
                self.notify(f"{name} -> {result}")
            else:
                self.notify(f"{name} ungrouped")

        self.push_screen(GroupSessionModal(name, current), after)

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

    def action_kill_session(self) -> None:
        sid = self.selected_id
        name = self.selected_name
        if not sid or not name:
            self.bell()
            return
        meta = self.store["sessions"].get(sid, {}) or {}
        title = meta.get("title")

        def after(result: Optional[bool]) -> None:
            if not result:
                return
            r = tmux.kill_session(name)
            if r.returncode != 0:
                self.notify(
                    (r.stderr or "kill-session failed").strip()[:120],
                    severity="error",
                )
                return
            self.store["sessions"].pop(sid, None)
            store_mod.save_store(self.store)
            if self.selected_id == sid:
                self.selected_id = None
                self.selected_name = None
            self.refresh_now()
            self.notify(f"killed {name}")

        self.push_screen(ConfirmKillModal(name, title), after)

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
