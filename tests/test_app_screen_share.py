"""Screen share: the Screen tab of Devices, over `shani_link.screen_stream`.

Moved from Shani Chronoa's tests/test_screen_share.py with the page. There, every
control went through the assistant's dispatcher and its `screen-share-enabled`
consent; here a press runs `screen_share.act`, which asks the engine directly -
the desktop's ScreenCast portal still asks the person what to share.

**The check is slow, and the page is honest about it**: it runs on a worker and
reports on an idle, so the window keeps turning.
"""

from __future__ import annotations

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import pytest  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from shani_link.app import screen_share as surface_mod  # noqa: E402

from _gui import needs_display  # noqa: E402

pytestmark = needs_display


def _texts(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        _texts(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _buttons(child, out)
        child = child.get_next_sibling()
    return out


class _App:
    window = None


def _build():
    page = surface_mod.build(_App())
    return page, getattr(page, "surface", None)


def _pump(seconds: float = 5.0):
    """Run the loop until the worker thread is done and its answer has landed.

    Stopping at the first moment nothing was pending raced the worker: under
    load the thread had not yet queued its idle, and the test read the page
    before the answer was on it (the flaky failures this file was known for)."""
    import threading
    import time
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if not any(t.name == "link-screen-share" and t.is_alive() for t in threading.enumerate()):
            while context.pending():
                context.iteration(False)
            return
        time.sleep(0.005)


@pytest.fixture
def dispatched(monkeypatch):
    """Record what the panel sends through the real dispatch path.

    `act` is stubbed so the test does not need a portal, a stream or a phone -
    but it is stubbed **at the seam the page calls**, so a page that stopped
    calling it would send nothing and fail here.
    """
    calls = []

    def fake(action):
        calls.append(action)
        return f"answer for {action}"

    monkeypatch.setattr(surface_mod, "act", fake)
    return calls


# --- the controls go through the real dispatch path -------------------------


def test_check_start_and_stop_all_go_through_act(dispatched):
    """**Pressed, not called.** `surface._run(...)` is what a first version did.

    It passed with `surface._run` renamed, which is not a test of a button - and
    this repo has recorded the dead-control defect five times. Emitting
    `clicked` measures the wiring rather than the behaviour behind it.
    """
    page, surface = _build()
    mine = [b for b in _buttons(page) if b.get_label() in ("Check", "Start", "Stop")]
    assert len(mine) == 3, [b.get_label() for b in _buttons(page)]
    for action_button in mine:
        action_button.emit("clicked")
        _pump()
    assert dispatched == ["status", "start", "stop"], dispatched


def test_the_answer_is_shown_in_full_not_truncated(dispatched):
    page, surface = _build()
    surface._run("status")
    _pump()
    shown = " ".join(t for t in _texts(page) if t)
    assert "answer for status" in shown, shown


def test_the_page_offers_to_connect_a_phone(monkeypatch):
    """Check can only say a phone is missing; this is the way to get one."""
    fired = []

    class App:
        window = None

        def activate_action(self, name, value=None):
            fired.append((name, value))

    page = surface_mod.build(App())
    button = next(b for b in _buttons(page) if b.get_label() == "Connect a phone")
    button.emit("clicked")
    assert fired and fired[0][0] == "connect-device", fired
    assert fired[0][1].unpack() == "android", fired


def test_the_touch_switch_is_on_the_page_and_off_by_default(monkeypatch):
    page, surface = _build()
    switch = getattr(surface, "_touch", None)
    assert switch is not None, "no touch switch on the page"
    assert switch.toggle.get_active() is False
    monkeypatch.setitem(surface_mod._TOUCH, "on", False)
    switch.toggle.set_active(True)
    assert surface_mod._TOUCH["on"] is True, "the switch must reach the next stream"
    monkeypatch.setitem(surface_mod._TOUCH, "on", False)


def test_start_passes_the_touch_switch_to_the_stream(monkeypatch):
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (True, ""))
    monkeypatch.setattr(screen_stream, "phone_serial", lambda: ("R5CT10", ""))
    seen = {}

    class Proc:
        def __init__(self, argv, **kw):
            seen["env"] = kw["env"]

        def poll(self):
            return None

    monkeypatch.setattr(surface_mod.subprocess, "Popen", Proc)
    monkeypatch.setitem(surface_mod._PROC, "proc", None)
    monkeypatch.setitem(surface_mod._TOUCH, "on", True)
    surface_mod.act("start")
    assert seen["env"]["SHANI_LINK_SCREEN_TOUCH"] == "1", seen["env"]
    monkeypatch.setitem(surface_mod._TOUCH, "on", False)


def test_a_machine_that_cannot_share_says_what_is_missing(monkeypatch):
    """Not started, and the reason - the engine names the missing package."""
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (False, "gstreamer has no vp8enc"))
    monkeypatch.setattr(surface_mod.subprocess, "Popen", lambda *a, **k: pytest.fail("started anyway"))
    assert "vp8enc" in surface_mod.act("status")
    assert surface_mod.act("start").startswith("Not started") and "vp8enc" in surface_mod.act("start")


def test_no_bluetooth_network_is_its_own_reason(monkeypatch):
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (True, ""))
    monkeypatch.setattr(screen_stream, "phone_serial", lambda: ("", "no Android device is connected"))
    monkeypatch.setattr(screen_stream, "bluetooth_address", lambda: None)
    said = surface_mod.act("status")
    # both roads are named: "not on Bluetooth" alone leaves a person hunting
    # for a setting when the answer is a phone on a cable or Wi-Fi
    assert "not sharing its network over Bluetooth" in said, said
    assert "no Android device is connected" in said, said


def test_the_road_is_preferred_and_named(monkeypatch):
    """A phone adb can reach is the sturdier road, so it is the one offered."""
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (True, ""))
    monkeypatch.setattr(screen_stream, "phone_serial", lambda: ("R5CT10", ""))
    monkeypatch.setattr(screen_stream, "bluetooth_address", lambda: "192.168.44.2")
    said = surface_mod.act("status")
    assert "127.0.0.1:8090" in said and "USB cable" in said, said
    assert "192.168.44.2" not in said, said


def test_a_wireless_phone_is_named_as_wi_fi(monkeypatch):
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (True, ""))
    monkeypatch.setattr(screen_stream, "phone_serial", lambda: ("192.168.1.20:41000", ""))
    said = surface_mod.act("status")
    assert "127.0.0.1:8090" in said and "over Wi-Fi" in said, said


def test_start_tells_the_stream_which_road_it_was_given(monkeypatch):
    """The process is told, not left to probe again and disagree with the page."""
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (True, ""))
    monkeypatch.setattr(screen_stream, "phone_serial", lambda: ("192.168.1.20:41000", ""))
    monkeypatch.setattr(screen_stream, "bluetooth_address", lambda: "192.168.44.2")
    seen = {}

    class Proc:
        def __init__(self, argv, **kw):
            seen["env"] = kw["env"]
            seen["argv"] = argv

        def poll(self):
            return None

    monkeypatch.setattr(surface_mod.subprocess, "Popen", Proc)
    monkeypatch.setitem(surface_mod._PROC, "proc", None)
    assert "http://127.0.0.1:8090" in surface_mod.act("start")
    assert seen["env"]["SHANI_LINK_SCREEN_TRANSPORT"] == "wifi", seen["env"]
    assert seen["argv"][1:] == ["-m", "shani_link.screen_stream"], seen["argv"]


def test_a_stream_that_ended_says_the_last_thing_it_did(monkeypatch):
    """The phone shows a frozen picture; the page has to know why."""
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (True, ""))
    monkeypatch.setattr(screen_stream, "phone_serial", lambda: ("", "no Android device is connected"))
    monkeypatch.setattr(screen_stream, "bluetooth_address", lambda: "192.168.44.2")
    log = surface_mod.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("screen stream: http://192.168.44.2:8090\n"
                   "the screen share was ended on this computer; press Start to ask again\n",
                   encoding="utf-8")

    class Dead:
        def poll(self):
            return 0

    monkeypatch.setitem(surface_mod._PROC, "proc", Dead())
    said = surface_mod.act("status")
    assert said.startswith("The stream has stopped"), said
    assert "press Start" in said, said
    assert surface_mod._PROC.get("proc") is None, "a finished stream must not be reported as running"


def test_a_running_stream_is_reported_as_running(monkeypatch):
    """Not "cannot be shared right now": it is sharing, and the phone has the picture."""
    class Alive:
        def poll(self):
            return None

    monkeypatch.setitem(surface_mod._PROC, "proc", Alive())
    monkeypatch.setitem(surface_mod._PROC, "where", "http://127.0.0.1:8090")
    said = surface_mod.act("status")
    assert "right now" in said and "127.0.0.1:8090" in said, said
    monkeypatch.setitem(surface_mod._PROC, "proc", None)


def test_start_runs_the_stream_and_stop_ends_that_process(monkeypatch):
    """The stream is its own process, started once and stopped by its handle."""
    from shani_link import screen_stream
    monkeypatch.setattr(screen_stream, "gstreamer_ready", lambda: (True, ""))
    monkeypatch.setattr(screen_stream, "phone_serial", lambda: ("", "no Android device is connected"))
    monkeypatch.setattr(screen_stream, "bluetooth_address", lambda: "192.168.44.2")
    started = []

    class Proc:
        def __init__(self, argv, **kw):
            started.append((argv, kw["env"].get("PYTHONPATH", "")))
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            self.alive = False

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(surface_mod.subprocess, "Popen", Proc)
    monkeypatch.setitem(surface_mod._PROC, "proc", None)
    said = surface_mod.act("start")
    assert "http://192.168.44.2:" in said, said
    assert started[0][0][1:] == ["-m", "shani_link.screen_stream"], started
    assert "Already sharing" in surface_mod.act("start") and len(started) == 1
    assert surface_mod.act("stop").startswith("Stopped")
    assert surface_mod.act("stop") == "Nothing is being shared."


def test_a_broken_engine_is_a_sentence_not_a_freeze(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("the launcher would not start")
    monkeypatch.setattr(surface_mod, "act", boom)
    page, surface = _build()
    surface._run("start")
    _pump()
    shown = " ".join(t for t in _texts(page) if t)
    assert "failed" in shown and "launcher" in shown, shown


# --- slow, and the window stays alive ---------------------------------------


def test_the_buttons_come_back_after_a_slow_action(dispatched):
    """A 90-second probe must leave the panel usable."""
    from shani_link.app import screen_share as mod
    # drive the worker the way the panel does, then let the idle fire
    page, surface = _build()
    original = mod.threading.Thread
    mod.threading.Thread = lambda **kw: original(
        target=kw["target"], daemon=kw.get("daemon", True), name=kw.get("name"))
    try:
        surface._run("status")
        for _ in range(50):
            _pump()
            if surface._busy is False:
                break
    finally:
        mod.threading.Thread = original
    mine = [b for b in _buttons(page) if b.get_label() in ("Check", "Start", "Stop")]
    assert len(mine) == 3, [b.get_label() for b in _buttons(page)]
    for action_button in mine:
        assert action_button.get_sensitive(), (
            f"{action_button.get_label()} stayed disabled after the action")


def test_the_panel_does_not_freeze_the_loop_during_a_probe(monkeypatch):
    """The probe runs on a worker - the window must still turn while it runs.

    `quick_ask.py` records that `asyncio.run()` in a GTK handler blocks it, and
    the same failure here would freeze the window for up to 90 seconds.
    """
    page, surface = _build()
    monkeypatch.setattr(surface_mod, "_pump_noop", lambda: None, raising=False)
    # A worker is what makes this true; assert one is started rather than that
    # the work happened inline.
    started = []
    real_thread = surface_mod.threading.Thread

    class _Recorder:
        def __init__(self, **kwargs):
            started.append(kwargs.get("name"))

        def start(self):
            pass

    surface_mod.threading.Thread = _Recorder
    try:
        surface._run("status")
    finally:
        surface_mod.threading.Thread = real_thread
    assert started == ["link-screen-share"], started
