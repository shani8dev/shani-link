"""The phone-link actions (shani_link/lan_phone.py), with `lan_link`'s network
calls stood in for. Moved from Shani Chronoa's `lan_phone` skill tests; the
consent test stayed there.
"""
import pytest

from shani_link import lan_link
from shani_link import lan_phone as lp


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state"


def test_a_wrong_action_is_refused_not_ignored():
    out = lp._run({"action": "share"})
    assert "must be one of" in out and "share" in out


def test_status_names_the_protocol_and_the_stored_pairing():
    out = lp._run({"action": "status"})
    assert str(lan_link.UDP_PORT) in out
    state, _why = lan_link.pairing_status()
    assert f"Pairing: {state}" in out


def test_one_device_found_is_remembered_for_send_and_pair(monkeypatch):
    monkeypatch.setattr(lan_link, "listen_once", lambda seconds: [
        {"deviceName": "Pixel 9", "deviceType": "phone", "address": "192.168.1.44",
         "protocolVersion": 8, "tcpPort": 1716}])
    out = lp._run({"action": "devices", "seconds": 2})
    assert "1 device(s)" in out and "Pixel 9 [phone] at 192.168.1.44" in out
    assert lp.default_host() == "192.168.1.44"
    sent = []
    monkeypatch.setattr(lan_link, "send_file", lambda host, path: sent.append((host, path)) or (True, "f.txt"))
    assert lp._run({"action": "send", "path": "/tmp/f.txt"}) == "f.txt was offered to the device"
    assert sent == [("192.168.1.44", "/tmp/f.txt")]


def test_nothing_found_is_a_real_answer(monkeypatch):
    heard = []
    monkeypatch.setattr(lan_link, "listen_once", lambda seconds: heard.append(seconds) or [])
    assert "No device announced itself" in lp._run({"action": "devices", "seconds": 99})
    assert heard == [30.0], "the listen is held to 30 seconds"


def test_send_with_no_device_yet_says_what_to_do(monkeypatch):
    monkeypatch.setattr(lan_link, "send_file", lambda *a: pytest.fail("sent to nobody"))
    assert lp.send("/tmp/f.txt").startswith("No device yet")
    assert lp.send("").startswith("Which file?")


def test_pair_says_each_outcome(monkeypatch):
    lp.remember("192.168.1.44")
    for state, why, want in (("paired", "Pixel 9", "Paired: Pixel 9."),
                             ("waiting", "no answer", "nobody answered on the phone in time"),
                             ("refused", "the phone said no", "Not paired (refused): the phone said no.")):
        monkeypatch.setattr(lan_link, "pair", lambda host, timeout, s=state, w=why: (s, w))
        assert want in lp._run({"action": "pair"})


def test_announce(monkeypatch):
    monkeypatch.setattr(lan_link, "announce", lambda name: (True, ""))
    monkeypatch.setattr(lan_link, "_hostname", lambda: "desk")
    assert lp.announce() == ("Announced this computer as 'desk' on the local network. A phone "
                             "running KDE Connect on the same Wi-Fi will now offer to pair.")
    monkeypatch.setattr(lan_link, "announce", lambda name: (True, "announced by broadcast; the mDNS service was not published"))
    assert "(announced by broadcast" in lp.announce()
    monkeypatch.setattr(lan_link, "announce", lambda name: (False, "no route"))
    assert lp._run({"action": "announce"}).startswith("Could not announce this computer (no route)")
