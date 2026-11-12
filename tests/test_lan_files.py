"""Files over the KDE Connect link, both ways, against tests/fake_kdeconnect.py
on real sockets: the computer offers a file and the phone pulls it over TLS
from a payload port; the phone pushes one and the computer takes it - only
from the paired phone, and only under a safe name.

What this proves and what it does not: the fake phone plays the roles as
kdeconnect-kde's upload and download jobs do (upload = TLS server, download =
TLS client, payload ports 1739-1764). A real phone is the remaining proof."""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl for the TLS identities")


@pytest.fixture
def link(tmp_path, monkeypatch):
    for var, sub in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    from shani_link import lan_link
    from fake_kdeconnect import FakePhone, PHONE_ID
    phone = FakePhone().start()
    lan_link._remember_pairing({"deviceId": PHONE_ID, "deviceName": "Fake Pixel"}, "127.0.0.1", phone.port)
    yield lan_link, phone, PHONE_ID
    phone.stop()


def test_a_file_sent_arrives_whole_over_tls_from_a_payload_port(tmp_path, link):
    lan_link, phone, _ = link
    f = tmp_path / "holiday.jpg"
    f.write_bytes(os.urandom(250_000))
    ok, why = lan_link.send_file("127.0.0.1", f, tcp_port=phone.port, timeout=20)
    assert ok, why
    deadline = time.time() + 5
    while "holiday.jpg" not in phone.files and time.time() < deadline:
        time.sleep(0.05)
    assert phone.files["holiday.jpg"] == f.read_bytes()
    offer = phone.got("kdeconnect.share.request")[0]
    assert lan_link.MIN_PAYLOAD_PORT <= offer["payloadTransferInfo"]["port"] <= lan_link.MAX_PAYLOAD_PORT


def test_the_paired_phone_sends_us_a_file(tmp_path, link):
    lan_link, phone, phone_id = link
    got = []
    done = threading.Event()
    rx = lan_link.Receiver(tmp_path / "Downloads", lambda path, why: (got.append((path, why)), done.set()), port=0, paired_id=phone_id)
    # port 0 for the test: bind, then read the port back
    rx.port = _free_port()
    assert rx.start()[0]
    try:
        phone.push(rx.port, "../../.bashrc", b"from the phone")
        assert done.wait(10), "nothing arrived"
        path, why = got[0]
        assert why == "" and Path(path).name == "bashrc" and Path(path).parent == tmp_path / "Downloads"
        assert Path(path).read_bytes() == b"from the phone"
    finally:
        rx.stop()


def test_a_device_that_is_not_the_paired_phone_is_refused(tmp_path, link):
    lan_link, phone, phone_id = link
    got = []
    rx = lan_link.Receiver(tmp_path / "Downloads", lambda path, why: got.append(path), port=_free_port(), paired_id=phone_id)
    assert rx.start()[0]
    try:
        phone.push(rx.port, "evil.sh", b"rm -rf ~", device_id="a" * 32)
        time.sleep(1.0)
        assert got == [] and not (tmp_path / "Downloads").exists()
    finally:
        rx.stop()


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_the_switch_opens_the_firewall_for_the_session_and_closes_it(tmp_path, link, monkeypatch):
    """lan_receive follows `phone-lan-receive-enabled`: on, it listens and asks
    firewalld for the kdeconnect service without --permanent; off, it undoes both."""
    lan_link, phone, phone_id = link
    log = tmp_path / "firewall-log"
    fake = tmp_path / "firewall-cmd"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\n[ "$1" = --state ] && exit 0\nexit 0\n')
    fake.chmod(0o755)
    from shani_link import lan_receive
    monkeypatch.setattr(lan_receive, "FIREWALL", str(fake))
    monkeypatch.setattr(lan_receive, "SWITCH_POLL_SECONDS", 0.2)
    monkeypatch.setattr(lan_link, "announce", lambda *a, **k: (True, ""))

    class Settings:
        on = True
        def get_bool(self, key, default=False):
            return self.on if key == lan_receive.KEY else default

    cfg = Settings()
    rx = lan_receive.LanReceiver(cfg, folder=str(tmp_path / "Downloads"), port=_free_port())
    rx.start()
    try:
        deadline = time.time() + 5
        while (rx._rx is None) and time.time() < deadline:
            time.sleep(0.05)
        assert rx._rx is not None
        phone.push(rx.port, "photo.png", b"png bytes")
        deadline = time.time() + 5
        while not (tmp_path / "Downloads" / "photo.png").exists() and time.time() < deadline:
            time.sleep(0.05)
        assert (tmp_path / "Downloads" / "photo.png").read_bytes() == b"png bytes"
        cfg.on = False
        deadline = time.time() + 5
        while rx._rx is not None and time.time() < deadline:
            time.sleep(0.05)
        lines = log.read_text().split("\n")
        assert "--add-service=kdeconnect" in lines and "--remove-service=kdeconnect" in lines
        assert not any("--permanent" in l for l in lines)
    finally:
        rx.stop()
