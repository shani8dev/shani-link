"""The Calls tab's newer parts: the shared call history, the line to call on,
and the dial pad's suggestions - kept apart from phone.py, which is long enough.

**The history** is GNOME Calls' history for every line at once: the landline,
the phone over Bluetooth and the computer's modem write to one list
(`shani_link.calls_log`), shown here under a heading per day. Each row says
which way the call went (or that it was missed), on which line, when and for
how long, with Call back on the row and the rest in its menu: send a message,
add the number to your contacts or to a contact you already have, delete it.
Clear all asks nothing and offers Undo instead, like deleting a contact.

**The line** a call goes out on is chosen on the dial pad, and the choice is
remembered in `~/.config/shani-link/calls.json` - its own file, not the
service's settings.json, because the service rewrites that one from memory and
would drop a key it does not know.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_link import calls_log, contacts, paths  # noqa: E402
from shani_link.app import common  # noqa: E402

logger = logging.getLogger(__name__)

#: The dial pad's choices: (id, what it says). "auto" is the old behaviour -
#: the landline when it is up, else the phone.
LINE_CHOICES = (("auto", "Automatic"), ("landline", "Landline"), ("phone", "Phone"),
                ("modem", "Mobile"))


def _prefs_file():
    return paths.config_home() / "shani-link" / "calls.json"


def default_line() -> str:
    try:
        value = json.loads(_prefs_file().read_text()).get("default-line", "auto")
    except (OSError, ValueError, AttributeError):
        return "auto"
    return value if value in dict(LINE_CHOICES) else "auto"


def set_default_line(line: str) -> None:
    path = _prefs_file()
    try:
        data = json.loads(path.read_text())
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data["default-line"] = line
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        logger.debug("could not remember the line", exc_info=True)


def dialable(text: str) -> str:
    """What may go on the dial pad from pasted text: a tel: link's number, or
    the digits, *, # and a leading + of anything else ("+91 (98) 765-43210")."""
    from shani_link import tel_uri
    t = (text or "").strip()
    if ":" in t:
        return tel_uri.parse(t).number
    kept = "".join(ch for ch in t if ch.isdigit() or ch in "*#")
    return ("+" + kept) if t.startswith("+") and kept else kept


def suggestions(typed: str, phone_book, limit: int = 4) -> "list[tuple[str, str]]":
    """(name, number) for contacts the keypad digits spell or appear in - the
    dial pad's matches as you type, yours before the phone's."""
    if len(contacts.digits(typed)) < 2:
        return []
    out = []
    for c in contacts.search("", limit=0, phone_book=phone_book or []):
        if c.numbers() and contacts.t9_matches(c, typed):
            hit = next((n for n in c.numbers() if contacts.digits(typed) in contacts.digits(n)),
                       c.numbers()[0])
            out.append((c.display_name(), hit))
            if len(out) >= limit:
                break
    return out


# --- the history ----------------------------------------------------------------------

ICONS = {"missed": "call-missed-symbolic", "in": "call-incoming-symbolic", "out": "call-outgoing-symbolic"}


class History:
    """The history list. `panel` gives it `call(number, line)`, `message(number)`,
    `add_contact(number)`, `add_to_existing(number)` and `name_for(number)`."""

    def __init__(self, panel: Any):
        self.panel = panel
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head = Gtk.Box(spacing=6)
        title = Gtk.Label(label="Recent calls", xalign=0.0, hexpand=True)
        title.add_css_class("heading")
        head.append(title)
        self.clear_button = Gtk.Button(label="Clear all", valign=Gtk.Align.CENTER)
        self.clear_button.add_css_class("flat")
        self.clear_button.connect("clicked", lambda _b: self.clear_all())
        head.append(self.clear_button)
        self.box.append(head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.box.append(self.body)
        self.cleared: List[dict] = []
        self.rows: List[dict] = []

    def load(self) -> None:
        self.rows = calls_log.rows(100)
        common.clear(self.body)
        self.clear_button.set_visible(bool(self.rows))
        if not self.rows:
            note = Gtk.Label(label="No calls yet. Calls on your landline, your phone and this "
                                   "computer's modem are listed here.", xalign=0.0, wrap=True)
            note.add_css_class("dim-label")
            self.body.append(note)
            return
        for day, items in calls_log.grouped(self.rows).items():
            label = Gtk.Label(label=day, xalign=0.0, margin_top=6)
            label.add_css_class("caption-heading")
            self.body.append(label)
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.add_css_class("boxed-list")
            for row in items:
                lb.append(self._row(row))
            self.body.append(lb)

    def who(self, row: dict) -> str:
        number = row.get("number", "")
        return self.panel.name_for(number) or row.get("name") or number or "Unknown caller"

    def _row(self, row: dict) -> Gtk.Widget:
        number = row.get("number", "")
        suffix = Gtk.Box(spacing=4)
        if number:
            call = Gtk.Button(icon_name="call-start-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=f"Call {number} back")
            call.add_css_class("flat")
            call.update_property([Gtk.AccessibleProperty.LABEL], [f"Call {number} back"])
            call.connect("clicked", lambda _b: self.panel.call(number, row.get("line", "")))
            suffix.append(call)
        suffix.append(self._menu(row))
        widget = common.row(self.who(row), calls_log.describe(row), suffix)
        icon = Gtk.Image(icon_name=ICONS["missed" if row.get("missed") else row.get("direction", "out")])
        if row.get("missed"):
            icon.add_css_class("error")
        if hasattr(widget, "add_prefix"):
            widget.add_prefix(icon)
        widget.call_row = row
        return widget

    def _menu(self, row: dict) -> Gtk.MenuButton:
        number = row.get("number", "")
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pop = Gtk.Popover(child=items)
        actions: "list[tuple[str, Callable[[], None]]]" = []
        if number:
            actions.append(("Send a message", lambda: self.panel.message(number)))
            _name, found = contacts.lookup(number, self.panel.book or None)
            if found is None or found.source != "local":
                actions.append(("Add to contacts", lambda: self.panel.add_contact(number)))
                actions.append(("Add to an existing contact", lambda: self.panel.add_to_existing(number)))
        actions.append(("Delete from the history", lambda: self.delete(row)))
        for label, act in actions:
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, a=act: (pop.popdown(), a()))
            items.append(b)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic", popover=pop, valign=Gtk.Align.CENTER,
                              tooltip_text="More")
        menu.add_css_class("flat")
        menu.actions = dict(actions)
        return menu

    def delete(self, row: dict) -> None:
        calls_log.delete(row.get("id", ""))
        self.load()

    def clear_all(self) -> None:
        self.cleared = calls_log.rows()
        calls_log.clear()
        self.load()
        self.panel.toast("Call history cleared", "Undo", self.undo_clear)

    def undo_clear(self) -> None:
        if self.cleared:
            calls_log.restore(self.cleared)
            self.cleared = []
            self.load()
