"""Screen share: this computer's screen, on a paired phone.

`shani_link.screen_stream` does it - the ScreenCast portal round trip, the VP8
pipeline, the one-viewer server. Moved here from Shani Chronoa, where the panel
went through the assistant's `screen_to_phone` skill and its
`screen-share-enabled` consent. Here pressing Start is the person's own act; the
desktop's ScreenCast portal still asks them what to share, every time.

**The stream is a separate process** (`python -m shani_link.screen_stream`),
because the portal round trip wants a GLib main loop of its own and a stream
should not die with a window. It is tracked by its process, never by name.

**The check is slow and the page says so** (it imports GStreamer and reads the
kernel's address table), so every action runs on a worker.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any, Optional

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_link.app import common  # noqa: E402

TITLE = "Screen share"
ICON = "send-to-symbolic"
SUBTITLE = "Show this computer's screen on your phone, over Bluetooth."


#: The running stream, tracked by its process (never found again by name).
_PROC: "dict[str, Optional[subprocess.Popen]]" = {}


def check() -> "tuple[str, str]":
    """(state, detail): "ready" and the address the phone opens, or why not."""
    from shani_link import screen_stream
    ok, why = screen_stream.gstreamer_ready()
    if not ok:
        return "cannot encode", why
    host = screen_stream.bluetooth_address()
    if not host:
        return ("no Bluetooth network",
                "the phone is not sharing its network over Bluetooth; turn on "
                "its tethering and connect to it first")
    return "ready", f"http://{host}:{screen_stream.PORT} would serve it"


def act(action: str) -> str:
    """status | start | stop, in a sentence."""
    if action == "status":
        state, detail = check()
        if state == "ready":
            return f"The screen can be shared: {detail}."
        return f"The screen cannot be shared right now ({state}): {detail}."
    proc = _PROC.get("proc")
    if action == "stop":
        if proc is None or proc.poll() is not None:
            return "Nothing is being shared."
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        _PROC["proc"] = None
        return "Stopped. The portal session is closed and nothing is streamed."
    if action != "start":
        return f"action must be status, start or stop, not {action!r}"
    state, detail = check()
    if state != "ready":
        return f"Not started: {state} ({detail}). Fix that first - the portal was not opened."
    if proc is not None and proc.poll() is None:
        return f"Already sharing on {detail}."
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ)
    env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        _PROC["proc"] = subprocess.Popen([sys.executable, "-m", "shani_link.screen_stream"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    except OSError as exc:
        return f"The stream would not start ({exc})."
    return (f"Sharing the screen on {detail}. Open that address on the phone. "
            "The desktop asked you first and will ask again next time.")


class _ShareSurface:
    def __init__(self, app: Any, set_content) -> None:
        self._app = app
        self._body = common.page_body()
        for side in ("start", "end", "top", "bottom"):
            getattr(self._body, f"set_margin_{side}")(12)
        set_content(common.scrolled(self._body))

        self._group = common.group()
        self._body.append(self._group)
        self._row = common.row("Share this screen with your phone",
                               "First turn on Bluetooth tethering on the phone and connect "
                               "this computer to it. You choose what to share; nothing "
                               "leaves the Bluetooth link.", suffix=self._buttons())
        self._group.add(self._row)
        #: What the last action said, in full - a truncated one-line label is not
        #: a report somebody can act on.
        self._report = common.row("What happened last", "(nothing yet)")
        _report_group = common.group()
        _report_group.add(self._report)
        _report_group.set_visible(False)          # until something has happened
        self._report_group = _report_group
        self._body.append(_report_group)
        self._busy = False

    def _buttons(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._check = Gtk.Button(label="Check")
        self._check.set_tooltip_text("See whether this computer can share its screen now (can take a minute)")
        self._check.connect("clicked", lambda _b: self._run("status"))

        self._start = Gtk.Button(label="Start")
        self._start.set_tooltip_text("Open the portal and stream to the paired phone")
        self._start.connect("clicked", lambda _b: self._run("start"))

        self._stop = Gtk.Button(label="Stop")
        self._stop.set_tooltip_text("Close the portal session and stop streaming")
        self._stop.connect("clicked", lambda _b: self._run("stop"))

        for widget in (self._check, self._start, self._stop):
            box.append(widget)
        return box

    # -- the controls --------------------------------------------------------

    def _run(self, action: str) -> None:
        """One action, off the drawing thread; the answer goes on the page."""
        if self._busy:
            return
        self._busy = True
        for widget in (self._check, self._start, self._stop):
            widget.set_sensitive(False)
        self._say({"status": "Checking…", "start": "Starting…", "stop": "Stopping…"}.get(action, f"{action}…"))

        def work():
            try:
                text = (act(action) or "").strip() or f"{action} produced no answer"
            except Exception as exc:  # noqa: BLE001 - a worker must still say something
                text = f"{action} failed: {type(exc).__name__}: {exc}"
            GLib.idle_add(lambda: (self._finished(action, text), False)[1])

        threading.Thread(target=work, daemon=True,
                         name="link-screen-share").start()

    def _finished(self, action: str, text: str) -> None:
        self._busy = False
        for widget in (self._check, self._start, self._stop):
            widget.set_sensitive(True)
        self._say(text)

    def _say(self, text: str) -> None:
        """The last answer, in full - and the panel is re-read on every showing.

        A worker's report is the only evidence a person gets from a 90-second
        probe, so a truncated label would hide the part that names the missing
        package.
        """
        self._report.set_subtitle(str(text))
        self._report_group.set_visible(True)


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _ShareSurface(app, set_content)
    page.surface = surface
    # **A word from the vocabulary, not an empty one.** An unknown word
    # renders an uncoloured dot, which is the failure
    # `common.status_row`'s closed vocabulary exists to prevent.
    page.status = lambda: common.STATUS_UNKNOWN
    return page


__all__ = ["TITLE", "ICON", "build", "act", "check"]
