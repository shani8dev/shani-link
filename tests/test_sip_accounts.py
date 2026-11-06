"""Ordinary SIP accounts, end to end against a fake provider on loopback.

Driven over real UDP and TCP sockets (never a real account): registration
with the digest answered, a call out (180 then 200, ACK, RTP both ways, BYE),
a call in (ring, answer, hangup), a busy second call, a declined call, a
wrong password that stops retrying, and the account file itself - validation,
passwords kept out of every public view, and mode 0600 on disk.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from shani_link import sip_accounts
from shani_link.landline import media, sip

REALM = "sip.test"
USERNAME = "alice"
PASSWORD = "correct horse"


def wait_for(predicate, timeout=10, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("timed out waiting for the fake provider")


class Silence:
    """A sound with no microphone and no speaker: silence out, bytes in."""

    def __init__(self):
        self.heard = bytearray()

    def read(self):
        return b"\0" * media.FRAME_BYTES

    def write(self, pcm):
        self.heard += pcm

    def close(self):
        pass


class Provider:
    """A fake SIP provider: digest REGISTER, calls both ways, RTP on loopback."""

    def __init__(self, transport="udp"):
        self.transport = transport
        self.username, self.password = USERNAME, PASSWORD
        self.seen = {"register": [], "invite": [], "bye": 0, "ack": 0, "cancel": 0}
        self.rtp_in = 0
        self.rtp_port = self._free_port()
        self._stop = threading.Event()
        if transport == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(("127.0.0.1", 0))
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("127.0.0.1", 0))
            self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True, name="fake-provider")
        self._thread.start()
        self._rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rtp.bind(("127.0.0.1", self.rtp_port))

    @staticmethod
    def _free_port():
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def _serve(self):
        if self.transport == "udp":
            self.sock.settimeout(0.2)
            while not self._stop.is_set():
                try:
                    data, addr = self.sock.recvfrom(65536)
                except socket.timeout:
                    continue
                except OSError:
                    return
                for message in [sip.parse(data)]:
                    self._handle(message, addr)
        else:
            self.sock.settimeout(0.2)
            conn = None
            while not self._stop.is_set():
                if conn is None:
                    try:
                        conn, _ = self.sock.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                try:
                    data = conn.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    conn = None
                    continue
                messages, _rest = sip.split_stream(data)
                for message in messages:
                    self._handle(message, None, conn)

    def _send(self, message, addr=None, conn=None):
        raw = message.encode()
        if conn is not None:
            conn.sendall(raw)
        else:
            self.sock.sendto(raw, addr)

    def _handle(self, message, addr=None, conn=None):
        def send(msg):
            self._send(msg, addr, conn)

        if message.method == "REGISTER":
            self.seen["register"].append(message)
            auth = message.get("Authorization")
            if not auth:
                send(sip.Message("SIP/2.0 401 Unauthorized", [
                    ("Via", message.get("Via")), ("From", message.get("From")),
                    ("To", message.get("To")), ("Call-ID", message.call_id),
                    ("CSeq", message.get("CSeq")),
                    ("WWW-Authenticate", f'Digest realm="{REALM}", nonce="abc123", qop="auth"'),
                    ("Content-Length", "0")]))
                return
            if sip.check_authorization(auth, "REGISTER", self.password):
                expires = sip.param(message.get("Contact"), "expires")
                send(sip.Message("SIP/2.0 200 OK", [
                    ("Via", message.get("Via")), ("From", message.get("From")),
                    ("To", message.get("To")), ("Call-ID", message.call_id),
                    ("CSeq", message.get("CSeq")),
                    ("Contact", f"{message.get('Contact')};expires={expires or 3600}"),
                    ("Content-Length", "0")]))
            else:
                send(sip.Message("SIP/2.0 403 Forbidden", [
                    ("Via", message.get("Via")), ("From", message.get("From")),
                    ("To", message.get("To")), ("Call-ID", message.call_id),
                    ("CSeq", message.get("CSeq")), ("Content-Length", "0")]))
            return
        if message.method == "INVITE":
            self.seen["invite"].append(message)
            call_id = message.call_id
            threading.Thread(target=self._answer_invite, args=(message, addr, conn), daemon=True).start()
            return
        if message.method == "ACK":
            self.seen["ack"] += 1
            return
        if message.method == "BYE":
            self.seen["bye"] += 1
            send(sip.Message("SIP/2.0 200 OK", [
                ("Via", message.get("Via")), ("From", message.get("From")),
                ("To", message.get("To")), ("Call-ID", message.call_id),
                ("CSeq", message.get("CSeq")), ("Content-Length", "0")]))
            return
        if message.method == "CANCEL":
            self.seen["cancel"] += 1
            send(sip.Message("SIP/2.0 200 OK", [
                ("Via", message.get("Via")), ("From", message.get("From")),
                ("To", message.get("To")), ("Call-ID", message.call_id),
                ("CSeq", message.get("CSeq")), ("Content-Length", "0")]))
            return
        if message.method in ("OPTIONS", "UPDATE"):
            send(sip.Message("SIP/2.0 200 OK", [
                ("Via", message.get("Via")), ("From", message.get("From")),
                ("To", message.get("To")), ("Call-ID", message.call_id),
                ("CSeq", message.get("CSeq")), ("Allow", "INVITE, ACK, BYE"),
                ("Content-Length", "0")]))

    def _answer_invite(self, invite, addr, conn):
        def send(msg):
            self._send(msg, addr, conn)

        _ip, port, _codecs, _dtmf = media.parse_sdp(invite.body)
        self.client_rtp = ("127.0.0.1", port)
        time.sleep(0.1)
        if self._stop.is_set():
            return
        send(sip.response(invite, 180, to_tag="prov1",
                          extra=[("Contact", f"<sip:bob@127.0.0.1:{self.port}>")]))
        time.sleep(0.1)
        body = (f"v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
                f"m=audio {self.rtp_port} RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=ptime:20\r\n")
        send(sip.response(invite, 200, to_tag="prov1", body=body,
                          extra=[("Contact", f"<sip:bob@127.0.0.1:{self.port}>")]))
        self._rtp_loop()

    def _rtp_loop(self):
        """RTP from us to the client, and count what comes back."""
        self._rtp.settimeout(0.5)
        deadline = time.monotonic() + 8
        packet = bytes([0x80, 0]) + (1).to_bytes(2, "big") + (2).to_bytes(4, "big") + \
            (3).to_bytes(4, "big") + b"\xff" * 160
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                data, addr = self._rtp.recvfrom(2048)
                if data:
                    self.rtp_in += 1
            except socket.timeout:
                pass
            except OSError:
                return
            # the client's answer port: borrow the invite's SDP port from the last answer we sent
            # (the test reads provider.seen; the actual send target is set by ring/outgoing tests)
            target = getattr(self, "client_rtp", None)
            if target:
                try:
                    self._rtp.sendto(packet, target)
                except OSError:
                    pass

    def ring(self, number="+15551234567"):
        """Ring the client: an INVITE from bob, and keep our side of it."""
        call_id = sip.new_call_id("127.0.0.1")
        body = (f"v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
                f"m=audio {self.rtp_port} RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=ptime:20\r\n")
        # The client's address: the last REGISTER's Via is the only thing we have.
        via = self.seen["register"][-1].get("Via") if self.seen["register"] else ""
        hostport = via.split(" ", 2)[-1].split(";", 1)[0]
        ip, _, port = hostport.partition(":")
        invite = sip.Message(f"INVITE sip:{USERNAME}@{REALM} SIP/2.0", [
            ("Via", f"SIP/2.0/{self.transport.upper()} {ip}:{port};branch={sip.new_branch()}"),
            ("From", f"<sip:bob@{REALM}>;tag=bob1"),
            ("To", f"<sip:{USERNAME}@{REALM}>"),
            ("Call-ID", call_id), ("CSeq", "1 INVITE"),
            ("Contact", f"<sip:bob@127.0.0.1:{self.port}>"),
            ("Content-Type", "application/sdp")], body)
        self._send(invite, (ip, int(port or 5060)))
        return call_id


@pytest.fixture()
def provider():
    return Provider("udp")


def _account(provider, **overrides):
    fields = {"name": "Test SIP", "username": USERNAME, "password": PASSWORD,
              "domain": "127.0.0.1", "port": provider.port, "transport": provider.transport}
    fields.update(overrides)
    return fields


def _start(provider, events, sounds, **overrides):
    saved = sip_accounts.validate(_account(provider, **overrides))
    saved["id"] = "testacct"
    runtime = sip_accounts.SipAccount(saved, on_event=lambda k, **i: events.append((k, i)),
                                      sound_factory=lambda: sounds.append(Silence()) or sounds[-1])
    runtime.start()
    return runtime


def test_accounts_are_saved_private_and_passwords_stay_inside(provider):
    added = sip_accounts.add_account(_account(provider))
    assert added["name"] == "Test SIP" and "password" not in added
    assert [a["name"] for a in sip_accounts.accounts()] == ["Test SIP"]
    assert "password" not in sip_accounts.accounts()[0]
    full = sip_accounts.account(added["id"])
    assert full["password"] == PASSWORD
    assert sip_accounts._file().stat().st_mode & 0o777 == 0o600
    assert sip_accounts.remove_account(added["id"]) is True
    assert sip_accounts.accounts() == []


@pytest.mark.parametrize("fields,why", [
    ({"username": "", "domain": "sip.test", "password": "x"}, "username is missing"),
    ({"username": "alice", "domain": "", "password": "x"}, "domain is missing"),
    ({"username": "alice", "domain": "sip.test", "password": ""}, "password is missing"),
    ({"username": "a b", "domain": "sip.test", "password": "x"}, "spaces"),
    ({"username": "alice", "domain": "sip.test", "password": "x", "port": "abc"}, "port"),
    ({"username": "alice", "domain": "sip.test", "password": "x", "port": 0}, "65535"),
    ({"username": "alice", "domain": "sip.test", "password": "x", "transport": "sctp"}, "transport"),
])
def test_account_fields_are_checked_in_plain_words(fields, why):
    with pytest.raises(sip_accounts.SipAccountError, match=why):
        sip_accounts.validate(fields)


def test_a_full_address_and_a_port_in_the_domain_are_split_for_you():
    cleaned = sip_accounts.validate({"username": "sip:alice@sip.test:5067", "password": "x"})
    assert cleaned["username"] == "alice" and cleaned["domain"] == "sip.test"
    assert cleaned["port"] == 5067 and cleaned["transport"] == "udp"


def test_registration_answers_the_digest(provider):
    events, sounds = [], []
    runtime = _start(provider, events, sounds)
    try:
        assert wait_for(lambda: runtime.registered)
        assert len(provider.seen["register"]) == 2          # unauthenticated, then digest
        assert any(k == "registered" for k, _ in events)
    finally:
        runtime.stop()


def test_a_call_out_rings_answers_and_carries_sound(provider):
    events, sounds = [], []
    runtime = _start(provider, events, sounds)
    try:
        assert wait_for(lambda: runtime.registered)
        runtime.dial("+15559876543")
        assert wait_for(lambda: any(k == "ringing" or (k == "answered") for k, _ in events))
        assert wait_for(lambda: runtime.call and runtime.call.state == "active")
        assert wait_for(lambda: provider.rtp_in > 0)
        assert provider.seen["ack"] >= 1
        assert provider.seen["invite"][0].uri == "sip:+15559876543@127.0.0.1"
        runtime.hangup()
        assert wait_for(lambda: provider.seen["bye"] >= 1)
        assert wait_for(lambda: runtime.call is None)
        assert any(k == "ended" for k, _ in events)
    finally:
        runtime.stop()


def test_a_call_in_rings_and_is_answered(provider):
    events, sounds = [], []
    runtime = _start(provider, events, sounds)
    try:
        assert wait_for(lambda: runtime.registered)
        provider.ring()
        assert wait_for(lambda: any(k == "incoming" for k, _ in events))
        runtime.answer()
        assert wait_for(lambda: runtime.call and runtime.call.state == "active")
        runtime.hangup()
        assert wait_for(lambda: provider.seen["bye"] >= 1)
    finally:
        runtime.stop()


def test_a_ringing_call_can_be_declined(provider):
    events, sounds = [], []
    runtime = _start(provider, events, sounds)
    try:
        assert wait_for(lambda: runtime.registered)
        provider.ring()
        assert wait_for(lambda: any(k == "incoming" for k, _ in events))
        runtime.hangup()
        assert wait_for(lambda: runtime.call is None)
    finally:
        runtime.stop()


def test_a_second_call_is_told_busy(provider):
    events, sounds = [], []
    runtime = _start(provider, events, sounds)
    try:
        assert wait_for(lambda: runtime.registered)
        runtime.dial("+15559876543")
        assert wait_for(lambda: runtime.call and runtime.call.state == "active")
        with pytest.raises(sip_accounts.SipAccountError, match="in progress"):
            runtime.dial("+15550000000")
    finally:
        runtime.stop()


def test_a_wrong_password_is_refused_once(provider):
    events, sounds = [], []
    runtime = _start(provider, events, sounds, password="wrong")
    try:
        assert wait_for(lambda: runtime.last_error and not runtime.registered)
        time.sleep(0.5)
        assert len(provider.seen["register"]) == 2          # one try, one digest, then no retrying
    finally:
        runtime.stop()


def test_tcp_works_too():
    provider = Provider("tcp")
    events, sounds = [], []
    runtime = _start(provider, events, sounds)
    try:
        assert wait_for(lambda: runtime.registered)
        runtime.dial("+15559876543")
        assert wait_for(lambda: runtime.call and runtime.call.state == "active")
    finally:
        runtime.stop()
        provider.close()


def test_the_manager_logs_calls_and_names_the_line(provider):
    saved = sip_accounts.add_account(_account(provider))
    line = sip_accounts.line_id(saved)
    try:
        assert wait_for(lambda: sip_accounts.ready(line))
        sip_accounts.dial(line, "+15559876543")
        assert wait_for(lambda: sip_accounts.runtime(line).call.state == "active")
        sip_accounts.hangup(line)
        from shani_link import calls_log
        rows = wait_for(lambda: calls_log.rows(5, line=line) or None)
        assert rows[0]["number"] == "+15559876543" and rows[0]["direction"] == "out"
        assert "Test SIP" in calls_log.describe(rows[0])
    finally:
        sip_accounts.remove_account(saved["id"])


def test_update_keeps_the_saved_password_when_left_empty(provider):
    added = sip_accounts.add_account(_account(provider))
    try:
        updated = sip_accounts.update_account(added["id"], {**_account(provider), "password": ""})
        assert sip_accounts.account(added["id"])["password"] == PASSWORD
        assert "password" not in updated
    finally:
        sip_accounts.remove_account(added["id"])
