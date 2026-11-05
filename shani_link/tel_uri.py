"""`tel:` links (RFC 3966), and the `sip:` ones that are really phone numbers.

A web page's "call us" link, a contact card in a mail program, a QR code: they
open `shani-link-app --dial URI` (data/applications/dev.shani.Link.Dial.desktop
registers us for x-scheme-handler/tel). The number lands on the dial pad and
**nothing is dialled until the person presses Call** - a link is somebody
else's text, and a link that places a call on its own is how premium-rate
numbers get rung.

RFC 3966 in brief:
- `tel:+1-201-555-0123` - a global number; "-", ".", "(" and ")" are only
  visual separators and are dropped.
- `tel:863-1234;phone-context=+1-914-555` - a local number; a context that is
  itself a global number prefix is put in front ("+19145558631234").
- `tel:7042;phone-context=example.com` - a local number in a domain's context
  (an office extension): dialled as it is, the domain cannot be dialled.
- `;ext=` is an extension, kept apart so the dial pad can say so; `;isub=` and
  any other parameter is ignored. Percent escapes are decoded first.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import NamedTuple

_VISUAL = re.compile(r"[-.()\s]")
_DIALABLE = re.compile(r"^\+?[0-9*#A-Da-d]+$")


class Target(NamedTuple):
    number: str          # what goes on the dial pad; "" when the link has none
    extension: str = ""  # RFC 3966 ;ext=, to be keyed in once the call answers
    problem: str = ""    # why there is no number, in words the person can act on


def _clean(text: str) -> str:
    return _VISUAL.sub("", text)


def parse(uri: str) -> Target:
    """The number in a tel: (or sip:/sips:) link, or why there is none."""
    raw = urllib.parse.unquote((uri or "").strip())
    scheme, colon, rest = raw.partition(":")
    scheme = scheme.lower()
    if not colon:
        # a bare number, as some programs pass one
        number = _clean(raw)
        return Target(number) if _DIALABLE.match(number) else Target("", problem="That is not a phone number.")
    if scheme in ("sip", "sips"):
        return _sip(rest)
    if scheme not in ("tel", "callto"):
        return Target("", problem=f"Shani Link cannot open {scheme}: links.")
    rest = rest.lstrip("/")                  # "tel://123" is wrong but common
    pieces = rest.split(";")
    number = _clean(pieces[0])
    params = {}
    for p in pieces[1:]:
        key, _, value = p.partition("=")
        params[key.strip().lower()] = value.strip()
    if not number or not _DIALABLE.match(number):
        return Target("", problem="That link has no phone number in it.")
    context = params.get("phone-context", "")
    if not number.startswith("+") and context.startswith("+"):
        prefix = _clean(context)
        if _DIALABLE.match(prefix):
            number = prefix + number
    ext = _clean(params.get("ext", ""))
    return Target(number, ext if ext.isdigit() else "")


def _sip(rest: str) -> Target:
    """A sip: link is a phone number only when it says so (`;user=phone`) or its
    user part is plainly one. Any other SIP address needs a SIP account, which
    the landline is not - it calls numbers through the JioFiber router."""
    user_part, at, host = rest.partition("@")
    if not at:
        return Target("", problem="That SIP link has no address.")
    params = host.lower().split(";")[1:]
    user = _clean(user_part.split(";")[0])
    if ("user=phone" in params or re.fullmatch(r"\+?\d{3,}", user)) and _DIALABLE.match(user):
        return Target(user)
    return Target("", problem="That is a SIP address, not a phone number. Shani Link calls phone "
                              "numbers; a SIP address needs a SIP app.")
