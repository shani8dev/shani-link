"""The address book: vCard in and out, the folder on disk, search, and the one
number-to-name lookup every screen uses. No GTK, no bus."""
import os

import pytest

from shani_link import contacts as ct


@pytest.fixture()
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return ct.book()


def _full():
    return ct.Contact(full_name="Asha Rao; Jr, \"A\"", given="Asha", family="Rao", nickname="Ash",
                      phones=[("cell", "+91 98765 43210"), ("work", "020-1234-5678")],
                      emails=[("home", "asha@example.org")],
                      addresses=[("home", {"street": "12 MG Road", "locality": "Pune",
                                           "region": "MH", "code": "411001", "country": "India"})],
                      birthday="1990-01-31", org="Shani; Ltd", role="Engineer",
                      urls=["https://example.org/asha"], note="Line one\nLine two, with commas",
                      photo=b"\x89PNG\r\n\x1a\nfake", photo_type="image/png")


def test_every_field_survives_a_round_trip_through_vcard_4():
    c = _full()
    c.uid = "1234"
    text = ct.serialize(c)
    assert text.startswith("BEGIN:VCARD\r\nVERSION:4.0\r\n")
    back = ct.parse(text)[0]
    for field in ("uid", "full_name", "given", "family", "nickname", "phones", "emails", "addresses",
                  "birthday", "org", "role", "urls", "note", "photo", "photo_type"):
        assert getattr(back, field) == getattr(c, field), field


def test_long_lines_fold_at_75_octets_without_splitting_a_character():
    c = ct.Contact(full_name="Ādítya " * 30)
    text = ct.serialize(c)
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    assert ct.parse(text)[0].full_name == c.full_name.strip()


def test_phones_export_2_1_and_3_0_and_both_are_read():
    v21 = ("BEGIN:VCARD\r\nVERSION:2.1\r\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=4B=C3=A4;M=C3=BCller\r\n"
           "FN:Kä Müller\r\nTEL;CELL;PREF:+4915112345678\r\nTEL;HOME:030 1234\r\nEND:VCARD\r\n")
    v30 = ("BEGIN:VCARD\nVERSION:3.0\nFN:Bob\nTEL;TYPE=WORK,VOICE:555-0100\n"
           "EMAIL;TYPE=INTERNET,WORK:bob@work.example\nBDAY:1985-07-04\n"
           "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQ\n SkZJRg==\nitem1.URL:https://bob.example\nEND:VCARD\n")
    first, second = ct.parse(v21 + v30)
    assert first.given == "Müller" and first.family == "Kä"
    assert first.phones == [("cell", "+4915112345678"), ("home", "030 1234")]
    assert second.phones == [("work", "555-0100")] and second.emails == [("work", "bob@work.example")]
    assert second.birthday == "1985-07-04" and second.urls == ["https://bob.example"]
    assert second.photo.startswith(b"\xff\xd8\xff") and second.photo_type == "image/jpeg"


def test_a_saved_contact_is_one_private_file_and_nothing_else(book):
    saved = book.save(_full())
    assert saved.uid
    files = os.listdir(ct.folder())
    assert files == [f"{saved.uid}.vcf"], files
    assert oct(os.stat(ct.folder() / files[0]).st_mode & 0o777) == "0o600"
    assert book.get(saved.uid).display_name() == _full().full_name


def test_a_save_that_fails_midway_leaves_the_old_contact_whole(book, monkeypatch):
    saved = book.save(_full())
    before = (ct.folder() / f"{saved.uid}.vcf").read_bytes()
    changed = saved.copy()
    changed.full_name = "Someone else"

    def broken(_fd):
        raise OSError("disk full")
    monkeypatch.setattr(ct.os, "fsync", broken)
    with pytest.raises(OSError):
        book.save(changed)
    assert (ct.folder() / f"{saved.uid}.vcf").read_bytes() == before
    assert os.listdir(ct.folder()) == [f"{saved.uid}.vcf"], "the temporary file was left behind"


def test_delete_hands_back_what_it_removed_and_undo_puts_it_back(book):
    a = book.save(ct.Contact(full_name="A", phones=[("cell", "1111111")]))
    b = book.save(ct.Contact(full_name="B", phones=[("cell", "2222222")]))
    gone = book.delete([a.uid, b.uid, "nobody"])
    assert {c.uid for c in gone} == {a.uid, b.uid} and book.all() == []
    book.restore(gone)
    assert sorted(c.uid for c in book.all()) == sorted([a.uid, b.uid])


def test_import_never_overwrites_a_contact_already_here(book):
    mine = book.save(ct.Contact(full_name="Mine", phones=[("cell", "1234567")]))
    text = ct.export([ct.Contact(uid=mine.uid, full_name="Theirs"), ct.Contact(full_name="New")])
    added = book.import_text(text)
    assert len(added) == 2 and all(c.uid != mine.uid for c in added)
    assert book.get(mine.uid).full_name == "Mine"
    assert len(book.all()) == 3


def test_export_of_several_is_one_file_every_book_reads(book):
    a = book.save(ct.Contact(full_name="A"))
    b = book.save(ct.Contact(full_name="B"))
    text = ct.export(book.all())
    assert text.count("BEGIN:VCARD") == 2
    assert {c.uid for c in ct.parse(text)} == {a.uid, b.uid}


def test_search_by_name_accent_email_and_number():
    c = _full()
    for q in ("asha", "ASH", "rao asha", "asha@exa", "98765", "+91 98765", "shani"):
        assert ct.matches(c, q), q
    assert ct.matches(ct.Contact(full_name="José Núñez"), "jose nunez")
    for q in ("bob", "11111", "zz"):
        assert not ct.matches(c, q), q


def test_sorting_by_first_or_last_name_and_section_letters():
    people = [ct.Contact(given="Zoe", family="Adams", full_name="Zoe Adams"),
              ct.Contact(given="Amit", family="Zaveri", full_name="Amit Zaveri"),
              ct.Contact(full_name="42 Club")]
    assert [c.given for c in sorted(people[:2], key=ct.sort_key)] == ["Amit", "Zoe"]
    assert [c.given for c in sorted(people[:2], key=lambda c: ct.sort_key(c, "last"))] == ["Zoe", "Amit"]
    assert ct.section(people[2]) == "#" and ct.section(people[0], "last") == "A"


def test_keypad_letters_find_a_contact():
    amma = ct.Contact(full_name="Amma Rao", phones=[("cell", "+919800011122")])
    assert ct.t9("Amma") == "2662"
    assert ct.t9_matches(amma, "266")        # A-M-M
    assert ct.t9_matches(amma, "726")        # R-A-O, the second word
    assert ct.t9_matches(amma, "00011")      # inside the number
    assert not ct.t9_matches(amma, "999")


def test_lookup_prefers_your_own_contact_over_the_phones(book):
    from shani_link import landline
    landline.cache_contacts([("Phone's name", ("+91 98000 00001",)), ("Only on phone", ("0222333444",))])
    book.save(ct.Contact(full_name="My name", phones=[("cell", "09800000001")]))
    name, contact = ct.lookup("+919800000001")
    assert name == "My name" and contact.source == "local"
    name, contact = ct.lookup("+91 222 333 444")
    assert name == "Only on phone" and contact.source == "phone"
    assert ct.lookup("12345") == ("", None)
    assert ct.lookup("") == ("", None)


def test_a_number_only_in_the_cached_phone_book_is_named_beside_a_callers_book(book):
    """The caller's loaded book does not hide the cached one: a number only the
    cache has is still named, not shown as "+91 98220 11111"."""
    from shani_link import landline
    landline.cache_contacts([("Amma", ("+91 98220 11111",))])
    assert ct.lookup("+91 98220 11111", [("Bank", ("18001234",))])[0] == "Amma"
    assert ct.name_for("09822011111", [("Bank", ("18001234",))]) == "Amma"
    assert ct.name_for("18001234", [("Bank", ("18001234",))]) == "Bank"


def test_the_phone_book_this_process_read_names_numbers_with_no_cache_file(book):
    """The app caches the phone's book through the service; with the service
    away nothing reaches the disk, and the book it read still names calls."""
    assert ct.name_for("+91 98220 11111") == ""
    ct.remember_phone_book([("Amma", ("+91 98220 11111",)), ("", ("1",)), ("Nobody", ())])
    assert ct.phone_contacts() == [("Amma", ("+91 98220 11111",))]
    name, contact = ct.lookup("+919822011111")
    assert name == "Amma" and contact.source == "phone"
    rows = [{"number": "+91 98220 11111", "name": ""}, {"number": "+911", "name": "Kept"},
            {"number": "", "name": ""}]
    assert [r["name"] for r in ct.named(rows)] == ["Amma", "Kept", ""]
    assert rows[0]["name"] == "", "named() copies, it does not edit the caller's rows"


def test_search_puts_yours_first_and_hides_the_phones_copy_of_the_same(book):
    book.save(ct.Contact(full_name="Amma", phones=[("cell", "+919800000001")]))
    found = ct.search("amma", phone_book=[("Amma", ("09800000001",)), ("Ammachi", ("1112223334",))])
    assert [(c.display_name(), c.source) for c in found] == [("Amma", "local"), ("Ammachi", "phone")]
