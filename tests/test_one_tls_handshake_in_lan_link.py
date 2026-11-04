"""`lan_link.py` drove the KDE Connect handshake twice, and one copy was dead.

An earlier version carried `identity_exchange()` - **119 lines** of hand-written
TCP + TLS + identity exchange, driving TLS over a `ssl.MemoryBIO` exactly as
`Link.connect()` does, both with `server_side=True`. It had **no caller and no
test**: the only mention anywhere was prose in a retired test file.

It was removed rather than given a caller, and this file holds that line.

## Why removal, not wiring

The one thing that made it look like a missing capability is in its docstring:
*"the capability list the phone advertises is real and is returned by
`identity_exchange`."* That is true — and irrelevant, because **discovery already
carries the capability list, with no handshake at all.**

The official KDE Connect field order puts `incomingCapabilities` and
`outgoingCapabilities` in the **plaintext** identity, `identity_packet` emits
them, and `parse_identity` returns them. `tests/test_lan_link.py` asserts
`"kdeconnect.battery" in peer["incomingCapabilities"]` on a discovery datagram.
So the advertised capabilities were always one `rglob` away from being readable.

And `Link` subsumes the rest. It keeps the peer's **whole** identity body in
`peer_identity` (an earlier reading of `connect()` suggested it kept only
`deviceId` — it does not), and it carries the `expect_peer` pinning defence that
the standalone copy had no way to express:

> "every later connection must match the pinned fingerprint exactly; on mismatch,
> hard-fail"

## Why two copies were the real hazard

This module's own docstring says the handshake's non-obvious parts were **"got
wrong first"** — the roles are inverted from the obvious reading, and using
`wrap_socket` instead of a memory BIO consumes the ClientHello. That knowledge is
expensive to acquire and trivially cheap to lose. Two hand-written copies drift:
one gets fixed, the other does not, and the broken one is the one nothing calls.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_link import lan_link  # noqa: E402

SOURCE = ROOT / "usr/lib/shani-chronoa/shani_chronoa/lan_link.py"


def _bio_sites():
    """Every function that constructs a `ssl.MemoryBIO` - i.e. hand-drives TLS.

    **Module level and methods, kept apart.** `Link.connect` is a method, and
    treating it as a public *module* function made the first version of this test
    fail against correct code: `connect` does not start with an underscore, but it
    is not something a caller reaches without constructing a `Link` first.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    module_level = [n for n in tree.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def builds(node):
        return any(isinstance(x, ast.Call)
                   and getattr(getattr(x, "func", None), "attr", "") == "MemoryBIO"
                   for x in ast.walk(node))

    return [("function", n.name) for n in module_level if builds(n)] + \
           [("method", f"{c.name}.{m.name}")
            for c in tree.body if isinstance(c, ast.ClassDef)
            for m in c.body
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and builds(m)]










