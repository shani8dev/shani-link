"""The app's window, built for real with every page on the private display.

In a child process with nothing of the machine reachable: no session or system
bus (both addresses point at a socket that does not exist), an empty PATH (no
bluetoothctl, adb, busctl, wpctl...), and its own XDG homes. So every page meets
the "nothing is here" answers, which is exactly where a page that raises takes
the window down - and nothing touches a real phone, watch or adapter.
"""
import json
import os
import subprocess
import sys

import pytest

from _gui import needs_display

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HARNESS = r'''
import json, sys, time
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
Adw.init()
from shani_link.app import window as W
from shani_link.app.switches import Switches


def pump(seconds):
    ctx = GLib.MainContext.default(); end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


def texts(w):
    out = []
    def walk(w):
        if isinstance(w, Gtk.Label): out.append(w.get_label())
        if isinstance(w, Adw.ActionRow): out.extend([w.get_title(), w.get_subtitle() or ""])
        if isinstance(w, Adw.StatusPage): out.extend([w.get_title() or "", w.get_description() or ""])
        c = w.get_first_child()
        while c is not None:
            walk(c); c = c.get_next_sibling()
    walk(w)
    return [t for t in out if t]


win = W.LinkWindow(switches=Switches(bus=False))
win.present()
out = {"title": win.get_title(), "sidebar": [r.page_name for r in win.rows.values()], "pages": {}}
for name, *_ in W.PAGES:
    widget = win.show_page(name)
    pump(1.5)
    status = getattr(widget, "status", None)
    out["pages"][name] = {"shown": win.stack.get_visible_child_name(), "title": win.title.get_title(),
                          "status": status() if callable(status) else None, "texts": texts(widget)}
for tab, *_ in W.DEVICE_TABS:
    widget = win.show_page("devices")
    widget.stack.set_visible_child_name(tab)
    pump(1.5)
    out["pages"]["devices/" + tab] = {"texts": texts(widget.tabs[tab]),
                                      "status": widget.tabs[tab].status()}
win.show_page("wearables")
out["alias"] = win.current
win.reload(); pump(0.5)
out["after_reload"] = win.current
win.open_connect("")
pump(0.5)
out["connect"] = texts(win.connect_window)
win.connect_window.close(); win.close(); pump(0.3)
print("RESULT " + json.dumps(out))
'''


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("app")
    (tmp / "bin").mkdir()
    env = dict(os.environ, PYTHONPATH=ROOT, PATH=str(tmp / "bin"), HOME=str(tmp / "home"),
               XDG_CONFIG_HOME=str(tmp / "config"), XDG_STATE_HOME=str(tmp / "state"),
               XDG_DATA_HOME=str(tmp / "data"),
               DBUS_SESSION_BUS_ADDRESS=f"unix:path={tmp}/no-session-bus",
               DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={tmp}/no-system-bus",
               GSETTINGS_BACKEND="memory", PYTHONDONTWRITEBYTECODE="1")
    script = tmp / "harness.py"
    script.write_text(HARNESS)
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          timeout=120, env=env, cwd=str(tmp))
    for line in done.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise AssertionError(f"the window did not build:\n{done.stdout[-3000:]}\n{done.stderr[-5000:]}")


@needs_display
def test_the_window_is_shani_link_with_every_page_in_its_sidebar(built):
    assert built["title"] == "Shani Link"
    assert built["sidebar"] == ["phone", "watch", "devices", "radio", "nfc", "remote", "background"]


@needs_display
def test_every_page_builds_and_says_something(built):
    for name, page in built["pages"].items():
        if "/" not in name:
            assert page["shown"] == name, (name, page)
        assert page["texts"], f"{name} shows nothing at all"
        joined = " ".join(page["texts"])
        assert "could not be shown" not in joined, (name, joined[:800])
        assert page["status"] in ("ok", "attention", "unknown", "off"), (name, page["status"])


@needs_display
def test_no_page_speaks_for_an_assistant(built):
    """The consent banners stayed in Chronoa: nothing here asks to "Let Chronoa"."""
    for name, page in built["pages"].items():
        joined = " ".join(page["texts"])
        assert "Let Chronoa" not in joined and "Privacy settings" not in joined, (name, joined[:600])


@needs_display
def test_pages_meet_an_empty_machine_with_its_reason(built):
    pages = built["pages"]
    assert any("not installed" in t for t in pages["devices/android"]["texts"]), pages["devices/android"]
    assert any("No phone" in t or "not paired" in t.lower() or "No phone link" in t
               for t in pages["phone"]["texts"] + pages["devices/paired"]["texts"])
    assert any("Bluetooth did not answer" in t or "No Bluetooth devices" in t or "Reading" in t
               for t in pages["watch"]["texts"]), pages["watch"]["texts"]
    # only the switches that are on unless turned off: media keys, and the
    # computer's own modem ringing (which never rings without a voice modem)
    assert pages["background"]["status"] == "ok"
    assert any("2 of 13 on" in t for t in pages["background"]["texts"]), pages["background"]["texts"]   # 13: + receive files over Wi-Fi, + share the clipboard (both off)


@needs_display
def test_old_panel_names_land_on_their_page_and_connect_opens(built):
    assert built["alias"] == "watch" and built["after_reload"] == "watch"
    assert "What are you connecting?" in built["connect"], built["connect"]
