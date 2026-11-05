"""A notification for each missed call, with "Call back" and "Message".

GNOME Calls left a missed call on screen until it was dealt with; this does the
same for every line (the landline, the phone, the modem). It is told by the
call history (`calls_log.on_missed`) the moment a missed call is logged, so it
needs no watching of its own.

- **Call back** calls the number on the line the call came in on. Pressing it
  is the person's decision, the same as pressing Call on the dial pad.
- **Message** opens Shani Link's Messages tab addressed to that number
  (`shani-link-app --message=NUMBER`); nothing is sent until they press Send.

`category=call.unanswered` is the freedesktop hint for exactly this, and the
desktop entry lets the notification server group it under Shani Link.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

NOTIFY = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
          "org.freedesktop.Notifications")
from shani_link.notify import APP_ENTRY  # noqa: E402


def text_for(row: dict) -> "tuple[str, str]":
    """(title, body) for a missed call."""
    from shani_link import calls_log, contacts
    number = row.get("number", "")
    name = row.get("name") or contacts.name_for(number)
    who = name or number or "Unknown caller"
    line = calls_log.LINE_NAMES.get(row.get("line", ""), "")
    at = time.strftime("%H:%M", time.localtime(row.get("when", time.time())))
    body = " · ".join(b for b in ((number if name else ""), line, at) if b)
    return f"Missed call: {who}", body


def _launch_app(args: List[str]) -> None:
    from gi.repository import Gio
    Gio.Subprocess.new(["shani-link-app"] + args, Gio.SubprocessFlags.NONE)


class MissedCallNotifier:
    def __init__(self, bus: Any, dial: Optional[Callable[[str, str], Any]] = None,
                 launch: Optional[Callable[[List[str]], None]] = None):
        from gi.repository import Gio
        self.bus = bus
        self.shown: Dict[int, dict] = {}
        if dial is None:
            from shani_link import lines
            dial = lines.dial
        self.dial = dial
        self.launch = launch or _launch_app
        self.sub = bus.signal_subscribe(NOTIFY[0], NOTIFY[2], "ActionInvoked", NOTIFY[1], None,
                                        Gio.DBusSignalFlags.NONE, self._on_action)
        self.sub_closed = bus.signal_subscribe(NOTIFY[0], NOTIFY[2], "NotificationClosed", NOTIFY[1],
                                               None, Gio.DBusSignalFlags.NONE, self._on_closed)

    def stop(self) -> None:
        self.bus.signal_unsubscribe(self.sub)
        self.bus.signal_unsubscribe(self.sub_closed)

    def post(self, row: dict) -> int:
        from gi.repository import GLib
        title, body = text_for(row)
        actions = ["call-back", "Call back"]
        if row.get("number"):
            actions += ["message", "Message"]
        else:
            actions = []                    # a withheld number cannot be called or texted back
        hints = {"category": GLib.Variant("s", "call.unanswered"),
                 "desktop-entry": GLib.Variant("s", APP_ENTRY),
                 "urgency": GLib.Variant("y", 1)}
        try:
            reply = self.bus.call_sync(*NOTIFY, "Notify", GLib.Variant("(susssasa{sv}i)", (
                "Shani Link", 0, "call-missed-symbolic", title, body, actions, hints, 0)),
                GLib.VariantType.new("(u)"), 0, 5000, None)
        except Exception:  # noqa: BLE001 - no notification server: the history still has it
            logger.info("missed call: no notification", exc_info=True)
            return 0
        nid = int(reply.unpack()[0])
        self.shown[nid] = row
        return nid

    def _on_action(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        nid, key = params.unpack()
        row = self.shown.pop(nid, None)
        if row is None or not row.get("number"):
            return
        if key == "call-back":
            try:
                self.dial(row.get("line", ""), row["number"])
            except Exception as exc:  # noqa: BLE001 - that line may be gone: open the dial pad
                logger.info("missed call: call back on %s failed (%s)", row.get("line"), exc)
                self.launch(["--dial", row["number"]])
        elif key == "message":
            self.launch(["--message", row["number"]])

    def _on_closed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        self.shown.pop(params.unpack()[0], None)
