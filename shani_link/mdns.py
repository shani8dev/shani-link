"""A small DNS-SD browser for adb's wireless-debugging services.

**Why this exists.** `adb mdns services` is the obvious way to find a phone, and
it is empty on Debian's `android-tools`: the binary has the `mdns` subcommand
but no backend behind it (`ADB_TRACE=mdns` answers "unknown trace flag"), so it
lists nothing on a network where the phone is plainly advertising. That made Wi-Fi
pairing look impossible - the address had to be read off the phone and typed.

The protocol behind it is small. One PTR question to 224.0.0.251:5353 brings
back the instance; a follow-up (or the same packet) carries SRV with the port and
the host, and an A record with the address:

    _adb-tls-connect._tcp.local  PTR  adb-<guid>
    adb-<guid>                   SRV  37133  <phone>.local
    adb-<guid>                   TXT  name=<model> v=1
    <phone>.local                A    192.168.31.79

The instance name is the pairing GUID, so a phone can be recognised across
reboots and a changed port (`connect_after_pairing` matches on it, not just the
IP). Responses are multicast to port 5353, so this binds that port and joins the
group; `browse()` takes a destination and a bind port so a test can answer on
localhost instead.

No dependency: `python-zeroconf` would be one more package in the image for two
record types.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

GROUP = "224.0.0.251"
PORT = 5353

PAIRING = "_adb-tls-pairing._tcp"
CONNECT = "_adb-tls-connect._tcp"


@dataclass
class Service:
    """One advertisement: the instance, where it is, and what it says about itself."""

    name: str
    host: str = ""
    port: int = 0
    addresses: Set[str] = field(default_factory=set)
    txt: Dict[str, str] = field(default_factory=dict)

    @property
    def address(self) -> str:
        """The first IPv4 address, or "" when the A record has not arrived yet."""
        return next(iter(sorted(self.addresses)), "")

    def __str__(self) -> str:
        return f"{self.name} at {self.address}:{self.port} ({self.host})"


def question(name: str) -> bytes:
    """A PTR question for `name`, with an empty answer section."""
    labels = b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\x00"
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + labels + struct.pack(">HH", 12, 1)


def _read_name(data: bytes, offset: int) -> "tuple[str, int]":
    """One name, following compression pointers. Returns (name, next offset)."""
    parts: List[str] = []
    jumped = False
    end = offset
    for _ in range(64):                       # a loop in the pointers is not a name
        if offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            if not jumped:
                end = offset
            break
        if length & 0xC0:
            pointer = struct.unpack(">H", data[offset:offset + 2])[0] & 0x3FFF
            if not jumped:
                end = offset + 2
            jumped = True
            offset = pointer
            continue
        parts.append(data[offset + 1:offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length
    return ".".join(parts), end


def _instance(name: str, service: str) -> str:
    """The instance label: `adb-<guid>` from `adb-<guid>._adb-tls-connect._tcp.local`.

    The QR flow names a phone by this label (`chronoa-abc123`), and the PTR
    record carries it with the service suffix attached; stripping it is what
    makes the two comparable.
    """
    suffix = f".{service}.local"
    return name[:-len(suffix)] if name.endswith(suffix) else name


def _txt(data: bytes) -> Dict[str, str]:
    """The key=value strings of an RDATA that is one or more length-prefixed strings."""
    out: Dict[str, str] = {}
    offset = 0
    while offset < len(data):
        length = data[offset]
        offset += 1
        chunk = data[offset:offset + length].decode("utf-8", "replace")
        offset += length
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            out[key] = value
        elif chunk:
            out[chunk] = ""
    return out


def _read_packet(data: bytes, service: str, services: Dict[str, Service],
                 targets: Dict[str, str], late: Dict[str, Set[str]]) -> None:
    """Fold one response into `services`.

    `targets` maps a host name to its instance; `late` holds addresses that
    arrived before the SRV that names them (mDNS answers can be split).
    """
    if len(data) < 12:
        return
    _id, _flags, questions, answers, authority, additional = struct.unpack(">HHHHHH", data[:12])
    offset = 12
    for _ in range(questions):
        _name, offset = _read_name(data, offset)
        offset += 4                                   # QTYPE + QCLASS
    for _ in range(answers + authority + additional):
        if offset + 10 > len(data):
            return
        name, offset = _read_name(data, offset)
        name = _instance(name, service)
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlen]
        offset += rdlen

        if rtype == 12:                                # PTR: service -> instance
            instance, _ = _read_name(data, offset - rdlen)
            services.setdefault(_instance(instance, service), Service(name=_instance(instance, service)))
        elif rtype == 33 and len(rdata) >= 6:          # SRV: instance -> port + host
            _priority, _weight, port = struct.unpack(">HHH", rdata[:6])
            host, _ = _read_name(data, offset - rdlen + 6)
            entry = services.get(name) or Service(name=name)
            entry.port, entry.host = port, host
            services[name] = entry
            targets[host] = name
            if host in late:
                entry.addresses |= late.pop(host)
        elif rtype == 16:                              # TXT: what it says about itself
            entry = services.get(name) or Service(name=name)
            entry.txt.update(_txt(rdata))
            services[name] = entry
        elif rtype == 1 and rdlen == 4:                # A: host -> address
            instance = targets.get(name)
            if instance is not None:
                services[instance].addresses.add(socket.inet_ntoa(rdata))
            else:
                late.setdefault(name, set()).add(socket.inet_ntoa(rdata))


def browse(service: str, timeout: float = 2.0, destination: str = GROUP,
           destination_port: int = PORT, bind_port: int = PORT,
           bind_address: str = "") -> List[Service]:
    """What advertises `service` on this network, as `Service` objects.

    `bind_port=0` takes any free port, which is how a test answers on localhost;
    a real browse needs 5353, because mDNS responses are multicast to it.
    """
    resolved: Dict[str, Service] = {}
    targets: Dict[str, str] = {}
    late: Dict[str, Set[str]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind((bind_address, bind_port))
        if not bind_address and destination == GROUP:
            # Only a multicast destination needs the group joined; a test that
            # answers on localhost would fail to join it.
            membership = struct.pack("=4sl", socket.inet_aton(GROUP), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(0.5)
        sock.sendto(question(f"{service}.local"), (destination, destination_port))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _from = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            _read_packet(data, service, resolved, targets, late)
    finally:
        sock.close()
    return list(resolved.values())


def first(service: str, timeout: float = 2.0) -> Optional[Service]:
    """The first advertisement of `service` that has both an address and a port."""
    found = [s for s in browse(service, timeout=timeout) if s.address and s.port]
    if not found:
        return None
    return sorted(found, key=lambda s: (s.name, s.address))[0]