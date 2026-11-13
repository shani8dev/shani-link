"""The DNS-SD browser (`shani_link/mdns.py`), against a UDP responder of its own.

`adb mdns services` is empty on Debian's `android-tools` - the subcommand is
there, the backend is not - so discovery is ours. A test that needed a real
phone or a real multicast group would be no test at all, so the responder binds
localhost and answers the query itself; `browse()` takes the destination and the
bind port for exactly this.
"""
import socket
import struct
import threading

import pytest

from shani_link import mdns


def _name(name: str) -> bytes:
    return b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\x00"


def _rr(name: bytes, rtype: int, rdata: bytes, ttl: int = 120) -> bytes:
    return name + struct.pack(">HHIH", rtype, 1, ttl, len(rdata)) + rdata


def _txt_rdata(*pairs) -> bytes:
    """TXT RDATA: one length-prefixed `key=value` string per pair."""
    out = b""
    for key, value in pairs:
        chunk = f"{key}={value}".encode()
        out += bytes([len(chunk)]) + chunk
    return out


def _answer(instance: str, host: str, address: str, port: int, txt: bytes) -> bytes:
    service = _name("_adb-tls-connect._tcp.local")
    full = _name(f"{instance}._adb-tls-connect._tcp.local")
    return (struct.pack(">HHHHHH", 0, 0x8400, 0, 4, 0, 0)
            + _rr(service, 12, full)
            + _rr(full, 16, txt)
            + _rr(_name(host), 1, socket.inet_aton(address))
            + _rr(full, 33, struct.pack(">HHH", 0, 0, port) + _name(host)))


class Responder:
    """A UDP server that answers one query with one packet, then closes."""

    def __init__(self, payload: bytes):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(2)
        self.payload = payload
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.socket.getsockname()[1]

    def _run(self) -> None:
        try:
            _data, from_ = self.socket.recvfrom(4096)
            self.socket.sendto(self.payload, from_)
        except OSError:
            pass
        finally:
            self.socket.close()


def test_a_query_brings_back_the_instance_host_address_and_port():
    responder = Responder(_answer("adb-abc123", "Pixel.local", "192.168.1.20", 41000,
                                  _txt_rdata(("name", "Pixel_8"), ("v", "1"))))
    (service,) = mdns.browse("_adb-tls-connect._tcp", timeout=1.5,
                             destination="127.0.0.1", destination_port=responder.port,
                             bind_port=0, bind_address="127.0.0.1")
    assert service.name == "adb-abc123"          # the instance label, not the full name
    assert service.address == "192.168.1.20"
    assert service.port == 41000
    assert service.host == "Pixel.local"
    assert service.txt == {"name": "Pixel_8", "v": "1"}


def test_a_late_address_is_matched_to_the_instance():
    """mDNS answers can be split; an A record that arrives first is kept."""
    full = _name("adb-late._adb-tls-connect._tcp.local")
    only_address = _rr(_name("Pixel.local"), 1, socket.inet_aton("10.0.0.5"))
    only_srv = _rr(full, 33, struct.pack(">HHH", 0, 0, 5555) + _name("Pixel.local"))
    responder = Responder(struct.pack(">HHHHHH", 0, 0x8400, 0, 2, 0, 0)
                          + only_address + only_srv)
    (found,) = mdns.browse("_adb-tls-connect._tcp", timeout=1.5,
                           destination="127.0.0.1", destination_port=responder.port,
                           bind_port=0, bind_address="127.0.0.1")
    assert found.name == "adb-late" and found.address == "10.0.0.5" and found.port == 5555


def test_a_garbage_packet_does_not_take_the_browser_down():
    responder = Responder(b"\x00" * 4)
    assert mdns.browse("_adb-tls-connect._tcp", timeout=1.0,
                       destination="127.0.0.1", destination_port=responder.port,
                       bind_port=0, bind_address="127.0.0.1") == []


def test_nobody_answers_is_an_empty_list_not_an_error():
    # port 9 (discard) answers nothing; the browse must still return
    assert mdns.browse("_adb-tls-connect._tcp", timeout=0.5,
                       destination="127.0.0.1", destination_port=9,
                       bind_port=0, bind_address="127.0.0.1") == []


def test_the_pairing_and_connect_services_are_named_for_what_they_are():
    assert mdns.PAIRING == "_adb-tls-pairing._tcp"
    assert mdns.CONNECT == "_adb-tls-connect._tcp"