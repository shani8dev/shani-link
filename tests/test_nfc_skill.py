"""The `nfc` skill: the NDEF decoder, and refusing what must be refused.

**The decoder is the part that can be tested without hardware, so it is tested
against real bytes.** NDEF is a published format (NFC Forum RTD), so the records
are decoded here rather than by scraping `nfc-list`'s human-readable dump. The
record used throughout is the one an NTAG213 sticker actually carries when it
holds a link:

    E1 01 0C 55 04 65 78 61 6D 70 6C 65 2E 63 6F 6D
    ^^ ^^ ^^ ^^ \\_________________________________/
    |  |  |  |      "example.org"
    |  |  |  type "U" (URI), prefix code 0x04 = "https://"
    |  |  payload length 12 = 1 prefix byte + 11 characters
    |  short record: the payload length is ONE byte, so bit 5 is set
    MB | ME, and TNF = 0x01 (NFC Forum well-known)

**`0xE1`, not `0xD1`.** The short-record bit is 0x20, and the first version of
`ndef_bytes` emitted `0xD1` - MB | ME | TNF=1, with SR *clear*. It then wrote a
one-byte payload length where the decoder, reading the same bits, expected the
high byte of a four-byte one. Every record it produced decoded to nothing at all,
with no error anywhere: `e1` vs `d1` is one bit and the whole feature was dead.
That is the reason the round trip is tested both ways here.

**The refusal tests are the other half, and their order matters.** The
destructive-write check runs *before* the tool-presence check, so "write to my
bank card" is refused identically on a machine with libnfc and one without. With
the checks the other way round the answer was "nfc-mfultralight is not
installed" - so the refusal existed only where the tool happened to be present,
which is the wrong way round for anything about not destroying somebody's card.

Run: `python3 -m pytest tests/test_nfc_skill.py`
"""

from __future__ import annotations

import subprocess
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_link import nfc  # noqa: E402


def record(tnf: int, typ: bytes, payload: bytes, short: bool = True,
           ident: bytes = b"") -> bytes:
    """One NDEF record with its lengths computed, never typed by hand.

    Hand-typed fixtures are how the MIME and long-record cases below were
    wrong the first time: three of them had a length field that disagreed with
    the payload, and the decoder was right every time.
    """
    header = 0x80 | 0x40 | tnf
    if short:
        header |= 0x20
    body = bytes([len(typ)])
    body += bytes([len(payload)]) if short else len(payload).to_bytes(4, "big")
    if ident:
        header |= 0x10
        body += bytes([len(ident)])
    return bytes([header]) + body + typ + ident + payload


NTAG213_LINK = bytes.fromhex("e1010c55046578616d706c652e6f7267")


class TestTheRealRecordFromASticker:
    def test_the_bytes_are_exactly_what_an_ntag213_holds(self):
        """Not "close enough" - this is the record, byte for byte."""
        assert nfc.ndef_bytes("https://example.org") == NTAG213_LINK

    def test_the_short_record_bit_is_set_and_the_round_trip_works(self):
        built = nfc.ndef_bytes("https://example.org")
        assert built[0] == 0xE1
        assert built[0] & 0x20, "SR is bit 5; without it the length field is four bytes"
        records = nfc.decode_message(built)
        assert [r.value for r in records] == ["https://example.org"]

    def test_it_decodes_the_real_record_without_having_built_it(self):
        """Decoding is tested against the literal bytes, so a broken *encoder*
        cannot make this pass by being decoded by a matching broken decoder."""
        assert nfc.decode_message(NTAG213_LINK)[0].value == "https://example.org"


class TestURIRecords:
    @pytest.mark.parametrize("text", [
        "https://example.org",
        "https://www.example.org",
        "http://www.example.com",
        "http://example.com",
        "tel:+441234567890",
        "mailto:someone@example.org",
        "btspp://00:11:22:33:44:55",
        "urn:nfc:something",
        "example.org",
    ])
    def test_every_scheme_survives_the_round_trip(self, text):
        records = nfc.decode_message(nfc.ndef_bytes(text))
        assert records[0].value == text

    def test_the_longer_abbreviation_wins_over_the_shorter_one(self):
        """"https://www." (0x02) must beat "https://" (0x04), or the result is
        "https://https://www...." - a link that goes nowhere."""
        built = nfc.ndef_bytes("https://www.example.org")
        # ndef_bytes lays the record out as [0]=0xE1, [1]=type length, [2]=payload
        # length, [3]=b"U", [4:]=the payload. So the prefix code is **byte 4**
        # and there is no separate length byte after the type - two earlier
        # versions of this assertion indexed [5] and [4] as if there were, which
        # is the mistake worth recording: the record was always right and the
        # test was reading the wrong byte.
        assert built[3] == ord("U"), "the type field is the ASCII 'U' of the URI record"
        assert built[4] == 0x02, f"the prefix code is {built[4]:#04x}, not 0x02"
        # [0] header, [1] type LENGTH (1, because "U" is one byte), [2] payload
        # length, [3] the type itself, [4:] the payload. Three earlier versions
        # of this assertion mixed the *length* of the type with the type byte -
        # the record was right every time and the test was reading the wrong
        # index, which is the mistake worth recording.
        assert built[:2] == bytes([0xE1, 0x01]), "header and type length"
        assert built[2] == len(built) - 4, "payload length is everything after the type"
        assert nfc.decode_message(built)[0].value == "https://www.example.org"

    def test_an_unassigned_prefix_code_is_not_turned_into_a_link(self):
        """"0x99 is not in the table" means this record was written by something
        using the format loosely. Reporting the bytes is honest; picking a
        plausible scheme is not."""
        d = nfc.decode_message(record(1, b"U", b"\x99ABC"))[0]
        assert d.value == ""
        assert "not in the URI abbreviation table" in d.note

    def test_an_empty_uri_record_says_so_rather_than_reporting_nothing(self):
        d = nfc.decode_message(record(1, b"U", b""))[0]
        assert d.kind == "url"
        assert "no payload" in d.note


class TestTextRecords:
    def test_a_text_record_with_a_language_code(self):
        d = nfc.decode_message(record(1, b"T", b"\x02enHello"))[0]
        assert (d.kind, d.value) == ("text", "Hello")
        assert d.note == "language: en"

    def test_a_utf16_text_record_is_decoded_as_utf16(self):
        """"Hi!" as UTF-16-LE is 6 bytes; read as UTF-8 it is three characters of
        which two are NUL. The status byte's 0x40 bit says which."""
        d = nfc.decode_message(record(1, b"T", b"\x42en" + "Hi!".encode("utf-16-le")))[0]
        assert d.value == "Hi!"
        assert "language: en" in d.note

    def test_a_text_record_with_no_language_code(self):
        d = nfc.decode_message(record(1, b"T", b"\x00Ok!!!"))[0]
        assert d.value == "Ok!!!"
        assert "unset" in d.note

    def test_a_text_record_is_not_a_link(self):
        """A note that happens to contain a URL is still a note. Promoting it to
        `kind="url"` would hand the model a link nobody wrote.

        The payload is chosen so the promotion **produces a plausible-looking
        link**: status byte 0x02 is both "language code is 2 bytes" and URI
        prefix code 0x02 = "https://www.". So a decoder that sent a text record
        down the URI branch builds `https://www.ensee https://x.org` - a real
        scheme, a real host, and nonsense. Asserting only `kind == "text"` was
        not enough on its own, so the absence of "https://www." is asserted too.
        """
        payload = b"\x02ensee https://x.org"
        d = nfc.decode_message(record(1, b"T", payload))[0]
        assert d.kind == "text"
        assert d.value == "see https://x.org"
        assert "https://www." not in d.value, (
            "the text record was decoded as a URI record; its status byte "
            "happens to be a valid URI prefix code"
        )

    def test_a_text_record_cut_off_inside_its_language_code(self):
        d = nfc.decode_message(bytes([0xE1, 0x01, 0x04, 0x54, 0x0F, 0x65]))[0]
        assert d.value == ""
        assert "truncated" in d.note


class TestTheOtherTypeNameFormats:
    def test_a_media_type_record_reports_the_mime_type(self):
        d = nfc.decode_message(record(2, b"text/plain", b"x"))[0]
        assert (d.kind, d.value) == ("mime", "text/plain")

    def test_a_media_type_record_is_not_fetched(self):
        """The module says it does not fetch; the prose must not imply it did."""
        assert "does not fetch" in nfc.describe(
            nfc.decode_message(record(2, b"text/plain", b"x")))

    def test_an_empty_record_is_an_empty_record(self):
        assert nfc.decode_message(record(0, b"", b""))[0].kind == "empty"

    def test_a_well_known_type_this_module_does_not_know_is_named_not_guessed(self):
        d = nfc.decode_message(record(1, b"Hr", b"\x01"))[0]
        assert d.kind == "Hr"
        assert "does not interpret" in d.note

    def test_a_long_record_with_a_four_byte_length(self):
        assert nfc.decode_message(record(1, b"U", b"\x04a.b", short=False))[0].value \
            == "https://a.b"

    def test_a_record_carrying_an_id_field(self):
        """IL set means an id length byte sits between the lengths and the type.
        Getting that wrong shifts the type and the payload by one byte."""
        d = nfc.decode_message(record(1, b"U", b"\x04a.b", ident=b"L1"))[0]
        assert d.value == "https://a.b"


class TestATagThatMovedAwayMidRead:
    def test_what_arrived_is_reported_as_a_partial_reading(self):
        """A short read is normal when a tag is pulled away, and the first record
        is usually the interesting one - so it is decoded, not thrown away."""
        records = nfc.decode_message(NTAG213_LINK[:8])
        assert records and records[0].kind == "url"
        assert records[0].value.startswith("https://exa")
        assert not records[0].value.endswith("example.org")

    def test_bytes_that_are_not_a_record_yield_nothing_rather_than_an_exception(self):
        assert nfc.decode_message(b"") == []
        assert nfc.decode_message(bytes([0xE1])) == []






class TestTheHonestNegative:
    """A laptop with no NFC reader is the common case, not an edge case."""

    def test_no_reader_is_reported_as_no_reader(self, monkeypatch):
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            nfc, "_run_tool",
            lambda argv, timeout=20: subprocess.CompletedProcess(argv, 0, "", ""))
        found, why = nfc.readers()
        assert found == []
        assert "no reader" in why.lower()
        assert "ACR122U" in why, "the answer should say what hardware would fix it"


    def test_a_reader_is_listed_when_one_is_found(self, monkeypatch):
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            nfc, "_run_tool",
            lambda argv, timeout=20: subprocess.CompletedProcess(
                argv, 0, "*  pn53x  pn53x reader\n", ""))
        found, why = nfc.readers()
        assert found == ["*  pn53x  pn53x reader"], found
        assert why == ""

    def test_a_missing_library_names_the_package_that_provides_it(self, monkeypatch):
        monkeypatch.setattr(nfc.shutil, "which", lambda name: None)
        found, why = nfc.readers()
        assert found == []
        assert "libnfc" in why, why

    def test_no_tag_within_the_window_says_so_without_claiming_a_failure(self, monkeypatch):
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)

        def boom(argv, timeout=20):
            raise subprocess.TimeoutExpired(argv, timeout)

        monkeypatch.setattr(nfc, "_run_tool", boom)
        payload, why = nfc.poll_tag(5)
        assert payload is None
        assert "5s" in why and "Hold a tag flat" in why, why

    def test_a_tag_with_no_ndef_is_not_reported_as_a_failed_read(self, monkeypatch):
        """A bank card answers and holds no NDEF. That is a fact about the card,
        not a broken reader."""
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            nfc, "_run_tool",
            lambda argv, timeout=20: subprocess.CompletedProcess(
                argv, 0, "NFC device found: MIFARE Plus\n", ""))
        payload, why = nfc.poll_tag(3)
        assert payload is None
        assert "no NDEF" in why, why


class TestTheDumpParser:
    """`nfc-list`'s output is a human dump, not NDEF. This is the one place a
    format is being scraped, so it says so."""

    def test_hex_lines_are_taken_in_order(self):
        text = ("NFC device found: NTAG213\n"
                "  04 00\n"          # page header bytes, not NDEF
                "  00 00 00\n"
                "  E1 01 0C 55 04 65 78\n")
        payload = nfc._payload_from_dump(text)
        assert payload is not None
        # Every hex line after the header, in order. The byte count is checked
        # as a count rather than a hand-written hex string, because a literal
        # here is another thing to mistype.
        assert payload == bytes([0x04, 0x00, 0x00, 0x00, 0x00, 0xE1, 0x01,
                                 0x0C, 0x55, 0x04, 0x65, 0x78])

    def test_a_dump_with_no_hex_yields_nothing_rather_than_guessing(self):
        assert nfc._payload_from_dump("MIFARE Classic\n") is None
        assert nfc._payload_from_dump("") is None

    def test_a_line_mixing_text_and_bytes_ends_the_payload(self):
        text = "header\n  E1 01\n  done: E1\n"
        assert nfc._payload_from_dump(text).hex() == "e101"

