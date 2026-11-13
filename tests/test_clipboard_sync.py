"""One clipboard with the paired phone (clipboard_sync.py, lan_link.send_clipboard,
Receiver(on_clipboard)): the protocol against tests/fake_kdeconnect.py on real
sockets, and the rules - nothing a password manager marks secret leaves, what
came from the phone is not echoed back, off is off, only text goes."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
needs_tls = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl for the TLS identities")


class _Settings:
    def __init__(self, on=True):
        self.on = on

    def get_bool(self, key, default=False):
        return self.on if key == "phone-clipboard-sync-enabled" else default


@pytest.fixture
def homes(tmp_path, monkeypatch):
    for var, sub in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path


def fake_wl_paste(tmp_path, types, text):
    """A wl-paste that offers `types` and `text`, as the compositor's clipboard would."""
    p = tmp_path / "wl-paste"
    p.write_text("#!/bin/sh\ncase \"$1\" in --list-types) printf '%s\\n' " + " ".join(repr(t) for t in types)
                 + " ;; *) printf '%s' " + repr(text) + " ;; esac\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def make_sync(monkeypatch, homes, types, text, on=True):
    from shani_link import clipboard_sync, lan_link
    monkeypatch.setattr(clipboard_sync, "WL_PASTE", fake_wl_paste(homes, types, text))
    lan_link._remember_pairing({"deviceId": "p" * 32, "deviceName": "Pixel 9"}, "127.0.0.1", 1716)
    sent, copied = [], []
    sync = clipboard_sync.ClipboardSync(_Settings(on), send=lambda t: (sent.append(t), (True, ""))[1], copy=copied.append)
    return clipboard_sync, sync, sent, copied


def test_copied_text_goes_to_the_phone(monkeypatch, homes):
    _, sync, sent, _ = make_sync(monkeypatch, homes, ["text/plain;charset=utf-8", "UTF8_STRING"], "meet at 5")
    assert sync.changed() == (True, "") and sent == ["meet at 5"]


def test_a_password_managers_secret_never_leaves(monkeypatch, homes):
    _, sync, sent, _ = make_sync(monkeypatch, homes, ["text/plain", "x-kde-passwordManagerHint"], "hunter2")
    assert sync.changed() == (False, "a password manager marked it secret") and sent == []


def test_off_is_off_and_pictures_stay(monkeypatch, homes):
    _, sync, sent, _ = make_sync(monkeypatch, homes, ["text/plain"], "x", on=False)
    assert sync.changed()[0] is False and sent == []
    _, sync, sent, _ = make_sync(monkeypatch, homes, ["image/png"], "PNG")
    assert sync.changed() == (False, "not text") and sent == []


def test_what_came_from_the_phone_is_copied_recorded_and_not_echoed(monkeypatch, homes):
    clipboard_sync, sync, sent, copied = make_sync(monkeypatch, homes, ["text/plain"], "from the phone")
    sync.received("from the phone")
    assert copied == ["from the phone"]
    origins = json.loads(clipboard_sync.origins_file().read_text())
    assert origins[clipboard_sync.text_key("from the phone")]["device"] == "Pixel 9"
    # the clipboard watch fires for it too: it must not go back
    assert sync.changed() == (False, "came from the phone, or was just sent") and sent == []


@needs_tls
def test_clipboard_both_ways_over_the_real_link(tmp_path, homes):
    from shani_link import lan_link
    from fake_kdeconnect import FakePhone, PHONE_ID
    phone = FakePhone().start()
    lan_link._remember_pairing({"deviceId": PHONE_ID, "deviceName": "Fake Pixel"}, "127.0.0.1", phone.port)
    try:
        # here -> phone
        ok, why = lan_link.send_clipboard("127.0.0.1", "copied here", tcp_port=phone.port)
        assert ok, why
        deadline = time.time() + 5
        while not phone.got("kdeconnect.clipboard") and time.time() < deadline:
            time.sleep(0.05)
        assert phone.got("kdeconnect.clipboard")[0]["body"]["content"] == "copied here"
        # phone -> here, only from the paired phone
        got, done = [], threading.Event()
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        rx = lan_link.Receiver(tmp_path / "Downloads", None, port=port, paired_id=PHONE_ID,
                               on_clipboard=lambda text: (got.append(text), done.set()))
        assert rx.start()[0]
        try:
            phone.push_clipboard(port, "copied on the phone", device_id="a" * 32)   # a stranger: refused
            phone.push_clipboard(port, "copied on the phone")
            assert done.wait(10) and got == ["copied on the phone"]
        finally:
            rx.stop()
    finally:
        phone.stop()
