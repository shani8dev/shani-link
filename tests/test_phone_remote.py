"""The phone-remote client (shani_link/phone_remote.py): every answer of the
peripheral's socket, with `ble_peripheral.ask` stood in for (no Bluetooth).

Moved from Shani Chronoa's `phone_remote` skill, whose consent test stayed there.
"""
import pytest

pytest.importorskip("gi")

from shani_link import ble_peripheral as bp  # noqa: E402
from shani_link import phone_remote as pr  # noqa: E402


class Peripheral:
    """What `ble_peripheral.serve` answers, and what it was asked."""

    def __init__(self, subscribed=True, error="", missing=""):
        self.subscribed, self.error, self.missing, self.asked = subscribed, error, missing, []

    def __call__(self, action, **fields):
        self.asked.append((action, fields))
        if action == "status":
            return {"subscribed": self.subscribed, "error": self.error}
        if action == "type":
            return {"ok": True, "missing": self.missing}
        return {"ok": True}


@pytest.fixture
def peripheral(monkeypatch):
    p = Peripheral()
    monkeypatch.setattr(bp, "ask", p)
    return p


def test_press_the_shutter(peripheral):
    out = pr.press("shutter")
    assert out == "Pressed shutter on the phone (in the phone's camera app this takes the photo)."
    assert ("press", {"key": "shutter"}) in peripheral.asked


def test_a_key_by_its_spoken_name(peripheral):
    assert pr.press("Volume Up") == "Pressed volume up on the phone."
    assert peripheral.asked[-1] == ("press", {"key": "volume_up"})


def test_an_unknown_key_is_refused_before_anything_is_asked(peripheral):
    assert "key must be one of" in pr.press("power")
    assert peripheral.asked == []


def test_no_phone_connected_presses_nothing(peripheral):
    peripheral.subscribed = False
    assert pr.press("play_pause") == "No phone is connected to Chronoa Remote, so nothing was pressed."
    assert [a for a, _f in peripheral.asked] == ["status"]
    assert "Pair 'Chronoa Remote'" in pr._run({"action": "status"})


def test_the_remote_not_running_says_where_it_is_turned_on(monkeypatch):
    def refused(*_a, **_k):
        raise ConnectionRefusedError
    monkeypatch.setattr(bp, "ask", refused)
    out = pr.press("shutter")
    assert out == pr.NOT_RUNNING and "Phone as a remote" in out and "Background" in out


def test_a_peripheral_that_could_not_start_says_why(peripheral):
    peripheral.error = "no adapter"
    assert pr._run({"action": "status"}) == "The phone remote could not start: no adapter."
    assert pr.press("next") == "The phone remote could not start: no adapter."


def test_typing_reports_what_a_us_keyboard_could_not(peripheral):
    peripheral.missing = "é"
    out = pr._run({"action": "type", "text": "café"})
    assert out.startswith("Typed 3 characters") and "'é'" in out
    assert "Nothing was typed" in pr._run({"action": "type", "text": ""})


def test_typing_with_no_phone_says_typed_not_pressed(peripheral):
    peripheral.subscribed = False
    assert pr.type_text("hi") == "No phone is connected to Chronoa Remote, so nothing was typed."


def test_the_tool_arguments(peripheral):
    assert pr._run({"action": "press", "key": "mute"}) == "Pressed mute on the phone."
    assert "status, press or type" in pr._run({"action": "swipe"})
    assert "needs an action" in pr._run(None)
    assert pr._run({}) == "The phone is connected to Chronoa Remote and listening for keys."
