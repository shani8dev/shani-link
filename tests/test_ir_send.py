"""Sending infrared, against fakes: a /dev/lirc* node that answers
LIRC_GET_FEATURES, and fake `irsend` and `ir-ctl` on PATH that record argv.

No real device is opened: the node is a file in tmp, and the ioctl is the
fake's (it checks the request number, which is what a real driver would see).
"""
import struct

import pytest

from shani_link import ir_remote as I

CAN_SEND_PULSE = 0x00000002
CAN_REC_MODE2 = 0x00040000


def _stub(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


@pytest.fixture
def ir(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    log.mkdir()
    node = tmp_path / "lirc0"
    node.write_bytes(b"")
    state = {"mask": CAN_SEND_PULSE, "asked": []}

    def ioctl(fd, request, arg):
        state["asked"].append(request)
        assert request == 0x80046900, "LIRC_GET_FEATURES"
        return struct.pack("I", state["mask"])
    monkeypatch.setattr(I.fcntl, "ioctl", ioctl)
    monkeypatch.setattr(I, "lirc_devices", lambda: [str(node)])
    # lirc 0.10: lists on stdout; a SEND_ONCE is recorded
    _stub(bindir, "irsend", f'''printf '%s|' "$@" >> {log}/irsend.args; echo >> {log}/irsend.args
case "$1 $2" in
  "LIST ") printf 'tv\\nsoundbar\\n';;
  "LIST tv") printf '0000000000000001 KEY_POWER\\n0000000000000002 KEY_VOLUMEUP\\n';;
  "LIST soundbar") printf '0000000000000010 KEY_MUTE\\n';;
  "SEND_ONCE "*) ;;
  *) echo "irsend: unknown remote: \\"$2\\"" >&2; exit 1;;
esac''')
    _stub(bindir, "ir-ctl", f'''printf '%s\\n' "$@" > {log}/ir-ctl.args
for a in "$@"; do case "$a" in --send=*) /bin/cat "${{a#--send=}}" > {log}/ir-ctl.sent;; esac; done''')
    monkeypatch.setenv("PATH", str(bindir))
    return {"log": log, "node": str(node), "state": state, "bin": bindir}


def _irsend_calls(log):
    return [line.rstrip("|").split("|") for line in (log / "irsend.args").read_text().splitlines()]


def test_a_transmitter_is_a_lirc_node_that_can_send(ir):
    assert I.transmitters() == ([ir["node"]], "")
    ir["state"]["mask"] = CAN_REC_MODE2
    found, why = I.transmitters()
    assert found == [] and "can only receive" in why


def test_remotes_and_buttons_are_lircds_own_lists(ir):
    assert I.remotes() == (["tv", "soundbar"], "")
    assert I.keys("tv") == (["KEY_POWER", "KEY_VOLUMEUP"], "")
    names, why = I.keys("radio")
    assert names == [] and "does not know a remote called 'radio'" in why


def test_send_key_is_irsend_send_once(ir):
    assert I._run({"action": "send-key", "remote": "tv", "key": "KEY_POWER"}) == "Sent KEY_POWER of tv."
    assert _irsend_calls(ir["log"])[-1] == ["SEND_ONCE", "tv", "KEY_POWER"]


def test_a_button_lircd_did_not_list_is_never_sent(ir):
    said = I.send_key("tv", "KEY_MUTE; rm -rf ~")
    assert "has no button called" in said
    assert all(call[0] == "LIST" for call in _irsend_calls(ir["log"])), "nothing was sent"


def test_no_transmitter_sends_nothing(ir):
    ir["state"]["mask"] = CAN_REC_MODE2
    said = I.send_key("tv", "KEY_POWER")
    assert said.startswith("Nothing was sent:") and "can only receive" in said
    assert not (ir["log"] / "irsend.args").exists()
    assert I.send_abilities()["send"] and I.send_abilities()["send-code"]


def test_lirc_09_lists_on_stderr_behind_a_prefix(ir):
    _stub(ir["bin"], "irsend", '''case "$1 $2" in
  "LIST ") printf 'irsend: tv\\n' >&2;;
  "LIST tv") printf 'irsend: 0000000000000001 KEY_POWER\\n' >&2;;
esac''')
    assert I.remotes() == (["tv"], "")
    assert I.keys("tv") == (["KEY_POWER"], "")


def test_lircd_not_running_is_said(ir):
    _stub(ir["bin"], "irsend", 'echo "irsend: could not connect to socket" >&2; '
                               'echo "irsend: Connection refused" >&2; exit 1')
    names, why = I.remotes()
    assert names == [] and "lircd is not running" in why
    assert I.send_abilities()["send"] == why


def test_a_raw_code_goes_through_ir_ctl(ir):
    said = I._run({"action": "send", "protocol": "nec", "address": "4", "command": "8"})
    assert said == f"Sent a NEC code (address 4, button 8) through {ir['node']}."
    args = (ir["log"] / "ir-ctl.args").read_text().split("\n")[:-1]
    assert args[:2] == ["-d", ir["node"]] and args[2].startswith("--send=")
    sent = (ir["log"] / "ir-ctl.sent").read_text()
    assert sent == I.to_irctl_file(I.nec_encode(4, 8), I.CARRIER_NEC)
    assert I.send_abilities() == {"send": "", "send-code": ""}


def test_ir_ctl_refusing_is_not_sent(ir):
    _stub(ir["bin"], "ir-ctl", 'echo "ir-ctl: /dev/lirc0: device cannot send" >&2; exit 1')
    said = I._run({"action": "send", "protocol": "nec", "address": "4", "command": "8"})
    assert "device cannot send" in said and said.endswith("Nothing was sent.")
