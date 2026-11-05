"""This computer on the phone's own link: the Phone link tab, over `lan_link`.

The KDE Connect LAN protocol: see what is on the network, make this computer
findable, pair, push a file (the actions are `shani_link.lan_phone`'s, which
Chronoa's `lan_phone` skill also calls).
Moved here from Shani Chronoa, minus the assistant's `lan-phone-enabled`
consent: looking for phones and sending a file happen when the person presses
the button, and that is the asking.

**Pairing state first, and the way to change it beside it.** The top row is the
stored pairing, and "Pair a phone" opens Connect a device on its KDE Connect
route, where the phone's owner taps Accept.

**Everything slow is off the GTK thread.** `listen_once` waits on the network
for as long as it is asked, and a file push waits on a phone that may never
dial in. On the main loop that is a frozen window, which is worse than no button.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

from shani_link import lan_link, lan_phone
from shani_link.app import common
from shani_link.app.common import plural

logger = logging.getLogger(__name__)

TITLE = "Phone link"
ICON = "phone-symbolic"
SUBTITLE = ("Send files to your phone over Wi-Fi, with the KDE Connect app "
            "on the phone (Android or iPhone).")

LOOK_SECONDS = 5.0

NOT_PAIRED = "No phone paired yet"
NOT_PAIRED_WHY = ("Open KDE Connect on your phone, on the same Wi-Fi as this "
                  "computer, then press Pair a phone and tap Accept on the phone.")


def _hosts() -> str:
    """The phone this computer last saw, or "" - `lan_phone`'s one remembered
    address, which Chronoa's skill reads too."""
    return lan_phone.default_host()


def _off_thread(work: Callable[[], Any],
                done: Callable[[Any], None]) -> None:
    """`work()` on a worker; `done(result)` on the GTK thread.

    `listen_once` waits on the network and a file push waits on a phone that may
    never dial in, so neither may run on the loop that draws the window.
    """

    def runner() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown in the panel
            logger.warning("phone link work failed", exc_info=True)
            result = exc
        GLib.idle_add(finish, result)

    def finish(result) -> bool:
        try:
            done(result)
        except Exception:  # noqa: BLE001
            logger.warning("could not show the result", exc_info=True)
        return False

    threading.Thread(target=runner, daemon=True).start()


class _PhoneLinkView(Gtk.Box):
    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._app = app
        self.status_recorder = common.StatusRecorder()
        self._devices: List[dict] = []

        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.append(self._status_slot)
        self.append(self._find_row())
        self._list_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._list_slot.set_vexpand(True)
        self.append(self._list_slot)

        self._refresh_status()
        self._refresh_list()

    # -- the honest top line ------------------------------------------------

    def _refresh_status(self) -> None:
        common.clear(self._status_slot)
        state, why = lan_link.pairing_status()
        buttons = Gtk.Box(spacing=8, margin_start=12, margin_end=12)
        if state == "paired":
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK, f"Paired {why.split(';')[0]}",
                "Only this phone is accepted from now on."))
            buttons.append(self._connect_button("Pair another phone"))
            forget = Gtk.Button(label="Forget this phone")
            forget.connect("clicked", self._on_forget)
            buttons.append(forget)
        else:
            # Not paired is where everybody starts - grey, not a red alarm.
            self.status_recorder.set(common.STATUS_OFF)
            first = common.row(NOT_PAIRED, NOT_PAIRED_WHY)
            first.set_margin_start(12)
            first.set_margin_end(12)
            self._status_slot.append(first)
            buttons.append(self._connect_button("Pair a phone", suggested=True))
        self._status_slot.append(buttons)
        host = _hosts()
        if host:
            seen = _dim(f"Last phone seen at {host}.")
            seen.set_margin_start(12)
            seen.set_margin_top(6)
            self._status_slot.append(seen)

    def _connect_button(self, label: str, suggested: bool = False) -> Gtk.Button:
        button = Gtk.Button(label=label)
        if suggested:
            button.add_css_class("suggested-action")
        button.set_tooltip_text("Opens Connect a device; the phone asks its owner to accept")

        def clicked(*_a) -> None:
            activate = getattr(self._app, "activate_action", None)
            if callable(activate):
                activate("connect-device", GLib.Variant("s", "kdeconnect"))
        button.connect("clicked", clicked)
        return button

    def _on_forget(self, *_a) -> None:
        lan_link.forget_pairing()
        self._refresh_status()

    # -- discovery ----------------------------------------------------------

    def _find_row(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top"):
            getattr(box, f"set_margin_{side}")(12 if side in ("start", "end") else 10)
        self.find = Gtk.Button(label="Look for phones")
        self.find.set_tooltip_text("Listen for a phone announcing itself")
        _label(self.find, "Look for phones on this network")
        self.find.connect("clicked", self._on_find)
        box.append(self.find)
        self.announce = Gtk.Button(label="Make this computer findable")
        self.announce.set_tooltip_text("Tell phones on this network that this computer is here")
        _label(self.announce, "Make this computer findable by a phone's KDE Connect app")
        self.announce.connect("clicked", self._on_announce)
        box.append(self.announce)
        self.find_note = _dim("")
        box.append(self.find_note)
        return box

    def _on_find(self, _button: Gtk.Button) -> None:
        self.find.set_sensitive(False)
        self.find.set_label("Looking...")
        self.find_note.set_label("Listening on the network.")
        _off_thread(lambda: lan_link.listen_once(LOOK_SECONDS), self._found)

    def _found(self, result) -> None:
        self.find.set_sensitive(True)
        self.find.set_label("Look for phones")
        if isinstance(result, Exception):
            self.find_note.set_label(f"Could not listen: {result}")
            return
        self._devices = [entry for entry in result if isinstance(entry, dict)]
        if not self._devices:
            # **A real answer, not a failure** — the same sentence the skill
            # gives, because both are true and only one of them is actionable.
            self.find_note.set_label(
                f"No phone answered in {LOOK_SECONDS:.0f} seconds. Check that KDE "
                "Connect is open on the phone and that both are on the same Wi-Fi "
                "(office and university Wi-Fi often block this).")
        else:
            self.find_note.set_label(
                f"{plural(len(self._devices), 'device')} answered. Remembering the "
                f"first one so the phone has somewhere to go.")
            _remember(self._devices[0].get("address", ""))
        self._refresh_list()
        self._refresh_status()

    def _on_announce(self, _button: Gtk.Button) -> None:
        """Say "this computer is here" on the network, so the phone's KDE
        Connect app lists it and offers to pair from there."""
        self.announce.set_sensitive(False)
        self.find_note.set_label("Announcing this computer on the network...")

        def done(result) -> None:
            self.announce.set_sensitive(True)
            self.find_note.set_label(f"Could not announce this computer: {result}"
                                     if isinstance(result, Exception) else str(result))
        _off_thread(lan_phone.announce, done)

    def show_devices(self, entries: List[dict]) -> None:
        """Put a discovered list on screen. **The seam a test needs.**

        The first version exposed no way in: `_refresh_list()` was reachable on
        the page but the state it read (`self._devices`) was not, so setting
        `page._devices = [...]` created an attribute on the *page* that the
        *view* never looked at, and the panel stayed on its empty state forever.
        A panel that cannot be driven to a state it can be in is a panel whose
        that-state behaviour is untestable - which is how it shipped with a
        device list nobody could get into.

        One method rather than a settable attribute, because two writes that
        must agree ("set this, then refresh") is two places for them to fall out
        of step, and only one of them is the one that reads it.
        """
        self._devices = [entry for entry in entries if isinstance(entry, dict)]
        self._refresh_list()

    def _refresh_list(self) -> None:
        _replace(self._list_slot, self._devices_list())

    def _devices_list(self) -> Gtk.Widget:
        if not self._devices:
            return nothing_state(
                "phone-symbolic", "No phone found yet",
                "Open KDE Connect on your phone, then press Look for phones.")
        group = common.group()
        group.set_margin_start(12)
        group.set_margin_end(12)
        for entry in self._devices:
            group.add(self._device_row(entry))
        return common.scrolled(group, 300)

    def _device_row(self, entry: dict) -> Gtk.Widget:
        name = str(entry.get("name") or entry.get("address") or "a device")
        address = str(entry.get("address") or "")
        row = common.row(name, address)
        row.set_subtitle_selectable(True)
        _label(row, f"{name} at {address}")
        send = Gtk.Button(label="Send a file")
        send.add_css_class("flat")
        send.set_tooltip_text("Put one file on the phone")
        _label(send, f"Send a file to {name}")
        send.connect("clicked", lambda _b: self._on_send(address))
        row.add_suffix(send)
        return row

    # -- the two things a person actually does ------------------------------

    def _on_send(self, host: str) -> None:
        """Pick a file and push it.

        `lan_link.send_file` is a *listening socket*: the peer dials in and
        pulls the bytes. That means it can wait a long time, which is why this
        is off the loop, and it means "sent" is not "arrived" — the sentence
        says so.
        """
        dialog = Gtk.FileDialog(title="Send to your phone", modal=True)

        def chosen(dialog_, result) -> None:
            try:
                picked = dialog_.open_finish(result)
            except GLib.Error:
                return                     # dismissed is silence
            if picked is None:
                return
            path = picked.get_path()
            if not path:
                return
            self.find_note.set_label("Sending — accept it on the phone...")
            _off_thread(lambda: lan_link.send_file(host, path), self._sent)

        dialog.open(None, None, chosen)

    def _sent(self, result) -> None:
        if isinstance(result, Exception):
            self.find_note.set_label(f"Not sent: {result}")
            return
        sent, why = result
        self.find_note.set_label(
            f"{why} was offered to the phone." if sent else f"Not sent ({why}).")


def _remember(address: str) -> None:
    lan_phone.remember(address)


def _replace(container: Gtk.Widget, widget: Gtk.Widget) -> None:
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        container.remove(child)
        child = following
    container.append(widget)


def nothing_state(icon: str, title: str, description: str = "") -> Gtk.Widget:
    return common.empty_state(icon, title, description)


def _dim(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    label.add_css_class("dim-label")
    common.wrap_label(label, 64)
    return label


def _label(widget: Gtk.Widget, label: str) -> None:
    try:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    except Exception:  # noqa: BLE001
        logger.debug("no accessible label on %r", type(widget).__name__)


def build(app: Any) -> Gtk.Widget:
    view = _PhoneLinkView(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(view)
    for name in ("find", "find_note", "announce", "show_devices", "_refresh_status",
                 "_refresh_list", "_on_find", "_on_send", "_on_announce"):
        setattr(page, name, getattr(view, name))
    page.status = view.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "SUBTITLE", "build",
           "NOT_PAIRED", "LOOK_SECONDS"]