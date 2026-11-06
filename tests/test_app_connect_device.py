"""The "Connect a device" window, clicked through on a private Broadway display.

Moved from Shani Chronoa's tests/test_connect_device_window.py with the window.
Its assistant switches ("Let Chronoa use this phone", "Let this phone ask
Chronoa") did not come along, so neither did the steps that pressed them.

Each route is driven the way a person drives it - press the route, press Pair,
answer the code - against the harness fakes: `tests/fake_bluez.py` on a private
D-Bus, `tests/fake_adb.py` first on PATH, `tests/fake_kdeconnect.py` on
loopback. No real adapter, phone or network is touched, and the state directory
is a temporary one so the real KDE Connect certificate is never read.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

_HARNESS = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["LINK_ROOT"])
sys.path.insert(0, os.environ["LINK_TESTS"])
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
Adw.init()

from shani_link.app.connect_device import ConnectDeviceWindow


class App:
    """What the app hands the window: `show_page` for its "Open ..." buttons."""
    def __init__(self):
        self.shown = []
    def show_page(self, name):
        self.shown.append(name)


def pump(until=lambda: False, seconds=8.0):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        time.sleep(0.02)
    return until()


def walk(widget, out=None):
    out = [] if out is None else out
    for getter in ("get_label", "get_title", "get_subtitle", "get_text"):
        fn = getattr(widget, getter, None)
        if fn is not None:
            try:
                value = fn()
            except TypeError:
                value = None
            if isinstance(value, str) and value:
                out.append(value)
    child = widget.get_first_child()
    while child is not None:
        walk(child, out)
        child = child.get_next_sibling()
    return out


def find(widget, kind, label):
    if isinstance(widget, kind):
        for getter in ("get_label", "get_title"):
            fn = getattr(widget, getter, None)
            if fn is not None and fn() == label:
                return widget
    child = widget.get_first_child()
    while child is not None:
        hit = find(child, kind, label)
        if hit is not None:
            return hit
        child = child.get_next_sibling()
    return None


def page(win):
    return win.view.get_visible_page()


def tag(win):
    return page(win).get_tag()


def texts(win):
    return walk(page(win))


def press(win, label):
    button = None
    if not pump(lambda: find(page(win), Gtk.Button, label) is not None, 8):
        raise SystemExit(f"no button {label!r} on {tag(win)}: {texts(win)}")
    find(page(win), Gtk.Button, label).emit("clicked")
    pump(seconds=0.2)


def activate_row(win, title):
    if not pump(lambda: find(page(win), Adw.ActionRow, title) is not None, 8):
        raise SystemExit(f"no row {title!r} on {tag(win)}: {texts(win)}")
    find(page(win), Adw.ActionRow, title).emit("activated")
    pump(seconds=0.2)


def row_button(win, title, label):
    if not pump(lambda: find(page(win), Adw.ActionRow, title) is not None, 8):
        raise SystemExit(f"no row {title!r} on {tag(win)}: {texts(win)}")
    find(find(page(win), Adw.ActionRow, title), Gtk.Button, label).emit("clicked")
    pump(seconds=0.2)


def say(**kw):
    print("RESULT " + json.dumps(kw), flush=True)


case = sys.argv[1]
record = {}

if case == "start":
    win = ConnectDeviceWindow(App()); win.present(); pump(seconds=0.3)
    say(tag=tag(win), texts=texts(win))

elif case.startswith("bt"):
    import fake_bluez
    bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE); bus.up()
    fake = fake_bluez.FakeBluez(bus.get_bus_address(), powered=(case != "bt-off")).start()
    app = App()
    win = ConnectDeviceWindow(app, bt_address=bus.get_bus_address()); win.present()
    activate_row(win, "Bluetooth")
    record["first"] = tag(win)
    if case == "bt-off":
        record["off_texts"] = texts(win)
        row_button(win, "Bluetooth is off", "Turn on")
        pump(lambda: find(page(win), Adw.ActionRow, "Pixel 9") is not None, 8)
        record["after_on"] = texts(win)
    elif case == "bt-phone":
        row_button(win, "Pixel 9", "Pair")
        pump(lambda: tag(win) == "bt-code", 8)
        record["code_page"] = texts(win)
        press(win, "Yes, pair")
        pump(lambda: tag(win) == "done", 8)
        record["done"] = texts(win)
        press(win, "Open Phone")
    elif case == "bt-decline":
        row_button(win, "Pixel 9", "Pair")
        pump(lambda: tag(win) == "bt-code", 8)
        press(win, "No, cancel")
        pump(lambda: tag(win) == "done", 8)
        record["done"] = texts(win)
    elif case == "bt-band":
        row_button(win, "Band 8", "Connect")
        pump(lambda: tag(win) == "done", 8)
        record["done"] = texts(win)
    elif case == "bt-pin":
        row_button(win, "Car kit", "Pair")
        pump(lambda: tag(win) == "bt-code", 8)
        record["code_page"] = texts(win)
        entry = find(page(win), Adw.EntryRow, "PIN")
        entry.set_text("0000")
        press(win, "Pair")
        pump(lambda: tag(win) == "done", 8)
        record["done"] = texts(win)
    win.close(); pump(seconds=0.3)
    record["agent_after_close"] = fake.agent
    record["shown"] = app.shown
    say(**record)

elif case.startswith("adb"):
    app = App()
    win = ConnectDeviceWindow(app); win.present()
    activate_row(win, "Android phone")
    record["how"] = texts(win)
    state = os.environ["FAKE_ADB_STATE"]
    def phone(**values):
        data = json.load(open(state)); data.update(values); json.dump(data, open(state, "w"))
    if case == "adb-usb":
        activate_row(win, "USB cable")
        phone(devices=[["R5CT10", "unauthorized", "usb:1-1"]])
        pump(lambda: any("Allow" in t for t in texts(win)), 8)
        record["waiting"] = texts(win)
        phone(devices=[["R5CT10", "device", "usb:1-1 model:Pixel_8"]])
        pump(lambda: tag(win) == "done", 8)
        record["done"] = texts(win)
        record["switches"] = [w for w in walk(page(win)) if w.startswith("Let ")]
    elif case == "adb-wifi":
        phone(mdns=[["adb-R5CT10-abc", "pairing", "192.168.1.20:37099"],
                    ["adb-R5CT10-abc", "connect", "192.168.1.20:41000"]])
        activate_row(win, "Wi-Fi")
        pump(lambda: find(page(win), Adw.EntryRow, "IP address & port").get_text() != "", 8)
        record["prefilled"] = find(page(win), Adw.EntryRow, "IP address & port").get_text()
        record["has_qr"] = find(page(win), Gtk.Picture, None) is not None or any(
            "QR" in t for t in texts(win))
        find(page(win), Adw.EntryRow, "Wi-Fi pairing code").set_text("000000")
        press(win, "Pair")
        pump(lambda: any("did not match" in t for t in texts(win)), 8)
        record["wrong"] = texts(win)
        find(page(win), Adw.EntryRow, "Wi-Fi pairing code").set_text("123456")
        press(win, "Pair")
        pump(lambda: tag(win) == "done", 10)
        record["done"] = texts(win)
    win.close(); pump(seconds=0.3)
    say(**record)

elif case.startswith("lan"):
    import fake_kdeconnect
    from shani_link import lan_link
    answer = {"lan-accept": "accept", "lan-reject": "reject"}[case]
    phone = fake_kdeconnect.FakePhone(answer=answer, delay=1.5).start()
    entry = {"deviceId": fake_kdeconnect.PHONE_ID, "deviceName": "Fake Pixel",
             "deviceType": "phone", "address": "127.0.0.1", "tcpPort": phone.port}
    lan_link.listen_once = lambda timeout=3.0: (time.sleep(0.2), [entry])[1]
    lan_link.announce = lambda name, tcp_port=1716: (True, "")
    app = App()
    win = ConnectDeviceWindow(app); win.present()
    activate_row(win, "Phone with KDE Connect")
    row_button(win, "Fake Pixel", "Pair")
    record["waiting"] = texts(win)
    pump(lambda: tag(win) == "done", 10)
    record["done"] = texts(win)
    record["stored"] = lan_link.paired_device()
    if case == "lan-accept":
        press(win, "Open Phone link")
        record["shown"] = app.shown
    win.close(); pump(seconds=0.3)
    say(**record)
'''


from _gui import needs_display  # noqa: E402

pytestmark = needs_display


@pytest.fixture
def broadway():
    return os.environ.get("BROADWAY_DISPLAY", "")


def _run(broadway, case, tmp_path):
    script = tmp_path / f"harness-{case}.py"
    script.write_text(_HARNESS)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if not (bindir / "adb").exists():
        (bindir / "adb").symlink_to(ROOT / "tests" / "fake_adb.py")
    state = tmp_path / "adb.json"
    state.write_text(json.dumps({"devices": [], "mdns": [], "code": "123456",
                                 "connect": "192.168.1.20:41000"}))
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               XDG_STATE_HOME=str(tmp_path / "state"), XDG_DATA_HOME=str(tmp_path / "data"),
               LINK_ROOT=str(ROOT), LINK_TESTS=str(ROOT / "tests"),
               FAKE_ADB_STATE=str(state),
               PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run([sys.executable, str(script), case],
                          capture_output=True, text=True, timeout=120, env=env)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    raise AssertionError(f"{case}: no result\n{done.stdout[-2000:]}\n{done.stderr[-4000:]}")


def _has(texts, needle):
    return any(needle in t for t in texts)


def test_the_start_page_asks_what_is_being_connected(broadway, tmp_path):
    out = _run(broadway, "start", tmp_path)
    assert out["tag"] == "start"
    for route in ("Bluetooth", "Android phone", "Phone with KDE Connect"):
        assert route in out["texts"], out["texts"]


def test_bluetooth_phone_compares_a_code_then_offers_the_phone_panel(broadway, tmp_path):
    out = _run(broadway, "bt-phone", tmp_path)
    assert out["first"] == "bluetooth"
    assert "123456" in out["code_page"] and _has(out["code_page"], "Does Pixel 9 show this code?")
    assert "Pixel 9 is paired" in out["done"], out["done"]
    assert out["shown"] == ["phone"], out["shown"]
    assert out["agent_after_close"] is None, "closing the window left our agent registered"


def test_declining_the_code_is_not_paired(broadway, tmp_path):
    out = _run(broadway, "bt-decline", tmp_path)
    assert _has(out["done"], "did not pair") and _has(out["done"], "declined"), out["done"]


def test_a_band_is_connected_over_gatt_not_paired(broadway, tmp_path):
    out = _run(broadway, "bt-band", tmp_path)
    assert "Band 8 is connected" in out["done"], out["done"]
    assert "Open Watch" in out["done"]


def test_a_car_kit_asks_for_a_pin(broadway, tmp_path):
    out = _run(broadway, "bt-pin", tmp_path)
    assert _has(out["code_page"], "Enter the PIN for Car kit")
    assert "Car kit is paired" in out["done"], out["done"]


def test_bluetooth_off_is_turned_on_from_the_page(broadway, tmp_path):
    out = _run(broadway, "bt-off", tmp_path)
    assert "Bluetooth is off" in out["off_texts"]
    assert "Pixel 9" in out["after_on"], out["after_on"]


def test_android_by_usb_walks_the_allow_step(broadway, tmp_path):
    out = _run(broadway, "adb-usb", tmp_path)
    assert _has(out["waiting"], "Tap Allow on the phone"), out["waiting"]
    assert "Pixel 8 is connected" in out["done"], out["done"]
    assert out["switches"] == [], "an assistant's switch came along: " + str(out["switches"])


def test_android_by_wifi_code_finds_the_address_and_pairs(broadway, tmp_path):
    out = _run(broadway, "adb-wifi", tmp_path)
    assert out["prefilled"] == "192.168.1.20:37099"
    assert _has(out["wrong"], "did not match"), out["wrong"]
    assert _has(out["done"], "is connected"), out["done"]


def test_kde_connect_pairs_stores_and_offers_the_bridge(broadway, tmp_path):
    out = _run(broadway, "lan-accept", tmp_path)
    assert _has(out["waiting"], "Tap Accept on Fake Pixel"), out["waiting"]
    assert "Fake Pixel is paired" in out["done"], out["done"]
    assert out["stored"]["deviceName"] == "Fake Pixel"
    assert out["shown"] == ["phone_link"], out
    assert not any("Chronoa" in t for t in out["done"]), out["done"]


def test_kde_connect_declined_stores_nothing(broadway, tmp_path):
    out = _run(broadway, "lan-reject", tmp_path)
    assert "Fake Pixel declined" in out["done"], out["done"]
    assert out["stored"] == {}
