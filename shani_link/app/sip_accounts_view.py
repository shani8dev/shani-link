"""The SIP accounts manager, on the Calls page: add, edit, remove.

An ordinary internet SIP account (a provider's username, password, domain,
port and transport) sits beside the landline, the phone and the modem as one
more line a call can go out on. Everything here asks the service
(`app/sip_client.py`); the page never holds a password, and the service never
sends one back - editing an account leaves the password field empty to keep
the saved one.

Choices over typing wherever there is a choice: the transport is three
buttons, ports are filled in for you, and every field that must be typed
(only the account's own fields) says what it is for.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_link.app import common, sip_client  # noqa: E402

TRANSPORTS = (("udp", "UDP"), ("tcp", "TCP"), ("tls", "TLS"))


class AccountsBox:
    """The account list and its Add button. `panel` gives it `toast(title, button, on_button)`
    and `reload_lines()` (the dial pad re-reads which lines there are)."""

    def __init__(self, panel: Any):
        self.panel = panel
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head = Gtk.Box(spacing=6)
        title = Gtk.Label(label="SIP accounts", xalign=0.0, hexpand=True)
        title.add_css_class("heading")
        head.append(title)
        self.add_button = Gtk.Button(label="Add a SIP account…", valign=Gtk.Align.CENTER)
        self.add_button.connect("clicked", lambda _b: AccountDialog(self.panel, None, self.reload).show())
        head.append(self.add_button)
        self.box.append(head)
        self.note = Gtk.Label(label="Internet phone accounts from a provider you choose. "
                                    "Calls can go out on them like any other line.", xalign=0.0, wrap=True)
        self.note.add_css_class("dim-label")
        self.box.append(self.note)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.box.append(self.body)
        self.rows: List[dict] = []

    def reload(self) -> None:
        self.rows = sip_client.accounts()
        common.clear(self.body)
        for saved in self.rows:
            self.body.append(self._row(saved))
        if hasattr(self.panel, "reload_lines"):
            self.panel.reload_lines()

    def _row(self, saved: dict) -> Gtk.Widget:
        where = f"{saved.get('username', '')}@{saved.get('domain', '')}"
        transport = str(saved.get("transport") or "udp").upper()
        port = saved.get("port") or 5060
        state = "On" if saved.get("enabled", True) else "Off"
        suffix = Gtk.Box(spacing=4)
        edit = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER)
        edit.connect("clicked", lambda _b: AccountDialog(self.panel, saved, self.reload).show())
        remove = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
        remove.add_css_class("destructive-action")
        remove.connect("clicked", lambda _b: self._remove(saved))
        suffix.append(edit)
        suffix.append(remove)
        row = common.row(saved.get("name") or where,
                         f"{where} · {transport} port {port} · {state}", suffix)
        row.account = saved
        return row

    def _remove(self, saved: dict) -> None:
        gone = dict(saved)
        sip_client.remove_account(str(saved.get("id") or ""))
        self.reload()
        self.panel.toast(f"Removed {gone.get('name') or 'SIP account'}", "Undo",
                         lambda: sip_client.add_account(gone) and self.reload())


class AccountDialog:
    """One account's own fields, and nothing else to type in."""

    def __init__(self, panel: Any, saved: Optional[dict], done: Callable[[], None]):
        self.panel = panel
        self.saved = saved
        self.done = done
        self.error = Gtk.Label(xalign=0.0, wrap=True)
        self.error.add_css_class("error")
        self.error.set_visible(False)
        self.fields = {}
        self.window = Gtk.Window(title="Edit a SIP account" if saved else "Add a SIP account",
                                 modal=True)
        self.window.set_default_size(440, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=16,
                      margin_end=16, margin_top=16, margin_bottom=16)
        self.window.set_child(box)
        if saved is None:
            box.append(Gtk.Label(label="Your provider gives you these four things: a username, "
                                       "a password, and a server name. The port and transport "
                                       "below are usually right as they are.", xalign=0.0, wrap=True))
        for key, label, hint, secret in (
                ("name", "Name", "What calls it while you pick a line (optional)", False),
                ("username", "Username", "The account name your provider gave you", False),
                ("password", "Password", "Leave empty to keep the saved one" if saved
                 else "The password your provider gave you", True),
                ("domain", "Server (domain)", "Like sip.example.com", False)):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            row.append(Gtk.Label(label=label, xalign=0.0))
            entry = Gtk.Entry()
            if secret:
                entry.set_visibility(False)
            entry.set_placeholder_text(hint)
            entry.set_text(str((saved or {}).get(key) or ""))
            row.append(entry)
            box.append(row)
            self.fields[key] = entry
        # Transport: three buttons, not a typed word.
        box.append(Gtk.Label(label="Transport", xalign=0.0))
        transport_row = Gtk.Box(spacing=4)
        self.transport_buttons = {}
        current = str((saved or {}).get("transport") or "udp").lower()
        for key, label in TRANSPORTS:
            button = Gtk.ToggleButton(label=label)
            button.set_active(key == current)
            button.connect("toggled", lambda b, k=key: self._pick_transport(k))
            transport_row.append(button)
            self.transport_buttons[key] = button
        self.transport = current
        box.append(transport_row)
        port_row = Gtk.Box(spacing=8)
        port_row.append(Gtk.Label(label="Port", xalign=0.0))
        self.port = Gtk.Entry()
        self.port.set_text(str((saved or {}).get("port") or 5060))
        self.port.set_width_chars(8)
        port_row.append(self.port)
        box.append(port_row)
        box.append(self.error)
        actions = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.window.close())
        save = Gtk.Button(label="Save", sensitive=True)
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _b: self._save())
        actions.append(cancel)
        actions.append(save)
        box.append(actions)

    def _pick_transport(self, key: str) -> None:
        self.transport = key
        for other, button in self.transport_buttons.items():
            if other != key:
                button.set_active(False)

    def _save(self) -> None:
        fields = {key: entry.get_text().strip() for key, entry in self.fields.items()}
        fields["transport"] = self.transport
        fields["port"] = self.port.get_text().strip()
        fields["enabled"] = True
        fields["register"] = True
        try:
            if self.saved:
                sip_client.update_account(str(self.saved.get("id") or ""), fields)
            else:
                sip_client.add_account(fields)
        except RuntimeError as exc:
            self.error.set_text(str(exc))
            self.error.set_visible(True)
            return
        self.window.close()
        self.done()

    def show(self) -> None:
        self.window.present()
