"""The Phone page's "Use the phone's internet" switch, pressed on the private
Broadway display against `tests/fake_nmcli.py` first on PATH."""
import json
import os
import pathlib

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from _gui import needs_display, pump, texts  # noqa: E402
from test_app_phone_page import _build  # noqa: E402

pytestmark = needs_display

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAC = "AA:00:00:00:00:01"


@pytest.fixture
def nm(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "nmcli").symlink_to(ROOT / "tests" / "fake_nmcli.py")
    state = tmp_path / "nmcli.json"
    state.write_text(json.dumps({"connections": []}))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NMCLI_STATE", str(state))

    def set_(**values):
        data = json.loads(state.read_text())
        data.update(values)
        state.write_text(json.dumps(data))
    set_.read = lambda: json.loads(state.read_text())
    return set_


def _page(monkeypatch, tmp_path):
    from shani_link import phone as ph
    return _build(monkeypatch, tmp_path, [ph.Device(MAC, "Pixel 8", True, True)])


def test_the_switch_uses_the_phones_internet_and_stops(nm, monkeypatch, tmp_path):
    p = _page(monkeypatch, tmp_path)
    assert "Use the phone's internet" in texts(p.status_box)
    assert not p.internet_switch.get_active()
    p.internet_switch.set_active(True)
    assert pump(lambda: p.internet_row.get_subtitle() == "Connected through Pixel 8", 8), \
        p.internet_row.get_subtitle()
    calls = nm.read()["calls"]
    assert ["connection", "add", "type", "bluetooth", "bt-type", "panu", "bluetooth.bdaddr",
            MAC, "con-name", "Pixel 8 internet"] in calls, calls
    assert any(c[:2] == ["connection", "up"] for c in calls)
    p.internet_switch.set_active(False)
    assert pump(lambda: any(c[:2] == ["connection", "down"] for c in nm.read()["calls"])
                and p.internet_switch.get_sensitive(), 8)
    assert not p.internet_switch.get_active()


def test_already_on_is_shown_on(nm, monkeypatch, tmp_path):
    nm(connections=[{"name": "Pixel 8 internet", "uuid": "bt-1", "type": "bluetooth",
                     "bdaddr": MAC, "bttype": "panu", "active": True}])
    p = _page(monkeypatch, tmp_path)
    assert p.internet_switch.get_active()
    assert p.internet_row.get_subtitle() == "Connected through Pixel 8"


def test_a_phone_not_sharing_says_what_to_turn_on(nm, monkeypatch, tmp_path):
    nm(phone_shares=False)
    p = _page(monkeypatch, tmp_path)
    p.internet_switch.set_active(True)
    assert pump(lambda: "tethering on the phone first" in (p.internet_row.get_subtitle() or ""), 8), \
        p.internet_row.get_subtitle()
    assert "Hotspot &amp; tethering" in p.internet_row.get_subtitle()   # escaped markup
    assert not p.internet_switch.get_active(), "the switch went back off"
    assert p.internet_switch.get_sensitive()


def test_no_row_without_networkmanager(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    p = _page(monkeypatch, tmp_path)
    assert "Use the phone's internet" not in texts(p.status_box)
