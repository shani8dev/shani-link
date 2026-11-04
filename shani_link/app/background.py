"""Background: what Shani Link does with no window open, one switch each.

These are not an assistant's permissions - they are the things the service
(`shani-link`) does on its own once turned on: ring for a call, show a text,
take a file the phone sends, keep the watch connected. Everything else in the
app happens when its button is pressed, so it has no switch.

The switches live in `shani_link.settings` and go through the running service
(`switches.py`), so a change reaches its watcher without a restart. The landline
has its own switch on the Phone page, where it is set up.
"""
from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_link import settings  # noqa: E402
from shani_link.app import common  # noqa: E402
from shani_link.app.switches import LABELS  # noqa: E402

TITLE = "Background"
ICON = "emblem-system-symbolic"
SUBTITLE = ("What Shani Link does on its own, with this window closed. Each "
            "runs only while its switch is on.")

#: Shown elsewhere: the landline is switched on where it is set up (Phone).
ELSEWHERE = {"landline-enabled"}

#: How the switches are grouped on the page.
GROUPS = (
    ("Phone", ("bluetooth-call-enabled", "phone-control-enabled",
               "phone-messages-read-enabled", "phone-messages-send-enabled",
               "phone-remote-enabled")),
    ("Watch", ("bluetooth-gatt-enabled", "watch-companion-enabled",
               "watch-nowplaying-enabled", "watch-weather-enabled")),
    ("Music", ("bluetooth-media-remote-enabled",)),
)


def keys_shown() -> "list[str]":
    return [k for _title, keys in GROUPS for k in keys]


def build(app: Any) -> Gtk.Widget:
    switches = getattr(app, "switches", None)
    page, set_content = common.surface(TITLE, SUBTITLE)
    recorder = common.StatusRecorder()
    page.status = recorder.status
    body = common.page_body(12)
    for side in ("start", "end"):
        getattr(body, f"set_margin_{side}")(12)
    try:
        values = switches.all() if switches is not None else settings.Settings().all()
        problem = ""
    except Exception as exc:  # noqa: BLE001 - said on the page
        values, problem = {}, str(exc)
    on = sum(1 for k in keys_shown() if values.get(k))
    body.append(recorder.row(
        common.STATUS_ATTENTION if problem else (common.STATUS_OK if on else common.STATUS_OFF),
        problem and f"Could not read the switches: {problem}" or
        (f"{on} of {len(keys_shown())} on" if on else "Nothing runs in the background"),
        "" if problem else "Turn on only what you want happening without a click."))
    page.rows = {}
    for title, keys in GROUPS:
        group = common.group(title)
        for key in keys:
            label, what = LABELS[key]

            def changed(active: bool, key=key) -> None:
                if switches is not None:
                    switches.set_bool(key, active)
                else:
                    settings.Settings().set_bool(key, active)
            row = common.switch_row(label, what, bool(values.get(key)), changed)
            row.key = key
            page.rows[key] = row
            group.add(row) if hasattr(group, "add") else group.append(row)
        body.append(group)
    set_content(common.scrolled(body))
    return page
