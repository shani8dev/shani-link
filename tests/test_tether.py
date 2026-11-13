"""Tethering over a cable and Wi-Fi, against a fake nmcli.

Bluetooth has its own tests in `tests/test_app_tether.py` and the service on a
private bus; these are the two roads that need no pairing and no address, so
they are pure NetworkManager calls: what device is there, and what to do with it.
"""
import json

import pytest

from shani_link import tether


def _nmcli(answers):
    """A fake `_nmcli`: the first key the call starts with wins."""
    calls = []

    def run(*args):
        calls.append(" ".join(args))
        joined = " ".join(args)
        for prefix, out in answers.items():
            if prefix in joined:
                return 0, out
        return 1, f"unexpected: {joined}"

    run.calls = calls
    return run


DEVICES = ("lo:loopback:unmanaged\n"
           "enp0s31f6:ethernet:unavailable\n"
           "usb0:ethernet:connected\n"
           "wlan0:wifi:connected\n")
STATES = ("lo:unmanaged\n"
          "enp0s31f6:unavailable\n"
          "usb0:connected\n"
          "wlan0:connected\n")


@pytest.fixture
def nmcli(monkeypatch):
    monkeypatch.setattr(tether, "available", lambda: True)
    holder = {}

    def install(answers):
        fake = _nmcli(answers)
        holder["fake"] = fake
        monkeypatch.setattr(tether, "_nmcli", fake)
        return fake

    return install


def test_the_cable_phone_is_known_by_its_device_name(nmcli):
    nmcli({"-f DEVICE,TYPE,STATE device status": DEVICES})
    assert tether.usb_device() == "usb0"


def test_a_computer_with_no_cable_has_no_usb_phone(nmcli):
    nmcli({"-f DEVICE,TYPE,STATE device status":
           "lo:loopback:unmanaged\nenp0s31f6:ethernet:unavailable\n"})
    assert tether.usb_device() is None


def test_usb_tethering_brings_the_cable_up(nmcli):
    fake = nmcli({"-f DEVICE,TYPE,STATE device status": DEVICES,
                  "device connect": "", "device disconnect": ""})
    said = tether.usb(True)
    assert "usb0" in said and "available" in said, said
    assert "device connect usb0" in fake.calls, fake.calls


def test_usb_tethering_can_be_stopped_again(nmcli):
    fake = nmcli({"-f DEVICE,TYPE,STATE device status": DEVICES,
                  "device disconnect": ""})
    said = tether.usb(False)
    assert "Disconnected usb0" in said, said
    assert "device disconnect usb0" in fake.calls, fake.calls


def test_no_cable_names_the_switch_on_the_phone(nmcli):
    nmcli({"-f DEVICE,TYPE,STATE device status": "lo:loopback:unmanaged\n"})
    with pytest.raises(tether.TetherError) as caught:
        tether.usb(True)
    assert "USB tethering" in str(caught.value), caught.value


def test_the_state_says_whether_the_cable_is_up(nmcli):
    nmcli({"-f DEVICE,TYPE,STATE device status": DEVICES,
           "-f DEVICE,STATE device status": STATES})
    assert tether.usb_state() == {"device": "usb0", "on": True}
    nmcli({"-f DEVICE,TYPE,STATE device status": DEVICES,
           "-f DEVICE,STATE device status": "usb0:disconnected\n"})
    assert tether.usb_state() == {"device": "usb0", "on": False}


def test_the_wifi_answer_names_the_network(nmcli):
    nmcli({"dev wifi": "no::\nyes:ShaniNet\n",
           "connection show --active": "Wired connection 1:abc:802-3-ethernet\n"})
    assert "ShaniNet" in tether.wifi()
    nmcli({"dev wifi": "no::\n", "connection show --active": ""})
    assert "not on Wi-Fi" in tether.wifi()


def test_the_computers_own_hotspot_is_not_called_wifi(nmcli):
    nmcli({"dev wifi": "no::\nyes:shani-hotspot\n",
           "connection show --active": "Hotspot:uuid-1:802-11-wireless\n",
           "802-11-wireless.mode": "ap\n"})
    said = tether.wifi()
    assert "hotspot is on: Hotspot" in said, said
    assert "On Wi-Fi" not in said, said


def test_a_joined_network_is_not_an_access_point(nmcli):
    nmcli({"dev wifi": "no::\nyes:ShaniNet\n",
           "connection show --active": "ShaniNet:uuid-2:802-11-wireless\n",
           "802-11-wireless.mode": "infrastructure\n"})
    assert "On Wi-Fi: ShaniNet" in tether.wifi()