"""Bluetooth tethering through NetworkManager, against `tests/fake_nmcli.py` first on PATH."""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shani_link import tether  # noqa: E402

MAC = "AA:00:00:00:00:01"


@pytest.fixture
def nm(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "nmcli").symlink_to(ROOT / "tests" / "fake_nmcli.py")
    state = tmp_path / "nmcli.json"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NMCLI_STATE", str(state))

    class NM:
        def set(self, **values):
            data = self.read()
            data.update(values)
            state.write_text(json.dumps(data))

        def read(self):
            return json.loads(state.read_text()) if state.exists() else {}

    n = NM()
    n.set(connections=[{"name": "Home Wi-Fi", "uuid": "w-1", "type": "802-11-wireless", "active": True}])
    return n


def test_first_use_adds_a_panu_connection_for_the_phone_then_brings_it_up(nm):
    assert tether.state(MAC, "Pixel 8")["on"] is False
    assert tether.tether(MAC, True, "Pixel 8") == "Connected through Pixel 8"
    calls = nm.read()["calls"]
    add = next(c for c in calls if c[:2] == ["connection", "add"])
    assert add == ["connection", "add", "type", "bluetooth", "bt-type", "panu",
                   "bluetooth.bdaddr", MAC, "con-name", "Pixel 8 internet"]
    assert any(c[:2] == ["connection", "up"] for c in calls)
    st = tether.state(MAC, "Pixel 8")
    assert st["on"] is True and st["connection"] == "Pixel 8 internet"


def test_an_existing_connection_is_reused_not_added_again(nm):
    nm.set(connections=[{"name": "My phone net", "uuid": "bt-1", "type": "bluetooth",
                         "bdaddr": MAC, "bttype": "panu", "active": False}])
    assert tether.tether(MAC.lower(), True, "Pixel 8") == "Connected through Pixel 8"
    calls = nm.read()["calls"]
    assert not any(c[:2] == ["connection", "add"] for c in calls)
    assert ["connection", "up", "uuid", "bt-1"] in calls


def test_off_brings_it_down(nm):
    tether.tether(MAC, True, "Pixel 8")
    assert tether.tether(MAC, False, "Pixel 8") == "Stopped using Pixel 8's internet"
    assert any(c[:2] == ["connection", "down"] for c in nm.read()["calls"])
    assert tether.state(MAC)["on"] is False
    # off when already off is not an error
    assert "Not using" in tether.tether(MAC, False, "Pixel 8")


def test_a_phone_that_is_not_sharing_says_where_the_switch_is(nm):
    nm.set(phone_shares=False)
    with pytest.raises(tether.TetherError) as err:
        tether.tether(MAC, True, "Pixel 8")
    assert "Turn on Bluetooth tethering on the phone first" in str(err.value)
    assert "Settings › Network › Hotspot & tethering" in str(err.value)
    assert tether.state(MAC)["on"] is False


def test_no_networkmanager_is_said_plainly(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert tether.state(MAC)["on"] is False
    with pytest.raises(tether.TetherError, match="nmcli"):
        tether.tether(MAC, True)
