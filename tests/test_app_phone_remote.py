"""The Phone page's Remote tab: the phone's keys, pressed from here.

Built on the private Broadway display conftest.py started; the client
(`shani_link.phone_remote.press`) is stood in for, so no Bluetooth is reached.
"""
from __future__ import annotations

import pytest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from _gui import needs_display, pump, texts  # noqa: E402

pytestmark = needs_display


@pytest.fixture(autouse=True)
def _adw():
    Adw.init()


class Switches:
    def __init__(self, on):
        self.on = on

    def get_bool(self, key, default=False):
        return self.on if key == "phone-remote-enabled" else default


class App:
    def __init__(self, on=True):
        self.switches = Switches(on)
        self.shown = []

    def show_page(self, name):
        self.shown.append(name)


def _tab(on=True):
    from shani_link.app.phone import PhonePanel
    panel = PhonePanel(App(on))
    tab = panel.remote_tab()
    return panel, tab


def test_with_the_switch_off_the_keys_are_hidden_and_background_is_named():
    panel, tab = _tab(on=False)
    assert not panel.remote_keys.get_visible() and panel.remote_off.get_visible()
    assert any("Phone as a remote" in t and "Background" in t for t in texts(panel.remote_off))
    open_background = next(b for b in _buttons(panel.remote_off) if b.get_label() == "Open Background")
    open_background.emit("clicked")
    assert panel.app.shown == ["background"]


def test_turning_the_switch_on_shows_the_keys_when_the_tab_is_shown_again():
    panel, tab = _tab(on=False)
    panel.app.switches.on = True
    panel.refresh_remote()
    assert panel.remote_keys.get_visible() and not panel.remote_off.get_visible()


@pytest.mark.parametrize("key", ["shutter", "previous", "play_pause", "next", "volume_down", "volume_up"])
def test_each_key_is_pressed_through_the_client(monkeypatch, key):
    from shani_link import phone_remote
    pressed = []
    monkeypatch.setattr(phone_remote, "press",
                        lambda k: pressed.append(k) or f"Pressed {k.replace('_', ' ')} on the phone.")
    panel, tab = _tab(on=True)
    assert panel.remote_keys.get_visible()
    panel.remote_buttons[key].emit("clicked")
    assert pump(lambda: panel.remote_note.get_text().startswith("Pressed"), 3)
    assert pressed == [key]
    assert panel.remote_note.get_text() == f"Pressed {key.replace('_', ' ')} on the phone."


def test_the_buttons_say_what_they_do():
    panel, tab = _tab(on=True)
    labels = [b.get_tooltip_text() for b in panel.remote_buttons.values()]
    assert labels == ["Take a photo", "Previous", "Play or pause", "Next", "Volume down", "Volume up"]


def test_a_failure_is_shown_in_words(monkeypatch):
    from shani_link import phone_remote
    monkeypatch.setattr(phone_remote, "press",
                        lambda k: "No phone is connected to Chronoa Remote, so nothing was pressed.")
    panel, tab = _tab(on=True)
    panel.remote_buttons["shutter"].emit("clicked")
    assert pump(lambda: "No phone is connected" in panel.remote_note.get_text(), 3)

    def broken(k):
        raise RuntimeError("socket closed")
    monkeypatch.setattr(phone_remote, "press", broken)
    panel.remote_buttons["next"].emit("clicked")
    assert pump(lambda: panel.remote_note.get_text() == "Not pressed: socket closed", 3)


def _buttons(widget):
    from gi.repository import Gtk
    out, stack = [], [widget]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.Button):
            out.append(node)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out
