"""Your own address book: the contacts kept on this computer, as vCard files.

What GNOME Contacts did, so ShaniOS does not need it: names (full, given,
family, nickname), phone numbers with their kind, email addresses, postal
addresses, a birthday, the organisation and the role there, websites, a note
and a photo. Each contact is one vCard 4.0 file (RFC 6350) in
`$XDG_DATA_HOME/shani-link/contacts/`, named by its UID.

**Why one file per contact, and why vCard.** vCard is what every phone, mail
program and address book exports and imports, so the folder *is* the export -
nothing to convert, nothing locked in. One file each means saving a contact
rewrites only that contact, and a crash mid-save can lose at most the edit in
flight: every write goes to a temporary file in the same folder, is synced,
and is renamed over the old one (`os.replace` is atomic on one filesystem).

**One lookup for every name.** `lookup(number)` is the single place a phone
number becomes a name: the incoming-call card, the call history, the messages
list and the landline all ask it. It looks in your own contacts first and then
in the phone's (the copy the Phone page caches when it reads them), so a name
you saved here wins over whatever the phone calls them.

Standard library only: vCard is a small line format and parsing it here keeps
the app free of an address-book service (Evolution Data Server) it never
otherwise needs.
"""
from __future__ import annotations

import base64
import binascii
import dataclasses
import os
import quopri
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from shani_link import paths

# --- what a contact holds ------------------------------------------------------

#: (vCard TYPE, what the person reads) for a phone number, in the order the
#: editor offers them. "cell" is vCard's word for a mobile.
PHONE_TYPES = (("cell", "Mobile"), ("home", "Home"), ("work", "Work"),
               ("fax", "Fax"), ("pager", "Pager"), ("other", "Other"))
#: The same for email and postal addresses.
PLACE_TYPES = (("home", "Home"), ("work", "Work"), ("other", "Other"))
#: The parts of a postal address, in vCard's ADR order after the P.O. box and
#: extended address (RFC 6350 6.3.1), with what the editor calls them.
ADDRESS_PARTS = (("street", "Street"), ("locality", "City"), ("region", "State or region"),
                 ("code", "Postal code"), ("country", "Country"))


@dataclasses.dataclass
class Contact:
    """One person (or company). Lists of (type, value) keep each value's kind."""
    uid: str = ""
    full_name: str = ""
    given: str = ""
    family: str = ""
    nickname: str = ""
    phones: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    emails: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    #: (type, {"street", "locality", "region", "code", "country"})
    addresses: List[Tuple[str, dict]] = dataclasses.field(default_factory=list)
    #: "YYYY-MM-DD", or "--MM-DD" when the year is not known
    birthday: str = ""
    org: str = ""
    role: str = ""
    urls: List[str] = dataclasses.field(default_factory=list)
    note: str = ""
    photo: bytes = b""
    photo_type: str = ""
    #: "local" for your own; "phone" for one read from the phone (read-only here)
    source: str = "local"

    def display_name(self) -> str:
        """What the list shows: the full name, else whatever names them at all."""
        joined = " ".join(p for p in (self.given, self.family) if p)
        for candidate in (self.full_name, joined, self.nickname, self.org):
            if candidate.strip():
                return candidate.strip()
        for _kind, value in self.emails + self.phones:
            if value.strip():
                return value.strip()
        return "Unnamed contact"

    def numbers(self) -> List[str]:
        return [v for _t, v in self.phones if v.strip()]

    def copy(self) -> "Contact":
        return dataclasses.replace(self, phones=list(self.phones), emails=list(self.emails),
                                   addresses=[(t, dict(a)) for t, a in self.addresses],
                                   urls=list(self.urls))


def from_phone(name: str, numbers: Sequence[str]) -> Contact:
    """A contact the phone has, in the shape the list shows (read-only here)."""
    return Contact(uid="phone:" + name, full_name=name,
                   phones=[("other", str(n)) for n in numbers if str(n).strip()], source="phone")


# --- vCard: reading -----------------------------------------------------------------

def _unfold(text: str) -> List[str]:
    """Physical lines into content lines (RFC 6350 3.2: CRLF + space or tab continues).

    vCard 2.1 quoted-printable values continue with a trailing "=" instead, which
    is undone here too, because phones still export 2.1.
    """
    raw = re.sub(r"\r\n|\r", "\n", text)
    raw = re.sub(r"\n[ \t]", "", raw)
    out: List[str] = []
    for line in raw.split("\n"):
        if out and out[-1].endswith("=") and "QUOTED-PRINTABLE" in out[-1].split(":", 1)[0].upper():
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
    return [ln for ln in out if ln.strip()]


def _split(value: str, sep: str) -> List[str]:
    """Split on `sep` where it is not escaped with a backslash."""
    parts, cur, i = [], [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            cur.append(value[i:i + 2])
            i += 2
            continue
        if ch == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\n", "\\n"))


def _parse_line(line: str) -> Optional[Tuple[str, dict, str]]:
    """(NAME, {PARAM: [values]}, raw value) for one content line, group dropped."""
    # The value starts at the first colon outside a quoted parameter value.
    quoted, cut = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            cut = i
            break
    if cut < 0:
        return None
    head, value = line[:cut], line[cut + 1:]
    pieces = _split(head, ";")
    name = pieces[0].split(".")[-1].strip().upper()
    params: dict = {}
    for piece in pieces[1:]:
        if "=" in piece:
            key, _, val = piece.partition("=")
            vals = [v.strip('"') for v in val.split(",")]
        else:                                   # vCard 2.1: a bare "HOME" is TYPE=HOME
            key, vals = "TYPE", [piece]
        params.setdefault(key.strip().upper(), []).extend(v.lower() for v in vals if v)
    return name, params, value


def _decode(params: dict, value: str) -> str:
    """The value as text, undoing 2.1 quoted-printable."""
    if "quoted-printable" in params.get("ENCODING", []):
        charset = (params.get("CHARSET") or ["utf-8"])[0]
        try:
            return quopri.decodestring(value.encode("latin-1", "replace")).decode(charset, "replace")
        except LookupError:
            return quopri.decodestring(value.encode("latin-1", "replace")).decode("utf-8", "replace")
    return value


def _kind(params: dict, allowed: Sequence[Tuple[str, str]], default: str = "other") -> str:
    """The first TYPE we know, mapping old spellings ("mobile", "cell", "voice")."""
    names = {k for k, _ in allowed}
    for t in params.get("TYPE", []):
        t = {"mobile": "cell", "iphone": "cell", "internet": "", "pref": "", "voice": ""}.get(t, t)
        if t in names:
            return t
    return default


def _photo(params: dict, value: str) -> Tuple[bytes, str]:
    """(bytes, mime type) from a data: URI (4.0) or base64 with ENCODING=b (3.0, 2.1)."""
    value = value.strip()
    try:
        if value.startswith("data:"):
            meta, _, payload = value[5:].partition(",")
            mime = meta.split(";")[0] or "image/jpeg"
            data = base64.b64decode(payload) if ";base64" in meta else payload.encode()
            return data, mime
        enc = params.get("ENCODING", [])
        if any(e in ("b", "base64") for e in enc):
            kind = (params.get("TYPE") or ["jpeg"])[0]
            mime = kind if "/" in kind else f"image/{kind}"
            return base64.b64decode(re.sub(r"\s", "", value)), mime
    except (binascii.Error, ValueError):
        pass
    return b"", ""                          # a photo by URL is not fetched: nothing here goes online


def _date(value: str) -> str:
    """RFC 6350 dates ("19900131", "--0131", "1990-01-31") as "YYYY-MM-DD" / "--MM-DD"."""
    v = value.strip().split("T")[0]
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.fullmatch(r"--(\d{2})-?(\d{2})", v)
    return f"--{m.group(1)}-{m.group(2)}" if m else ""


def parse(text: str) -> List[Contact]:
    """Every contact in `text`, one or many vCards (versions 2.1, 3.0 and 4.0)."""
    found: List[Contact] = []
    cur: Optional[Contact] = None
    for line in _unfold(text):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        name, params, raw = parsed
        if name == "BEGIN" and raw.strip().upper() == "VCARD":
            cur = Contact()
            continue
        if cur is None:
            continue
        if name == "END":
            if cur.display_name() != "Unnamed contact" or cur.phones or cur.emails:
                found.append(cur)
            cur = None
            continue
        value = _decode(params, raw)
        if name == "UID":
            cur.uid = re.sub(r"^urn:uuid:", "", value.strip(), flags=re.I)
        elif name == "FN":
            cur.full_name = _unescape(value).strip()
        elif name == "N":
            parts = [_unescape(p).strip() for p in _split(value, ";")] + [""] * 5
            cur.family, cur.given = parts[0], parts[1]
        elif name == "NICKNAME":
            cur.nickname = _unescape(_split(value, ",")[0]).strip()
        elif name == "TEL":
            number = re.sub(r"^tel:", "", _unescape(value).strip(), flags=re.I)
            if number:
                cur.phones.append((_kind(params, PHONE_TYPES), number))
        elif name == "EMAIL":
            address = re.sub(r"^mailto:", "", _unescape(value).strip(), flags=re.I)
            if address:
                cur.emails.append((_kind(params, PLACE_TYPES), address))
        elif name == "ADR":
            parts = [_unescape(p).strip() for p in _split(value, ";")] + [""] * 7
            street = ", ".join(p for p in (parts[0], parts[1], parts[2]) if p)
            adr = {"street": street, "locality": parts[3], "region": parts[4],
                   "code": parts[5], "country": parts[6]}
            if any(adr.values()):
                cur.addresses.append((_kind(params, PLACE_TYPES), adr))
        elif name == "BDAY":
            cur.birthday = _date(value)
        elif name == "ORG":
            cur.org = _unescape(_split(value, ";")[0]).strip()
        elif name in ("ROLE", "TITLE"):
            cur.role = cur.role or _unescape(value).strip()
        elif name == "URL":
            if value.strip():
                cur.urls.append(_unescape(value).strip())
        elif name == "NOTE":
            cur.note = _unescape(value)
        elif name == "PHOTO":
            cur.photo, cur.photo_type = _photo(params, raw)
    return found


# --- vCard: writing ------------------------------------------------------------------

def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next with a space (RFC 6350 3.2),
    never cutting a UTF-8 character in two."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 75:
            out.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    out.append(cur)
    return "\r\n".join(out)


def _bday(value: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value.replace("-", "")
    if re.fullmatch(r"--\d{2}-\d{2}", value):
        return "--" + value[2:].replace("-", "")
    return ""


def serialize(contact: Contact) -> str:
    """One contact as a vCard 4.0."""
    lines = ["BEGIN:VCARD", "VERSION:4.0", f"UID:urn:uuid:{contact.uid}" if contact.uid else ""]
    lines.append("FN:" + _escape(contact.display_name()))
    lines.append(f"N:{_escape(contact.family)};{_escape(contact.given)};;;")
    if contact.nickname:
        lines.append("NICKNAME:" + _escape(contact.nickname))
    for kind, number in contact.phones:
        if number.strip():
            lines.append(f"TEL;TYPE={kind}:{_escape(number.strip())}")
    for kind, address in contact.emails:
        if address.strip():
            lines.append(f"EMAIL;TYPE={kind}:{_escape(address.strip())}")
    for kind, adr in contact.addresses:
        fields = [adr.get(k, "") for k, _ in ADDRESS_PARTS]
        if any(f.strip() for f in fields):
            lines.append(f"ADR;TYPE={kind}:;;" + ";".join(_escape(f.strip()) for f in fields))
    if _bday(contact.birthday):
        lines.append("BDAY:" + _bday(contact.birthday))
    if contact.org:
        lines.append("ORG:" + _escape(contact.org))
    if contact.role:
        lines.append("ROLE:" + _escape(contact.role))
    for url in contact.urls:
        if url.strip():
            lines.append("URL:" + url.strip())
    if contact.note:
        lines.append("NOTE:" + _escape(contact.note))
    if contact.photo:
        mime = contact.photo_type or "image/jpeg"
        lines.append(f"PHOTO:data:{mime};base64," + base64.b64encode(contact.photo).decode())
    lines.append("REV:" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    lines.append("END:VCARD")
    return "".join(_fold(ln) + "\r\n" for ln in lines if ln)


def export(contacts: Iterable[Contact]) -> str:
    """Several contacts as one .vcf, which every address book imports."""
    return "".join(serialize(c) for c in contacts)


# --- searching and sorting ---------------------------------------------------------------

def fold_text(text: str) -> str:
    """Lowercase with accents removed, so "jose" finds "José"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def digits(number: str) -> str:
    return re.sub(r"\D", "", number or "")


def sort_key(contact: Contact, by: str = "first") -> Tuple[str, str]:
    """By first name ("first") or by family name ("last"), as GNOME Contacts offers."""
    shown = contact.display_name()
    if by == "last" and contact.family:
        return fold_text(contact.family + " " + contact.given), fold_text(shown)
    if by == "first" and contact.given:
        return fold_text(contact.given + " " + contact.family), fold_text(shown)
    return fold_text(shown), fold_text(shown)


def section(contact: Contact, by: str = "first") -> str:
    """The letter heading a contact sits under; "#" for names that start otherwise."""
    first = sort_key(contact, by)[0][:1].upper()
    return first if first.isalpha() else "#"


def matches(contact: Contact, query: str) -> bool:
    """A search hit on the name, nickname, organisation, an email, or a number.

    Digits match inside numbers whatever the formatting ("98765" finds
    "+91 98765 43210"); words match anywhere in the names.
    """
    q = fold_text(query.strip())
    if not q:
        return True
    names = fold_text(" ".join((contact.display_name(), contact.given, contact.family,
                                contact.nickname, contact.org)))
    if all(word in names for word in q.split()):
        return True
    if any(q in fold_text(address) for _k, address in contact.emails):
        return True
    wanted = digits(q)
    return len(wanted) >= 2 and len(wanted) >= len(q.replace(" ", "").replace("+", "")) - 2 and \
        any(wanted in digits(n) for n in contact.numbers())


#: The letters under each key of a phone keypad.
T9 = {ch: key for key, letters in {"2": "abc", "3": "def", "4": "ghi", "5": "jkl", "6": "mno",
                                   "7": "pqrs", "8": "tuv", "9": "wxyz"}.items() for ch in letters}


def t9(text: str) -> str:
    """A name as keypad digits: "Amma" -> "2662"."""
    return "".join(T9.get(ch, ch if ch.isdigit() else "") for ch in fold_text(text))


def t9_matches(contact: Contact, typed: str) -> bool:
    """Whether keypad digits `typed` spell the start of one of the contact's
    names, or appear in one of their numbers - the dial pad's suggestions."""
    typed = digits(typed)
    if len(typed) < 2:
        return False
    words = re.split(r"\s+", fold_text(contact.display_name())) + [fold_text(contact.nickname)]
    if any(w and t9(w).startswith(typed) for w in words):
        return True
    if t9(contact.display_name().replace(" ", "")).startswith(typed):
        return True
    return any(typed in digits(n) for n in contact.numbers())


def same_number(a: str, b: str) -> bool:
    """Equal phone numbers, tolerating a country code or trunk 0 on one side only."""
    from shani_link import phone as ph
    return ph.same_number(a, b)


# --- the book on disk -------------------------------------------------------------------

def folder() -> Path:
    return paths.data_home() / "shani-link" / "contacts"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write through a temporary file in the same folder, synced, then renamed in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".vcf", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)                # an address book is personal
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Book:
    """The contacts folder. Reads are cached until the folder changes, so the
    lookup on every ringing call or history row costs a `stat`, not a re-parse."""

    def __init__(self, where: Optional[Path] = None):
        self.where = Path(where) if where is not None else None
        self._lock = threading.Lock()
        self._cache: List[Contact] = []
        self._stamp: Optional[Tuple[str, int, int]] = None

    @property
    def path(self) -> Path:
        return self.where if self.where is not None else folder()

    def _file(self, uid: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", uid)
        return self.path / f"{safe}.vcf"

    def all(self) -> List[Contact]:
        where = self.path
        try:
            st = where.stat()
            stamp = (str(where), st.st_mtime_ns, len(os.listdir(where)))
        except OSError:
            return []
        with self._lock:
            if stamp != self._stamp:
                found = []
                for card in sorted(where.glob("*.vcf")):
                    try:
                        text = card.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for c in parse(text)[:1]:
                        c.uid = c.uid or card.stem
                        found.append(c)
                self._cache, self._stamp = found, stamp
            return [c.copy() for c in self._cache]

    def get(self, uid: str) -> Optional[Contact]:
        return next((c for c in self.all() if c.uid == uid), None)

    def save(self, contact: Contact) -> Contact:
        """Add or replace one contact; returns it with its UID."""
        saved = contact.copy()
        saved.source = "local"
        saved.uid = saved.uid if saved.uid and not saved.uid.startswith("phone:") else str(uuid.uuid4())
        _atomic_write(self._file(saved.uid), serialize(saved).encode("utf-8"))
        self._stamp = None
        return saved

    def delete(self, uids: Iterable[str]) -> List[Contact]:
        """Remove contacts; returns what was removed, so it can be put back (Undo)."""
        gone = []
        by_uid = {c.uid: c for c in self.all()}
        for uid in uids:
            contact = by_uid.get(uid)
            if contact is None:
                continue
            try:
                self._file(uid).unlink()
                gone.append(contact)
            except FileNotFoundError:
                pass
        self._stamp = None
        return gone

    def restore(self, contacts: Iterable[Contact]) -> None:
        """Put deleted contacts back exactly as they were (same UID)."""
        for c in contacts:
            self.save(c)

    def import_text(self, text: str) -> List[Contact]:
        """Save every contact in a .vcf. A UID already here is a new copy, not an
        overwrite: importing must never silently replace someone's edits."""
        have = {c.uid for c in self.all()}
        added = []
        for c in parse(text):
            if c.uid in have:
                c.uid = ""
            added.append(self.save(c))
        return added

    def import_file(self, path: str) -> List[Contact]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return self.import_text(fh.read())

    def lookup(self, number: str) -> Optional[Contact]:
        if not digits(number):
            return None
        return next((c for c in self.all() if any(same_number(number, n) for n in c.numbers())), None)


_BOOK = Book()


def book() -> Book:
    """The person's own address book (the folder resolved at each read, so a
    test or a sandbox moves it by setting XDG_DATA_HOME)."""
    return _BOOK


def phone_contacts() -> List[Tuple[str, Tuple[str, ...]]]:
    """The phone's contacts as last cached by the Phone page ([] when never read)."""
    try:
        from shani_link import landline
        return [(str(n), tuple(nums)) for n, nums in landline.history_contacts() if n and nums]
    except Exception:  # noqa: BLE001 - no cache is no names, never an error
        return []


def lookup(number: str, phone_book: Optional[Sequence[Tuple[str, Sequence[str]]]] = None
           ) -> Tuple[str, Optional[Contact]]:
    """(name, contact) for a phone number, or ("", None) when nobody has it.

    Your own contacts first, then the phone's: `phone_book` when the caller has
    it loaded, else the cached copy. A phone contact comes back marked
    `source="phone"`, so a screen can offer "Save to my contacts".
    """
    if not number or not digits(number):
        return "", None
    own = book().lookup(number)
    if own is not None:
        return own.display_name(), own
    for name, numbers in (phone_book if phone_book is not None else phone_contacts()):
        if any(same_number(number, n) for n in numbers):
            return str(name), from_phone(str(name), numbers)
    return "", None


def name_for(number: str, phone_book=None) -> str:
    return lookup(number, phone_book)[0]


def search(query: str, limit: int = 50, phone_book=None) -> List[Contact]:
    """Your contacts and the phone's that match `query`, yours first, by name.
    A phone contact with the same name and number as one of yours is left out."""
    own = sorted((c for c in book().all() if matches(c, query)), key=sort_key)
    mine = {(fold_text(c.display_name()), digits(n)[-9:]) for c in own for n in c.numbers()}
    theirs = []
    for name, numbers in (phone_book if phone_book is not None else phone_contacts()):
        c = from_phone(str(name), numbers)
        if any((fold_text(c.display_name()), digits(n)[-9:]) in mine for n in c.numbers()):
            continue
        if matches(c, query):
            theirs.append(c)
    theirs.sort(key=sort_key)
    found = own + theirs
    return found[:limit] if limit > 0 else found


def as_json(contact: Contact) -> dict:
    """A contact as D-Bus clients get it (the photo left out: it is big, and a
    client that wants it can read the vCard file)."""
    return {"uid": contact.uid, "name": contact.display_name(), "given": contact.given,
            "family": contact.family, "nickname": contact.nickname,
            "phones": [{"type": t, "number": n} for t, n in contact.phones],
            "emails": [{"type": t, "address": a} for t, a in contact.emails],
            "org": contact.org, "role": contact.role, "birthday": contact.birthday,
            "source": contact.source, "has_photo": bool(contact.photo)}
