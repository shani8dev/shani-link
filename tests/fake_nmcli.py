#!/usr/bin/env python3
"""A stand-in `nmcli` for tethering: put a link named `nmcli` first on PATH.

State is a JSON file named by `FAKE_NMCLI_STATE`:

    {"connections": [{"name", "uuid", "type", "bdaddr", "bttype", "active"}],
     "phone_shares": true,     # false: `connection up` fails as NetworkManager does
     "calls": [...]}           # appended to on every call

Only the verbs `shani_link.tether` uses are answered, in nmcli's own shapes.
"""
import json
import os
import sys
import uuid as uuidlib


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def main(argv) -> int:
    path = os.environ["FAKE_NMCLI_STATE"]
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    state.setdefault("calls", []).append(argv)
    conns = state.setdefault("connections", [])
    out, rc = "", 0
    by_uuid = lambda u: next((c for c in conns if c["uuid"] == u), None)  # noqa: E731
    if argv == ["-t", "-f", "NAME,UUID,TYPE,ACTIVE", "connection", "show"]:
        out = "\n".join(":".join([esc(c["name"]), c["uuid"], c["type"], "yes" if c.get("active") else "no"])
                        for c in conns)
    elif argv[:2] == ["-g", "bluetooth.bdaddr,bluetooth.type"] and argv[2:5] == ["connection", "show", "uuid"]:
        c = by_uuid(argv[5])
        if c is None:
            out, rc = f"Error: {argv[5]} - no such connection profile.", 10
        else:
            out = f"{esc(c.get('bdaddr', ''))}\n{c.get('bttype', '')}"
    elif argv[:2] == ["connection", "add"]:
        opts = dict(zip(argv[2::2], argv[3::2]))
        new = {"name": opts["con-name"], "uuid": str(uuidlib.uuid4()), "type": opts["type"],
               "bdaddr": opts["bluetooth.bdaddr"], "bttype": opts["bt-type"], "active": False}
        conns.append(new)
        out = f"Connection '{new['name']}' ({new['uuid']}) successfully added."
    elif argv[:2] == ["connection", "up"] and argv[2] == "uuid":
        c = by_uuid(argv[3])
        if c is None:
            out, rc = "Error: unknown connection", 10
        elif not state.get("phone_shares", True):
            out, rc = ("Error: Connection activation failed: The Bluetooth connection failed or timed out\n"
                       "Hint: use 'journalctl -xe NM_CONNECTION=...' to get more details."), 4
        else:
            c["active"] = True
            out = "Connection successfully activated (D-Bus active path: /org/freedesktop/NetworkManager/ActiveConnection/7)"
    elif argv[:2] == ["connection", "down"] and argv[2] == "uuid":
        c = by_uuid(argv[3])
        if c is None or not c.get("active"):
            out, rc = "Error: no active connection provided.", 10
        else:
            c["active"] = False
            out = f"Connection '{c['name']}' successfully deactivated"
    else:
        out, rc = f"fake nmcli: {' '.join(argv)} is not faked", 2
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
