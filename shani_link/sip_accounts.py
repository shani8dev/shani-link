"""Ordinary internet SIP accounts (SIP trunking / VoIP providers), beside the
JioFiber landline.

A person adds a normal provider account - a username, a password, the
provider's domain, a port and a transport (UDP, TCP or TLS) - and this module
registers it and makes and takes calls on it, exactly like the landline line
but with nothing Jio-specific about it.

What is reused from the landline engine, and why:

- `landline.sip` (message parsing, digest, responses) - that is RFC 3261/7616,
  not Jio. It is the generic layer.
- `landline.media` (RTP, codecs, SDP) - the sound of a call, also generic.

What is *not* reused is `landline.ua.Line`: it is one TLS connection to one
router, with IMS headers (Route to the router, P-Access-Network-Info, MMTel
feature tags, a +91 number shape). Carrying any of that to a generic
provider would break most of them, so this module is a second, deliberately
smaller user agent: one call at a time per account, no hold/swap/merge, no
video. Audio only, and only the codecs `media` can play.

Accounts live in `~/.config/shani-link/sip-accounts.json`, mode 0600, written
atomically - the password is inside that file and nowhere else: never in a
log line, never in a D-Bus reply (`accounts()` strips it; `account()` is the
only way back in, and only the service's own process calls it).

Transports: UDP (with RFC 3261 retransmission), TCP, and TLS. TLS verifies
the provider's certificate against the system trust store - the opposite of
the landline's self-signed router, because a public provider's certificate
can actually be checked. There is no STUN/ICE/TURN and no UPnP: media goes
to the address the far end's SDP names, and the SIP `rport` mechanism tells
us our public address for Via/Contact. On a symmetric NAT the provider may
never reach us; that is a stated limit, not a silent one.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from shani_link import paths
from shani_link.landline import media, sip

logger = logging.getLogger(__name__)

USER_AGENT = "ShaniLink-SIP/1.0"
EXPIRES = 3600
KEEPALIVE_SECONDS = 25
ALLOW = "INVITE, ACK, CANCEL, BYE, OPTIONS, UPDATE, INFO, REGISTER"
TRANSPORTS = ("udp", "tcp", "tls")


class SipAccountError(RuntimeError):
    """A plain-language reason an account cannot be used or saved."""


# --- the account records ------------------------------------------------------

def _file():
    return paths.config_home() / "shani-link" / "sip-accounts.json"


def _load() -> List[dict]:
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        return []
    rows = data.get("accounts") if isinstance(data, dict) else data
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save(rows: List[dict]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"accounts": rows}, indent=1))
    os.chmod(tmp, 0o600)               # passwords live here; nothing else may read them
    tmp.replace(path)


def validate(fields: dict, keep_password: bool = False) -> dict:
    """One account's fields, checked and cleaned. Raises with the sentence to show."""
    fields = dict(fields or {})
    username = str(fields.get("username") or "").strip()
    domain = str(fields.get("domain") or "").strip()
    password = str(fields.get("password") or "")
    if username.lower().startswith("sip:"):
        username = username[4:]
    if any(ch.isspace() for ch in username):
        raise SipAccountError("The username should not have spaces in it - it is the account name your provider gave you.")
    if "@" in username and not domain:
        username, domain = username.split("@", 1)
    if not username:
        raise SipAccountError("The username is missing - your provider's account name (the part before the @, or the whole address).")
    domain = domain.split("://", 1)[-1].split("/", 1)[0].strip()
    port = fields.get("port")
    if domain and ":" in domain:
        host, _, tail = domain.rpartition(":")
        if host and tail.isdigit():
            domain, port = host, int(tail)
    if not domain or any(ch.isspace() for ch in domain):
        raise SipAccountError("The domain is missing - the provider's server name, like sip.example.com.")
    if port in (None, ""):
        port = 5060
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise SipAccountError("The port should be a number, usually 5060.")
    if not 1 <= port <= 65535:
        raise SipAccountError("The port should be between 1 and 65535.")
    transport = str(fields.get("transport") or "udp").strip().lower()
    if transport not in TRANSPORTS:
        raise SipAccountError("The transport should be UDP, TCP or TLS.")
    if not password and not keep_password:
        raise SipAccountError("The password is missing - the one your provider gave you for this account.")
    name = str(fields.get("name") or "").strip() or f"{username}@{domain}"
    return {"name": name, "username": username, "password": password, "domain": domain,
            "port": port, "transport": transport,
            "enabled": bool(fields.get("enabled", True)),
            "register": bool(fields.get("register", True))}


def line_id(account: dict) -> str:
    return "sip-" + str(account.get("id") or "")


def _public(account: dict) -> dict:
    """An account as anyone but this process may see it: no password, ever."""
    return {k: v for k, v in account.items() if k != "password"}


def accounts() -> List[dict]:
    """Every saved account, without passwords, oldest first."""
    return [_public(a) for a in _load()]


def account(account_id: str) -> Optional[dict]:
    """One account with its password - the service's own use only."""
    return next((a for a in _load() if str(a.get("id")) == account_id), None)


def _by_line(line: str) -> Optional[dict]:
    if not str(line).startswith("sip-"):
        return None
    return account(str(line)[4:])


def name_for_line(line: str) -> str:
    found = _by_line(line)
    return str(found.get("name") or "") if found else ""


def add_account(fields: dict) -> dict:
    cleaned = validate(fields)
    cleaned["id"] = uuid.uuid4().hex[:8]
    rows = _load()
    rows.append(cleaned)
    _save(rows)
    start(cleaned["id"])
    return _public(cleaned)


def update_account(account_id: str, fields: dict) -> dict:
    rows = _load()
    for i, row in enumerate(rows):
        if str(row.get("id")) == account_id:
            cleaned = validate(fields, keep_password=True)
            if not fields.get("password"):
                cleaned["password"] = row.get("password", "")
            elif not cleaned["password"]:
                raise SipAccountError("The password is missing - re-enter it, or leave the field empty to keep the saved one.")
            cleaned["id"] = account_id
            rows[i] = cleaned
            _save(rows)
            restart(account_id)
            return _public(cleaned)
    raise SipAccountError("That SIP account no longer exists.")


def remove_account(account_id: str) -> bool:
    stop(account_id)
    rows = _load()
    kept = [r for r in rows if str(r.get("id")) != account_id]
    if len(kept) == len(rows):
        return False
    _save(kept)
    return True


# --- one call -----------------------------------------------------------------

class SipCall:
    def __init__(self, call_id: str, local_tag: str, outgoing: bool, number: str) -> None:
        self.call_id = call_id
        self.local_tag = local_tag
        self.remote_tag = ""
        self.outgoing = outgoing
        self.number = number
        self.name = ""
        self.state = "dialing" if outgoing else "incoming"
        self.cseq = 1
        self.remote_target = ""
        self.route: List[str] = []
        self.local_uri = ""
        self.remote_uri = ""
        self.invite: Optional[sip.Message] = None
        self.answer_200: Optional[sip.Message] = None
        self.rtp: Optional[media.RtpSession] = None
        self.started = 0.0


def _why(reply: sip.Message) -> str:
    return {404: "that number does not exist", 408: "the number did not answer in time",
            480: "the number is not reachable", 486: "the line is busy",
            487: "cancelled", 603: "the call was declined",
            488: "the other side would not take any sound this computer can play (488)"
            }.get(reply.status, f"the network refused the call ({reply.status} {reply.reason})")


class SipAccount:
    """One provider account registered and ready - or honestly not."""

    def __init__(self, account: dict, on_event: Callable[..., None] = lambda kind, **info: None,
                 sound_factory: Optional[Callable[[], object]] = None,
                 tls_context: Optional[ssl.SSLContext] = None) -> None:
        self.account = dict(account)
        self.name = str(account.get("name") or account.get("username") or "SIP")
        self.username = str(account["username"])
        self.password = str(account.get("password") or "")
        self.domain = str(account["domain"])
        self.port = int(account.get("port") or 5060)
        self.transport = str(account.get("transport") or "udp").lower()
        self.register = bool(account.get("register", True))
        self.on_event = on_event
        self.sound_factory = sound_factory
        self._tls_context = tls_context
        self.local_uri = f"sip:{self.username}@{self.domain}"
        self.registered = False
        self.last_error = ""
        self.call: Optional[SipCall] = None
        self._expires = EXPIRES
        self._sock = None
        self._peer = None
        self._send_lock = threading.Lock()
        self._pending: Dict[str, dict] = {}
        self._reg_call_id = sip.new_call_id(self.domain)
        self._reg_tag = sip.new_tag()
        self._reg_cseq = 0
        self._service_route: List[str] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.local_ip = ""
        self.local_port = 0

    # --- the connection -------------------------------------------------------

    @property
    def ready(self) -> bool:
        return bool(self.registered) or (not self.register and self._sock is not None)

    def _resolve(self):
        infos = socket.getaddrinfo(self.domain, self.port,
                                   socket.AF_INET, socket.SOCK_STREAM if self.transport != "udp" else socket.SOCK_DGRAM)
        if not infos:
            raise OSError(f"could not find {self.domain}")
        return infos[0][4]

    def _connect(self) -> None:
        self._peer = self._resolve()
        if self.transport == "udp":
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", 0))
            sock.connect(self._peer)                      # picks the outbound address
            self.local_ip, self.local_port = sock.getsockname()[:2]
            self._sock = sock
            threading.Thread(target=self._reader_udp, args=(sock,), daemon=True,
                             name="sip-read").start()
            return
        raw = socket.create_connection(self._peer, timeout=10)
        if self.transport == "tls":
            ctx = self._tls_context or ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=self.domain)
        else:
            sock = raw
        sock.settimeout(None)
        self.local_ip, self.local_port = sock.getsockname()[:2]
        self._sock = sock
        threading.Thread(target=self._reader_stream, args=(sock,), daemon=True,
                         name="sip-read").start()

    def _close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self._connect()
                if not self.register:
                    self.registered = True
                    self.on_event("registered", number=self.username, account=self.name)
                    while not self._stop.wait(KEEPALIVE_SECONDS):
                        if self._sock is None:
                            raise OSError("the connection closed")
                        self._send_raw(b"\r\n\r\n")
                    return
                status = self._register()
                if status != 200:
                    self.last_error = f"{self.name}: the provider refused registration ({status})"
                    self.on_event("unregistered", reason=self.last_error, account=self.name)
                    if status in (401, 403):
                        return        # wrong username/password: retrying changes nothing
                    raise OSError(self.last_error)
                backoff = 2
                refresh_at = time.monotonic() + self._expires * 0.8
                while not self._stop.wait(KEEPALIVE_SECONDS):
                    if self._sock is None:
                        raise OSError("the connection closed")
                    self._send_raw(b"\r\n\r\n")
                    if time.monotonic() >= refresh_at:
                        if self._register() != 200:
                            raise OSError("re-registration failed")
                        refresh_at = time.monotonic() + self._expires * 0.8
            except (OSError, ssl.SSLError) as exc:
                if self._stop.is_set():
                    return
                self.last_error = str(exc)
                if self.registered:
                    self.registered = False
                    self.on_event("unregistered", reason=str(exc), account=self.name)
                self._close()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def _reader_udp(self, sock) -> None:
        while True:
            try:
                data, _addr = sock.recvfrom(65536)
            except OSError:
                data = b""
            if not data:
                if self._sock is sock:
                    self._close()
                for slot in list(self._pending.values()):
                    slot["event"].set()
                return
            messages, _rest = sip.split_stream(data)
            for message in messages:
                try:
                    self._dispatch(message)
                except Exception:  # noqa: BLE001 - one bad message must not end the account
                    logger.exception("sip account %s: could not handle %r", self.name, message)

    def _reader_stream(self, sock) -> None:
        buffer = b""
        while True:
            try:
                data = sock.recv(65536)
            except (OSError, ssl.SSLError):
                data = b""
            if not data:
                if self._sock is sock:
                    self._close()
                for slot in list(self._pending.values()):
                    slot["event"].set()
                return
            messages, buffer = sip.split_stream(buffer + data)
            for message in messages:
                try:
                    self._dispatch(message)
                except Exception:  # noqa: BLE001
                    logger.exception("sip account %s: could not handle %r", self.name, message)

    def _send_raw(self, data: bytes) -> None:
        if self._stop.is_set():
            return
        sock = self._sock
        if sock is None:
            raise OSError(f"{self.name} is not connected")
        with self._send_lock:
            if self.transport == "udp":
                sock.send(data)
            else:
                sock.sendall(data)

    def _send(self, message: sip.Message) -> None:
        logger.debug("%s >> %s", self.name, message.first)
        self._send_raw(message.encode())

    # --- transactions -----------------------------------------------------------

    def _via(self, branch: str, received: str = "") -> str:
        via = f"SIP/2.0/{self.transport.upper()} {self.local_ip}:{self.local_port};branch={branch};rport"
        if received:
            via += f";received={received}"
        return via

    def _contact(self, expires: Optional[int] = None) -> str:
        contact = (f"<sip:{self.username}@{self.local_ip}:{self.local_port};transport={self.transport}>"
                   f";expires={expires}" if expires is not None else
                   f"<sip:{self.username}@{self.local_ip}:{self.local_port};transport={self.transport}>")
        return contact

    def _request(self, message: sip.Message, timeout: float = 32.0) -> Optional[sip.Message]:
        """Send and wait for the final response. UDP requests are retransmitted
        (RFC 3261): a provider that never answered the first copy gets another."""
        branch = sip.param(message.get("Via"), "branch")
        slot = {"event": threading.Event(), "responses": []}
        self._pending[branch] = slot
        try:
            self._send(message)
            end = time.monotonic() + timeout
            sent_at = time.monotonic()
            delay = 0.5
            while time.monotonic() < end:
                slot["event"].wait(timeout=0.25)
                slot["event"].clear()
                if slot["responses"] and slot["responses"][-1].status >= 200:
                    return slot["responses"][-1]
                if self.transport == "udp" and time.monotonic() - sent_at >= delay:
                    self._send(message)                 # same branch: a retransmit, not a retry
                    sent_at = time.monotonic()
                    delay = min(delay * 2, 4.0)
                if self._sock is None and not slot["responses"]:
                    return None
            return slot["responses"][-1] if slot["responses"] and slot["responses"][-1].status >= 200 else None
        finally:
            self._pending.pop(branch, None)

    def _dispatch(self, message: sip.Message) -> None:
        logger.debug("%s << %s", self.name, message.first)
        if not message.is_request:
            branch = sip.param(message.get("Via"), "branch")
            slot = self._pending.get(branch)
            if slot is not None:
                slot["responses"].append(message)
                slot["event"].set()
            if message.method == "INVITE" and 100 < message.status < 200:
                call = self.call
                if call is not None and call.outgoing and call.state == "dialing" \
                        and message.call_id == call.call_id:
                    call.state = "ringing"
                    self.on_event("ringing", number=call.number, account=self.name)
            return
        handler = {"INVITE": self._on_invite, "ACK": self._on_ack, "BYE": self._on_bye,
                   "CANCEL": self._on_cancel, "UPDATE": self._on_update,
                   "OPTIONS": self._on_options}.get(message.method)
        if handler is not None:
            handler(message)
        else:
            self._send(sip.response(message, 200))

    # --- registration -------------------------------------------------------------

    def _register(self, expires: int = EXPIRES, timeout: float = 32.0) -> int:
        auth = ""
        for _attempt in range(2):
            self._reg_cseq += 1
            uri = f"sip:{self.domain}"
            headers = [
                ("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
                ("From", f"<{self.local_uri}>;tag={self._reg_tag}"), ("To", f"<{self.local_uri}>"),
                ("Call-ID", self._reg_call_id), ("CSeq", f"{self._reg_cseq} REGISTER"),
                ("Contact", self._contact(expires)), ("Expires", str(expires)),
                ("Allow", ALLOW), ("User-Agent", USER_AGENT),
            ]
            if auth:
                headers.append(("Authorization", auth))
            reply = self._request(sip.Message(f"REGISTER {uri} SIP/2.0", headers), timeout=timeout)
            if reply is None:
                raise OSError(f"{self.name}: the provider did not answer the registration")
            if reply.status in (401, 407) and not auth:
                challenge = reply.get("WWW-Authenticate" if reply.status == 401 else "Proxy-Authenticate")
                auth = sip.authorization(challenge, "REGISTER", uri, self.username, self.password)
                continue
            if reply.status == 200 and expires:
                self._service_route = reply.all("Service-Route")
                granted = sip.param(reply.get("Contact"), "expires") or reply.get("Expires")
                self._expires = int(granted) if str(granted).isdigit() and int(granted) > 60 else EXPIRES
                if not self.registered:
                    self.registered = True
                    self.on_event("registered", number=self.username, account=self.name)
            elif reply.status == 200:
                self.registered = False
            return reply.status
        return 401

    # --- calls out ------------------------------------------------------------------

    def dial(self, number: str) -> None:
        if not self.ready:
            raise SipAccountError(f"{self.name} is not connected - check the account's details and the network")
        if self.call is not None:
            raise SipAccountError(f"{self.name} already has a call in progress")
        target = self._target(number)
        if not target:
            raise SipAccountError("That is not a number - type the number to call.")
        call = SipCall(sip.new_call_id(self.local_ip), sip.new_tag(), True, target[1])
        call.local_uri, call.remote_uri = self.local_uri, target[0]
        call.route = list(self._service_route)
        call.rtp = media.RtpSession.bind(self.local_ip, self.sound_factory() if self.sound_factory else None)
        self.call = call
        threading.Thread(target=self._outgoing, args=(call,), daemon=True, name="sip-dial").start()

    def _target(self, number: str):
        """(request-URI, what to show) for a typed number, a full URI or user@host."""
        text = (number or "").strip()
        if not text:
            return None
        if text.lower().startswith("sip:"):
            return text, sip.user_of(text)
        if "@" in text:
            return f"sip:{text}", sip.user_of(text)
        user = "".join(ch for ch in text if ch.isdigit() or ch in "+*#")
        if not user:
            return None
        return f"sip:{user}@{self.domain}", user

    def _invite(self, call: SipCall, auth: str = "") -> sip.Message:
        headers = [
            ("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
            *[("Route", r) for r in call.route],
            ("From", f"<{call.local_uri}>;tag={call.local_tag}"), ("To", f"<{call.remote_uri}>"),
            ("Call-ID", call.call_id), ("CSeq", f"{call.cseq} INVITE"),
            ("Contact", self._contact()), ("Allow", ALLOW),
            ("User-Agent", USER_AGENT), ("Content-Type", "application/sdp"),
        ]
        if auth:
            headers.append(("Proxy-Authorization" if auth.startswith("P:") else "Authorization",
                            auth[2:] if auth.startswith("P:") else auth))
        body = media.offer(self.local_ip, call.rtp.port)   # type: ignore[union-attr]
        return sip.Message(f"INVITE {call.remote_uri} SIP/2.0", headers, body)

    def _outgoing(self, call: SipCall) -> None:
        auth = ""
        try:
            for _attempt in range(2):
                invite = self._invite(call, auth)
                call.invite = invite
                reply = self._request(invite, timeout=180)
                if reply is None:
                    self._end(call, f"{self.name}: the provider did not answer")
                    return
                if reply.status in (401, 407) and not auth:
                    self._ack_failure(invite, reply)
                    challenge = reply.get("WWW-Authenticate" if reply.status == 401 else "Proxy-Authenticate")
                    value = sip.authorization(challenge, "INVITE", call.remote_uri, self.username, self.password)
                    auth = ("P:" if reply.status == 407 else "") + value
                    call.cseq += 1
                    continue
                if reply.status >= 300:
                    self._ack_failure(invite, reply)
                    self._end(call, _why(reply), status=reply.status)
                    return
                self._answered_out(call, reply)
                return
        except Exception as exc:  # noqa: BLE001
            logger.exception("sip account %s: the call failed", self.name)
            if self.call is call:
                self._end(call, str(exc))

    def _ack_failure(self, invite: sip.Message, reply: sip.Message) -> None:
        headers = [("Via", invite.get("Via")), ("Max-Forwards", "70"),
                   *[("Route", r) for r in invite.all("Route")],
                   ("From", invite.get("From")), ("To", reply.get("To")),
                   ("Call-ID", invite.call_id), ("CSeq", f"{invite.cseq[0]} ACK")]
        self._send(sip.Message(f"ACK {invite.uri} SIP/2.0", headers))

    def _answered_out(self, call: SipCall, reply: sip.Message) -> None:
        call.remote_tag = sip.param(reply.get("To"), "tag")
        call.remote_target = sip.uri_of(reply.get("Contact")) or call.remote_uri
        if reply.all("Record-Route"):
            call.route = list(reversed(reply.all("Record-Route")))
        self._in_dialog(call, "ACK", cseq=call.cseq)
        if self.call is not call:                    # hung up while being answered
            self._in_dialog(call, "BYE")
            return
        if not reply.body.strip():
            self._in_dialog(call, "BYE")
            self._end(call, "the other side answered with no sound at all")
            return
        if not self._start_media(call, reply.body):
            self._in_dialog(call, "BYE")
            self._end(call, "the other side answered with no sound this computer can play")
            return
        call.state, call.started = "active", time.monotonic()
        self.on_event("answered", number=call.number, account=self.name)

    def _start_media(self, call: SipCall, body: str) -> bool:
        ip, port, codecs, dtmf = media.parse_sdp(body)
        chosen = media.pick(codecs)
        if not ip or not port or chosen is None or not media.can_play(chosen[1]):
            return False
        packing = media.packing_for(media.negotiated(codecs, chosen[0]))
        try:
            call.rtp.start((ip, port), chosen[0], chosen[1], dtmf, packing)   # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("sip account %s: the sound would not start: %s", self.name, exc)
            return False
        return True

    # --- calls in ---------------------------------------------------------------------

    def _on_invite(self, invite: sip.Message) -> None:
        call = self.call
        if call is not None and invite.call_id == call.call_id:
            if call.answer_200 is not None:          # they retransmitted the INVITE after our 200
                self._send(call.answer_200)
            return
        if call is not None:
            self._send(sip.response(invite, 486, to_tag=sip.new_tag()))
            return
        call = SipCall(invite.call_id, sip.new_tag(), False, sip.user_of(invite.get("From")))
        call.name = sip.display_name(invite.get("P-Asserted-Identity") or invite.get("From"))
        asserted = invite.get("P-Asserted-Identity")
        if asserted:
            call.number = sip.user_of(asserted)
        call.invite = invite
        call.remote_tag = sip.param(invite.get("From"), "tag")
        call.remote_target = sip.uri_of(invite.get("Contact"))
        call.route = invite.all("Record-Route")
        call.local_uri, call.remote_uri = sip.uri_of(invite.get("To")), sip.uri_of(invite.get("From"))
        call.rtp = media.RtpSession.bind(self.local_ip, None)
        if media.pick(media.parse_sdp(invite.body)[2]) is None:
            call.rtp.stop()
            self._send(sip.response(invite, 488, to_tag=call.local_tag))
            self.on_event("ended", number=call.number, reason="a call came in with no sound this computer can play (488)",
                          seconds=0, account=self.name)
            return
        self.call = call
        self._send(sip.response(invite, 100))
        self._send(sip.response(invite, 180, to_tag=call.local_tag,
                                extra=[("Contact", self._contact())]))
        self.on_event("incoming", number=call.number, name=call.name, account=self.name)

    def answer(self) -> None:
        call = self.call
        if call is None or call.outgoing or call.state != "incoming":
            raise SipAccountError("no call is ringing on this account")
        if self.sound_factory is not None:
            call.rtp.sound = self.sound_factory()     # type: ignore[union-attr]
        ip, port, codecs, dtmf = media.parse_sdp(call.invite.body)   # type: ignore[union-attr]
        chosen = media.pick(codecs)
        if not ip or not port or chosen is None or not media.can_play(chosen[1]):
            self._send(sip.response(call.invite, 488, to_tag=call.local_tag))   # type: ignore[union-attr]
            self._end(call, "this computer cannot play the sound this call offered (488)")
            return
        body = media.offer(self.local_ip, call.rtp.port, only=chosen[1])   # type: ignore[union-attr]
        call.answer_200 = sip.response(call.invite, 200, to_tag=call.local_tag, body=body,   # type: ignore[union-attr]
                                       extra=[("Contact", self._contact()), ("Allow", ALLOW)])
        self._send(call.answer_200)
        packing = media.packing_for(media.negotiated(codecs, chosen[0]))
        call.rtp.start((ip, port), chosen[0], chosen[1], dtmf, packing)   # type: ignore[union-attr]
        call.state, call.started = "active", time.monotonic()
        self.on_event("answered", number=call.number, account=self.name)

    def _on_ack(self, _ack: sip.Message) -> None:
        pass

    def _on_bye(self, bye: sip.Message) -> None:
        call = self.call
        if call is None or bye.call_id != call.call_id:
            self._send(sip.response(bye, 481))
            return
        self._send(sip.response(bye, 200))
        self._end(call, "the other side hung up")

    def _on_update(self, update: sip.Message) -> None:
        call = self.call
        if call is None or update.call_id != call.call_id:
            self._send(sip.response(update, 200))
            return
        if not update.body.strip():
            self._send(sip.response(update, 200, to_tag=call.local_tag,
                                     extra=[("Contact", self._contact())]))
            return
        ip, port, codecs, dtmf = media.parse_sdp(update.body)
        chosen = media.pick(codecs)
        if not ip or not port or chosen is None or not media.can_play(chosen[1]):
            self._send(sip.response(update, 488, to_tag=call.local_tag))
            return
        if call.rtp.sound is None and self.sound_factory is not None:   # type: ignore[union-attr]
            call.rtp.sound = self.sound_factory()                       # type: ignore[union-attr]
        body = media.offer(self.local_ip, call.rtp.port, only=chosen[1])  # type: ignore[union-attr]
        self._send(sip.response(update, 200, to_tag=call.local_tag, body=body,
                                extra=[("Contact", self._contact())]))
        packing = media.packing_for(media.negotiated(codecs, chosen[0]))
        try:
            call.rtp.start((ip, port), chosen[0], chosen[1], dtmf, packing)   # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("sip account %s: the re-offered sound would not start: %s", self.name, exc)
            self._end(call, "this computer could not take the sound re-offered mid-call")

    def _on_cancel(self, cancel: sip.Message) -> None:
        call = self.call
        self._send(sip.response(cancel, 200))
        if call is not None and cancel.call_id == call.call_id and call.state == "incoming":
            self._send(sip.response(call.invite, 487, to_tag=call.local_tag))   # type: ignore[union-attr]
            self._end(call, "missed call")

    def _on_options(self, options: sip.Message) -> None:
        self._send(sip.response(options, 200, to_tag=sip.new_tag(),
                                extra=[("Contact", self._contact()), ("Allow", ALLOW)]))

    # --- ending -------------------------------------------------------------------------

    def hangup(self) -> None:
        call = self.call
        if call is None:
            raise SipAccountError("there is no call on this account")
        if not call.outgoing and call.state == "incoming":
            self._send(sip.response(call.invite, 603, to_tag=call.local_tag))   # type: ignore[union-attr]
            self._end(call, "declined")
        elif call.outgoing and call.state in ("dialing", "ringing"):
            invite = call.invite
            cancel = sip.Message(f"CANCEL {invite.uri} SIP/2.0", [              # type: ignore[union-attr]
                ("Via", invite.get("Via")), ("Max-Forwards", "70"),
                *[("Route", r) for r in invite.all("Route")],
                ("From", invite.get("From")), ("To", invite.get("To")),
                ("Call-ID", invite.call_id), ("CSeq", f"{invite.cseq[0]} CANCEL")])
            self._end(call, "cancelled")
            self._send(cancel)
        else:
            self._end(call, "hung up")
            threading.Thread(target=self._in_dialog, args=(call, "BYE"), daemon=True).start()

    def dtmf(self, digit: str) -> bool:
        call = self.call
        return bool(call and call.state == "active" and call.rtp and call.rtp.dtmf(digit))

    def _in_dialog(self, call: SipCall, method: str, cseq: Optional[int] = None) -> Optional[sip.Message]:
        if cseq is None:
            call.cseq += 1
        number = call.cseq if cseq is None else cseq
        headers = [("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
                   *[("Route", r) for r in call.route],
                   ("From", f"<{call.local_uri}>;tag={call.local_tag}"),
                   ("To", f"<{call.remote_uri}>;tag={call.remote_tag}"),
                   ("Call-ID", call.call_id), ("CSeq", f"{number} {method}"),
                   ("User-Agent", USER_AGENT)]
        message = sip.Message(f"{method} {call.remote_target or call.remote_uri} SIP/2.0", headers)
        if method == "ACK":
            self._send(message)
            return None
        return self._request(message, timeout=10)

    def _end(self, call: SipCall, reason: str, status: int = 0) -> None:
        if self.call is call:
            self.call = None
        if call.rtp is not None:
            call.rtp.stop()
        seconds = int(time.monotonic() - call.started) if call.started else 0
        call.state = "ended"
        self.on_event("ended", number=call.number, reason=reason, seconds=seconds,
                      status=status, account=self.name)

    # --- life -----------------------------------------------------------------------------

    def start(self) -> bool:
        if self._thread is not None or self._stop.is_set():
            return True
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"sip-{self.name}")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self.call is not None:
            try:
                self.hangup()
            except Exception:  # noqa: BLE001
                pass
        if self.registered and self.register:
            try:
                self._register(expires=0, timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
        self._close()


# --- the running accounts -----------------------------------------------------------------

_RUNTIMES: Dict[str, SipAccount] = {}
_LISTENERS: List[Callable[..., None]] = []
_STATE: Dict[str, dict] = {}


def listen(callback: Callable[..., None]) -> Callable[[], None]:
    _LISTENERS.append(callback)
    return lambda: callback in _LISTENERS and _LISTENERS.remove(callback)


def _name_for(number: str) -> str:
    try:
        from shani_link import contacts
        return contacts.name_for(number)
    except Exception:  # noqa: BLE001
        return ""


def _on_event(account_id: str, kind: str, **info) -> None:
    """History and listeners: one ended call is one history row, as the landline does."""
    line = "sip-" + account_id
    state = _STATE.setdefault(account_id, {})
    if kind == "ringing":
        state["dir"] = "placed"
    elif kind == "incoming":
        state["dir"] = "received"
    elif kind == "answered":
        state["answered"] = True
    elif kind == "ended":
        from shani_link import calls_log
        direction = state.pop("dir", "placed")
        answered = state.pop("answered", False)
        number = info.get("number", "")
        calls_log.add(line, number, "in" if direction == "received" else "out",
                      answered or direction == "placed", int(info.get("seconds", 0) or 0),
                      name=info.get("name", "") or _name_for(number))
    for callback in list(_LISTENERS):
        try:
            callback(kind, **info)
        except Exception:  # noqa: BLE001
            logger.debug("sip account listener failed", exc_info=True)
    if kind == "incoming":
        _notify(account_id, _name_for(info.get("number", "")) or info.get("name")
                or info.get("number") or "Unknown caller", info.get("number", ""))


def _make(account: dict) -> SipAccount:
    return SipAccount(account, on_event=lambda kind, **info: _on_event(account["id"], kind, **info))


def start(account_id: str) -> bool:
    """Register one saved account (a no-op when it is already running or off)."""
    saved = account(account_id)
    if saved is None or not saved.get("enabled", True):
        return False
    if account_id in _RUNTIMES:
        return True
    runtime = _make(saved)
    _RUNTIMES[account_id] = runtime
    runtime.start()
    return True


def restart(account_id: str) -> None:
    stop(account_id)
    start(account_id)


def stop(account_id: str) -> None:
    runtime = _RUNTIMES.pop(account_id, None)
    if runtime is not None:
        runtime.stop()


def start_all() -> None:
    """Every enabled account, at service start."""
    for saved in _load():
        if saved.get("enabled", True):
            start(str(saved["id"]))


def stop_all() -> None:
    for account_id in list(_RUNTIMES):
        stop(account_id)


def runtime(line: str) -> Optional[SipAccount]:
    if not str(line).startswith("sip-"):
        return None
    return _RUNTIMES.get(str(line)[4:])


def ready(line: str) -> bool:
    runtime_ = runtime(line)
    return bool(runtime_ and runtime_.ready)


def needs(line: str) -> SipAccount:
    runtime_ = runtime(line)
    if runtime_ is None:
        raise SipAccountError("that SIP account is not running - add it again if it was removed")
    return runtime_


def dial(line: str, number: str) -> str:
    runtime_ = needs(line)
    runtime_.dial(number)
    return f"Calling {number} on {runtime_.name}."


def answer(line: str) -> bool:
    needs(line).answer()
    return True


def hangup(line: str) -> bool:
    needs(line).hangup()
    return True


def dtmf(line: str, digit: str) -> bool:
    return bool(needs(line).dtmf(digit))


# --- the incoming-call card -----------------------------------------------------------------

_NOTIFIES = {"nid": 0, "subscribed": False}


def _notify(account_id: str, who: str, number: str) -> None:
    try:
        from gi.repository import Gio, GLib
        from shani_link.landline import NOTIFY
        from shani_link.notify import APP_NAME, hints as _hints
        account_name = name_for_line("sip-" + account_id) or "SIP"
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        hints = _hints("call.incoming", urgency=GLib.Variant("y", 2),
                       **{"x-shani-link": GLib.Variant("s", "sip-" + account_id)})
        reply = bus.call_sync(*NOTIFY, "Notify", GLib.Variant("(susssasa{sv}i)", (
            APP_NAME, 0, "call-start-symbolic", f"{account_name}: {who}",
            number if number and number != who else "Incoming call",
            ["sip-answer", "Answer", "sip-decline", "Decline"], hints, 0)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 3000, None)
        _NOTIFIES["nid"] = int(reply.unpack()[0])
    except Exception:  # noqa: BLE001
        logger.debug("sip account: no call card", exc_info=True)


def _subscribe_actions() -> None:
    if _NOTIFIES["subscribed"]:
        return
    try:
        from gi.repository import Gio
        from shani_link.landline import NOTIFY
        Gio.bus_get_sync(Gio.BusType.SESSION, None).signal_subscribe(
            NOTIFY[0], NOTIFY[2], "ActionInvoked", NOTIFY[1], None,
            Gio.DBusSignalFlags.NONE, _on_action)
        _NOTIFIES["subscribed"] = True
    except Exception:  # noqa: BLE001
        pass


def _on_action(_conn, _sender, _path, _iface, _signal, params) -> None:
    nid, key = params.unpack()
    if nid != _NOTIFIES["nid"] or not key.startswith("sip-"):
        return
    # The account is named in the notification's hint, which ActionInvoked
    # does not carry; every running account is asked, and only the ringing
    # one answers.
    for account_id in list(_RUNTIMES):
        runtime_ = _RUNTIMES[account_id]
        if runtime_.call is None or runtime_.call.state != "incoming":
            continue
        try:
            if key == "sip-answer":
                runtime_.answer()
            else:
                runtime_.hangup()
        except SipAccountError as exc:
            logger.info("sip account: %s", exc)
        return


_subscribe_actions()
