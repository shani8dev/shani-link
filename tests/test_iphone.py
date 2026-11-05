"""iPhones, honestly: where one is seen, the app says what works (files over
USB, Bluetooth audio) and that calls and texts through it aren't possible on
Linux - and offers no button that cannot work for it.

The connect flow runs against `tests/fake_bluez.py` on a private bus, with an
iPhone known only by its Apple vendor id (Modalias), in its own process on the
private Broadway display; the Devices page is built in-process with the phone
link stubbed at `devices.ph` and a fake sysfs for USB.
"""
import html
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shani_link import apple  # noqa: E402

from _gui import needs_display  # noqa: E402


# --- recognising one --------------------------------------------------------

def test_an_apple_device_is_told_by_vendor_or_name():
    assert apple.is_apple(modalias="bluetooth:v004Cp7102d0F40")
    assert apple.is_apple(modalias="usb:v05ACp12A8d1504")
    assert apple.is_apple(manufacturer_ids=[0x004C])
    assert apple.is_apple(name="Asha's iPhone")
    assert apple.is_apple(name="iPad Air")
    assert not apple.is_apple(name="Pixel 9", modalias="bluetooth:v00E0p1200d1436",
                              manufacturer_ids=[0x00E0])
    assert not apple.is_apple(name="iPhoneless speaker")


def _usb(tmp_path, *devices):
    root = tmp_path / "usb"
    for i, (vendor, product) in enumerate(devices):
        d = root / f"1-{i}"
        d.mkdir(parents=True)
        (d / "idVendor").write_text(vendor + "\n")
        (d / "product").write_text(product + "\n")
    (root / "usb1").mkdir(parents=True)          # a hub with no product file
    return str(root)


def test_an_iphone_on_usb_is_found_in_sysfs(tmp_path):
    root = _usb(tmp_path, ("05ac", "iPhone"), ("05ac", "Magic Keyboard"), ("18d1", "Pixel 8"))
    assert apple.usb_iphones(root) == ["iPhone"]
    assert apple.usb_iphones(str(tmp_path / "missing")) == []


def test_what_is_said_names_what_works_and_what_does_not():
    words = apple.summary()
    assert "USB cable" in words and "Files" in words and "Bluetooth audio" in words
    assert "Calls and texts through an iPhone aren't possible on Linux" in words


# --- the Devices page -------------------------------------------------------

class _Phone:
    PhoneUnavailable = __import__("shani_link.phone", fromlist=["x"]).PhoneUnavailable

    def __init__(self, found):
        self.found = found

    def backend(self):
        return "bluetooth"

    def devices(self):
        return self.found

    def battery(self, _d):
        return None


def _buttons(widget):
    from gi.repository import Gtk
    out = [widget] if isinstance(widget, Gtk.Button) else []
    child = widget.get_first_child()
    while child is not None:
        out += _buttons(child)
        child = child.get_next_sibling()
    return out


def _labels(widget):
    from gi.repository import Gtk
    out = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        out += _labels(child)
        child = child.get_next_sibling()
    return out


def _marked(widget, css):
    out = [widget] if css in widget.get_css_classes() else []
    child = widget.get_first_child()
    while child is not None:
        out += _marked(child, css)
        child = child.get_next_sibling()
    return out


@needs_display
def test_devices_says_what_works_for_a_paired_iphone_with_no_button(monkeypatch, tmp_path):
    from shani_link import phone as ph
    from shani_link.app import devices
    monkeypatch.setattr(devices, "ph", _Phone([ph.Device("AA:00", "Asha's iPhone", True, True),
                                               ph.Device("AA:01", "Pixel 9", True, True)]))
    monkeypatch.setattr(devices, "USB_SYSFS", _usb(tmp_path))
    page = devices.build(types.SimpleNamespace())
    (group_row,) = _marked(page, devices.APPLE_CSS)
    said = "\n".join(_labels(group_row))
    assert "Asha's iPhone" in said and "aren't possible on Linux" in said, said
    assert "Bluetooth audio" in said and "Files" in said
    assert _buttons(group_row) == [], "no button for an iPhone: nothing it could do works"
    assert apple.HEADLINE in "\n".join(_labels(page))


@needs_display
def test_devices_sees_an_iphone_on_usb_even_with_nothing_paired(monkeypatch, tmp_path):
    from shani_link.app import devices
    monkeypatch.setattr(devices, "ph", _Phone([]))
    monkeypatch.setattr(devices, "USB_SYSFS", _usb(tmp_path, ("05ac", "iPhone")))
    page = devices.build(types.SimpleNamespace())
    (row,) = _marked(page, devices.APPLE_CSS)
    said = "\n".join(_labels(row))
    assert "iPhone (on the USB cable)" in said and "aren't possible on Linux" in said, said


@needs_display
def test_devices_says_nothing_about_iphones_without_one(monkeypatch, tmp_path):
    from shani_link import phone as ph
    from shani_link.app import devices
    monkeypatch.setattr(devices, "ph", _Phone([ph.Device("AA:01", "Pixel 9", True, True)]))
    monkeypatch.setattr(devices, "USB_SYSFS", _usb(tmp_path, ("18d1", "Pixel 8")))
    page = devices.build(types.SimpleNamespace())
    assert _marked(page, devices.APPLE_CSS) == []
    assert not any("iPhone" in t for t in _labels(page))


# --- the connect flow -------------------------------------------------------

_HARNESS = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["LINK_ROOT"]); sys.path.insert(0, os.environ["LINK_TESTS"])
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
Adw.init()
import fake_bluez
from shani_link.app.connect_device import ConnectDeviceWindow

def pump(until=lambda: False, seconds=8.0):
    ctx = GLib.MainContext.default(); end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        time.sleep(0.02)
    return until()

def walk(w, out):
    for g in ("get_label", "get_title", "get_subtitle"):
        fn = getattr(w, g, None)
        if fn is not None:
            try:
                v = fn()
            except TypeError:
                v = None
            if isinstance(v, str) and v:
                out.append(v)
    c = w.get_first_child()
    while c is not None:
        walk(c, out); c = c.get_next_sibling()
    return out

def find(w, kind, label):
    if isinstance(w, kind) and any(getattr(w, g, lambda: None)() == label for g in ("get_label", "get_title")):
        return w
    c = w.get_first_child()
    while c is not None:
        hit = find(c, kind, label)
        if hit is not None:
            return hit
        c = c.get_next_sibling()

def buttons(w, out):
    if isinstance(w, Gtk.Button) and w.get_label():
        out.append(w.get_label())
    c = w.get_first_child()
    while c is not None:
        buttons(c, out); c = c.get_next_sibling()
    return out

class App:
    def __init__(self): self.shown = []
    def show_page(self, name): self.shown.append(name)

bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE); bus.up()
iphone = {"Kitchen": ("AA:00:00:00:00:09", "phone", 0, "public", [], "confirm",
                      {"Modalias": GLib.Variant("s", "bluetooth:v004Cp7102d0F40")})}
fake = fake_bluez.FakeBluez(bus.get_bus_address(), devices=iphone).start()
win = ConnectDeviceWindow(App(), bt_address=bus.get_bus_address()); win.present()
page = lambda: win.view.get_visible_page()
find(page(), Adw.ActionRow, "Bluetooth").emit("activated")
pump(lambda: find(page(), Adw.ActionRow, "Kitchen") is not None)
row = find(page(), Adw.ActionRow, "Kitchen")
record = {"row": row.get_subtitle()}
find(row, Gtk.Button, "Pair").emit("clicked")
pump(lambda: page().get_tag() == "bt-code")
find(page(), Gtk.Button, "Yes, pair").emit("clicked")
pump(lambda: page().get_tag() == "done")
record["done"] = walk(page(), [])
record["buttons"] = buttons(page(), [])
win.close(); pump(seconds=0.3)
print("RESULT " + json.dumps(record), flush=True)
'''


@needs_display
def test_pairing_an_iphone_says_what_works_and_offers_no_phone_page(tmp_path):
    script = tmp_path / "harness.py"
    script.write_text(_HARNESS)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               LINK_ROOT=str(ROOT), LINK_TESTS=str(ROOT / "tests"),
               XDG_STATE_HOME=str(tmp_path / "state"), XDG_DATA_HOME=str(tmp_path / "data"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          timeout=120, env=env)
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, done.stdout[-2000:] + done.stderr[-4000:]
    out = json.loads(lines[0][7:])
    # the stored subtitle is Pango markup (escaped); what a person reads is unescaped
    assert "aren't possible on Linux" in html.unescape(out["row"]), out["row"]
    said = "\n".join(out["done"])
    assert "Kitchen is paired" in said, said
    assert "USB cable" in said and "Bluetooth audio" in said and "aren't possible on Linux" in said, said
    assert "Open Phone" not in out["buttons"], out["buttons"]
    assert "Calls and texts go through it" not in said
