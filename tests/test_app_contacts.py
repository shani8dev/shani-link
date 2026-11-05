"""The Contacts tab, built on the private Broadway display and pressed like a person would.

Every contact goes to a temporary XDG_DATA_HOME; the phone's contacts are a
list handed in, as the Phone page hands them.
"""
import os
import stat

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from _gui import needs_display, pump, texts  # noqa: E402

pytestmark = needs_display


@pytest.fixture()
def view(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from gi.repository import Adw
    Adw.init()
    from shani_link import contacts as ct
    from shani_link.app.contacts_view import ContactsView
    ct.book().save(ct.Contact(given="Zoe", family="Adams", full_name="Zoe Adams",
                              phones=[("cell", "+919800000001")]))
    ct.book().save(ct.Contact(given="Amit", family="Zaveri", full_name="Amit Zaveri",
                              emails=[("work", "amit@work.example")]))
    calls, messages = [], []
    phone = [("Bank", ("18001234",)), ("Zoe Adams", ("09800000001",))]
    v = ContactsView(call=calls.append, message=messages.append, phone_book=lambda: phone)
    v.calls, v.messages = calls, messages
    return v


def _names(v):
    return [c.display_name() for c in v.shown()]


def test_the_list_is_alphabetical_with_letter_headings_and_the_phones_marked(view):
    assert _names(view) == ["Amit Zaveri", "Bank", "Zoe Adams"], "the phone's copy of Zoe is hidden"
    rows = sorted(view.rows.values(), key=lambda r: r.get_index())
    heads = [(r.contact.display_name(), r.get_header().get_label() if r.get_header() else None)
             for r in rows]
    assert heads == [("Amit Zaveri", "A"), ("Bank", "B"), ("Zoe Adams", "Z")], heads
    assert any("From your phone" in w for w in texts(view.list))
    view.sort.set_selected(1)                       # by last name
    assert _names(view) == ["Zoe Adams", "Bank", "Amit Zaveri"]


def test_search_finds_by_name_number_and_email(view):
    for query, expect in (("zoe", ["Zoe Adams"]), ("98000", ["Zoe Adams"]),
                          ("work.example", ["Amit Zaveri"]), ("1800", ["Bank"])):
        view.search.set_text(query)
        pump(lambda: _names(view) == expect, 1)
        assert _names(view) == expect, query
    view.search.set_text("nobody here")
    pump(lambda: view.empty.get_visible(), 1)
    assert "No contact matches" in view.empty.get_text()


def test_adding_a_contact_through_the_editor_writes_a_private_vcard(view):
    from shani_link import contacts as ct
    view.edit(None)
    view.f_given.set_text("Priya")
    view.f_family.set_text("Nair")
    view.f_phones and None
    view.f_bday.set_text("1992-02-30")
    assert view.save_editor() is None and "does not exist" in view.editor_note.get_text()
    view.f_bday.set_text("02-14")
    saved = view.save_editor()
    assert saved is not None and saved.birthday == "--02-14" and saved.full_name == "Priya Nair"
    path = ct.folder() / f"{saved.uid}.vcf"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert view.stack.get_visible_child_name() == "sheet"
    assert "Priya Nair" in _names(view)


def test_an_empty_contact_is_not_saved(view):
    view.edit(None)
    assert view.save_editor() is None
    assert "name, a number or an email" in view.editor_note.get_text()


def test_the_sheet_calls_and_messages_each_number(view):
    zoe = next(c for c in view.entries if c.display_name() == "Zoe Adams")
    view.open_sheet(zoe)
    from gi.repository import Gtk

    def buttons(w, out):
        if isinstance(w, Gtk.Button) and w.get_tooltip_text():
            out[w.get_tooltip_text()] = w
        child = w.get_first_child()
        while child is not None:
            buttons(child, out)
            child = child.get_next_sibling()
        return out
    found = buttons(view.sheet, {})
    found["Call +919800000001"].emit("clicked")
    found["Message +919800000001"].emit("clicked")
    assert view.calls == ["+919800000001"] and view.messages == ["+919800000001"]
    assert "Edit" in texts(view.sheet) and "Mobile" in texts(view.sheet)


def test_a_phone_contact_is_read_only_and_can_be_saved_as_your_own(view):
    bank = next(c for c in view.entries if c.display_name() == "Bank")
    view.open_sheet(bank)
    words = texts(view.sheet)
    assert "Edit" not in words and "Save to my contacts" in words
    view.save_phone_contact(bank)
    from shani_link import contacts as ct
    mine = [c for c in ct.book().all() if c.display_name() == "Bank"]
    assert len(mine) == 1 and mine[0].numbers() == ["18001234"]
    assert [c.source for c in view.entries if c.display_name() == "Bank"] == ["local"]


def test_select_several_delete_and_undo(view):
    from shani_link import contacts as ct
    view.set_selecting(True)
    for c in view.entries:
        if c.source == "local":
            view.rows[c.uid].check.set_active(True)
    assert view.select_count.get_text() == "2 contacts selected"
    view.delete_selected()
    assert ct.book().all() == [] and _names(view) == ["Bank", "Zoe Adams"]
    assert view.last_toast.get_title() == "Deleted 2 contacts"
    view.last_toast.emit("button-clicked")
    assert sorted(c.display_name() for c in ct.book().all()) == ["Amit Zaveri", "Zoe Adams"]
    assert _names(view) == ["Amit Zaveri", "Bank", "Zoe Adams"]


def test_import_one_file_or_many_and_export_the_selected(view, tmp_path):
    from shani_link import contacts as ct
    a = tmp_path / "a.vcf"
    a.write_text("BEGIN:VCARD\nVERSION:3.0\nFN:Chitra\nTEL:111222333\nEND:VCARD\n"
                 "BEGIN:VCARD\nVERSION:3.0\nFN:Dev\nEND:VCARD\n")
    b = tmp_path / "b.vcf"
    b.write_text("BEGIN:VCARD\nVERSION:4.0\nFN:Esha\nEMAIL:e@x.example\nEND:VCARD\n")
    assert view.import_paths([str(a), str(b)]) == 3
    assert view.last_toast.get_title() == "Imported 3 contacts"
    assert {"Chitra", "Dev", "Esha"} <= set(_names(view))
    out = tmp_path / "out.vcf"
    view.set_selecting(True)
    chitra = next(c for c in view.entries if c.display_name() == "Chitra")
    view.rows[chitra.uid].check.set_active(True)
    assert view.export_to(str(out), view.chosen())
    assert [c.display_name() for c in ct.parse(out.read_text())] == ["Chitra"]


def test_a_number_from_the_history_is_added_to_an_existing_contact(view):
    view.pick_for_number("+914444444444")
    assert view.pick_bar.get_visible() and "+914444444444" in view.pick_label.get_text()
    amit = view.rows[next(c.uid for c in view.entries if c.display_name() == "Amit Zaveri")]
    view.list.emit("row-activated", amit)
    assert view.stack.get_visible_child_name() == "edit"
    assert [e.get_text() for _d, e, _l in view.f_phones] == ["+914444444444"]
    saved = view.save_editor()
    assert saved.numbers() == ["+914444444444"] and saved.emails == [("work", "amit@work.example")]
    assert not view.pick_bar.get_visible()


def test_share_as_qr_draws_the_vcard_or_says_what_is_missing(view, tmp_path, monkeypatch):
    zoe = next(c for c in view.entries if c.display_name() == "Zoe Adams")
    fake = tmp_path / "bin"
    fake.mkdir()
    seen = tmp_path / "qr-input"
    tool = fake / "qrencode"
    png = tmp_path / "dot.png"
    from gi.repository import GdkPixbuf
    GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4).savev(str(png), "png", [], [])
    # a stand-in qrencode: records what it was asked to draw, prints a PNG
    tool.write_text(f"#!/bin/sh\nfor a; do last=\"$a\"; done\nprintf '%s' \"$last\" > {seen}\ncat {png}\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    view.share_qr(zoe)
    assert view.qr_png and view.stack.get_visible_child_name() == "qr"
    assert "FN:Zoe Adams" in seen.read_text() and "PHOTO" not in seen.read_text()
    monkeypatch.setenv("PATH", "/nonexistent")
    view.share_qr(zoe)
    assert view.qr_png is None and any("qrencode" in t for t in texts(view.qr))


def test_birthday_formats():
    from shani_link.app.contacts_view import birthday_from
    assert birthday_from("1990-1-31") == ("1990-01-31", "")
    assert birthday_from("--12-25") == ("--12-25", "")
    assert birthday_from("02-29") == ("--02-29", "")
    assert birthday_from("2023-02-29")[1] and birthday_from("yesterday")[1]
