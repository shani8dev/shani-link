"""The Contacts tab: your own address book, and the phone's contacts beside it.

What GNOME Contacts did, so ShaniOS ships without it:

- one alphabetical list with a letter heading for each section and a round
  avatar (the photo, or the initials) on every row;
- search by name, number or email, and sort by first or last name;
- add, edit and delete - several at once in selection mode - with Undo on the
  toast, because a delete nobody can take back is a delete people fear to use;
- import one or many .vcf files, and export the ones selected (or all);
- share one contact as a QR code another phone's camera can read;
- the phone's contacts in the same list, marked "From your phone": they are
  read-only here (the phone owns them), with "Save to my contacts" to keep a
  copy that is yours.

The book itself is `shani_link.contacts` (vCard files); this is only its
screen. Reading the folder is fast (a few files, parsed once and cached), so
it runs on the main thread; the phone's contacts arrive from the Phone page
when it has read them.

**One widget, four views** (a `Gtk.Stack`): the list, a contact's sheet, the
editor and the QR code. A stack rather than dialogs, so the tab works the
same in a narrow window and every view can be checked by a test.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from shani_link import contacts as ct  # noqa: E402
from shani_link.app import common  # noqa: E402

logger = logging.getLogger(__name__)

SORTS = (("first", "First name"), ("last", "Last name"))
FROM_PHONE = "From your phone"


def _avatar(contact: ct.Contact, size: int) -> Adw.Avatar:
    """The photo when there is one, else the initials on a colour of their own."""
    avatar = Adw.Avatar(size=size, text=contact.display_name(), show_initials=True)
    if contact.photo:
        try:
            avatar.set_custom_image(Gdk.Texture.new_from_bytes(GLib.Bytes.new(contact.photo)))
        except GLib.Error:
            pass                    # a photo GTK cannot read: the initials stay
    return avatar


def _label(text: str, *css: str, wrap: bool = True) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=0.0, wrap=wrap)
    for c in css:
        lab.add_css_class(c)
    return lab


def _icon_button(icon: str, tip: str, on_click: Callable[[], None], *css: str) -> Gtk.Button:
    btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
    btn.update_property([Gtk.AccessibleProperty.LABEL], [tip])
    for c in css or ("flat",):
        btn.add_css_class(c)
    btn.connect("clicked", lambda _b: on_click())
    return btn


def _text_button(label: str, on_click: Callable[[], None], *css: str) -> Gtk.Button:
    btn = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    for c in css:
        btn.add_css_class(c)
    btn.connect("clicked", lambda _b: on_click())
    return btn


def _type_names(kinds) -> List[str]:
    return [name for _k, name in kinds]


def _dropdown(kinds, current: str) -> Gtk.DropDown:
    keys = [k for k, _n in kinds]
    dd = Gtk.DropDown.new_from_strings(_type_names(kinds))
    dd.set_selected(keys.index(current) if current in keys else len(keys) - 1)
    dd.set_valign(Gtk.Align.CENTER)
    return dd


def _kind_of(kinds, dropdown: Gtk.DropDown) -> str:
    return kinds[min(dropdown.get_selected(), len(kinds) - 1)][0]


def _kind_name(kinds, key: str) -> str:
    return dict(kinds).get(key, "Other")


def birthday_from(text: str) -> "tuple[str, str]":
    """(stored value, problem) for what was typed: "1990-01-31", "31-01"... A
    year is optional, as in GNOME Contacts; "" is no birthday."""
    import re
    t = text.strip()
    if not t:
        return "", ""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(?:--)?(\d{1,2})-(\d{1,2})", t)
        if not m:
            return "", "Type the birthday as year-month-day (1990-01-31), or month-day (01-31)."
        y, mo, d = 0, int(m.group(1)), int(m.group(2))
    import calendar
    if not 1 <= mo <= 12 or not 1 <= d <= calendar.monthrange(y or 2000, mo)[1]:
        return "", "That date does not exist."
    return (f"{y:04d}-{mo:02d}-{d:02d}" if y else f"--{mo:02d}-{d:02d}"), ""


class ContactsView:
    """Build with `ContactsView(call=..., message=..., phone_book=...)`; the
    widget is `.widget`. `call(number)` and `message(number)` are the Phone
    page's; `phone_book()` returns the phone's contacts as it last read them."""

    def __init__(self, call: Callable[[str], None] = lambda n: None,
                 message: Callable[[str], None] = lambda n: None,
                 phone_book: Callable[[], list] = lambda: []):
        self.call, self.message, self.phone_book = call, message, phone_book
        self.sort_by = "first"
        self.entries: List[ct.Contact] = []
        self.rows: Dict[str, Gtk.ListBoxRow] = {}
        self.selected: set = set()
        self.selecting = False
        self.pick_number = ""             # adding a number to an existing contact
        self.current: Optional[ct.Contact] = None
        self.undo_stack: List[ct.Contact] = []
        self.last_toast: Optional[Adw.Toast] = None
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.toasts.set_child(self.stack)
        self.stack.add_named(self._list_view(), "list")
        self.sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.stack.add_named(self.sheet, "sheet")
        self.editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.stack.add_named(self.editor, "edit")
        self.qr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.stack.add_named(self.qr, "qr")
        self.widget = self.toasts
        self.refresh()

    # --- the list -----------------------------------------------------------------

    def _list_view(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bar = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text="Search by name, number or email", hexpand=True)
        self.search.connect("search-changed", lambda _e: self._refilter())
        bar.append(self.search)
        self.sort = Gtk.DropDown.new_from_strings([n for _k, n in SORTS])
        self.sort.set_tooltip_text("Sort by")
        self.sort.connect("notify::selected", lambda *_: self.set_sort(SORTS[self.sort.get_selected()][0]))
        bar.append(self.sort)
        bar.append(_icon_button("list-add-symbolic", "Add a contact", lambda: self.edit(None)))
        self.select_button = Gtk.ToggleButton(icon_name="selection-mode-symbolic",
                                              tooltip_text="Select contacts", valign=Gtk.Align.CENTER)
        self.select_button.add_css_class("flat")
        self.select_button.update_property([Gtk.AccessibleProperty.LABEL], ["Select contacts"])
        self.select_button.connect("toggled", lambda b: self.set_selecting(b.get_active()))
        bar.append(self.select_button)
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, act in (("Import from a file...", self.pick_import),
                           ("Export all...", lambda: self.pick_export(None))):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, a=act: (self.more.popdown(), a()))
            menu.append(b)
        self.more = Gtk.Popover(child=menu)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", popover=self.more,
                              tooltip_text="Import and export", valign=Gtk.Align.CENTER)
        more.add_css_class("flat")
        bar.append(more)
        box.append(bar)

        # Choosing a contact to add a number to (from the call history).
        self.pick_bar = Gtk.Box(spacing=8)
        self.pick_bar.add_css_class("card")
        self.pick_label = _label("", wrap=True)
        self.pick_label.set_hexpand(True)
        self.pick_label.set_margin_start(10)
        self.pick_bar.append(self.pick_label)
        self.pick_bar.append(_text_button("Cancel", lambda: self.pick_for_number(""), "flat"))
        self.pick_bar.set_visible(False)
        box.append(self.pick_bar)

        # Selection mode's own bar: how many, and what to do with them.
        self.select_bar = Gtk.Box(spacing=8)
        self.select_count = _label("Choose contacts", "dim-label", wrap=False)
        self.select_count.set_hexpand(True)
        self.select_bar.append(self.select_count)
        self.export_selected = _text_button("Export", lambda: self.pick_export(self.chosen()))
        self.delete_selected_button = _text_button("Delete", self.delete_selected, "destructive-action")
        self.select_bar.append(self.export_selected)
        self.select_bar.append(self.delete_selected_button)
        self.select_bar.set_visible(False)
        box.append(self.select_bar)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.set_filter_func(self._visible)
        self.list.set_sort_func(self._order)
        self.list.set_header_func(self._header)
        self.list.connect("row-activated", lambda _l, row: self._activated(row))
        box.append(self.list)
        self.empty = _label("", "dim-label")
        self.empty.set_xalign(0.5)
        box.append(self.empty)
        return box

    def refresh(self) -> None:
        """Read the book (and the phone's list) again and rebuild the rows."""
        self.entries = ct.search("", limit=0, phone_book=self.phone_book() or [])
        common.clear(self.list)
        self.rows = {}
        for c in self.entries:
            row = self._row(c)
            self.rows[c.uid] = row
            self.list.append(row)
        self.selected &= set(self.rows)
        self._refilter()
        self._update_select_bar()

    def _row(self, c: ct.Contact) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=True)
        row.contact = c
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER, visible=self.selecting)
        check.set_active(c.uid in self.selected)
        check.connect("toggled", lambda b, uid=c.uid: self._toggle(uid, b.get_active()))
        check.update_property([Gtk.AccessibleProperty.LABEL], [f"Select {c.display_name()}"])
        row.check = check
        box.append(check)
        box.append(_avatar(c, 36))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(_label(c.display_name(), wrap=False))
        detail = (c.numbers()[:1] or [a for _k, a in c.emails][:1] or [""])[0]
        if c.source == "phone":
            detail = f"{detail} · {FROM_PHONE}" if detail else FROM_PHONE
        if detail:
            text.append(_label(detail, "dim-label", "caption", wrap=False))
        box.append(text)
        row.set_child(box)
        row.update_property([Gtk.AccessibleProperty.LABEL], [c.display_name()])
        return row

    def _visible(self, row: Gtk.ListBoxRow) -> bool:
        return ct.matches(row.contact, self.search.get_text())

    def _order(self, a: Gtk.ListBoxRow, b: Gtk.ListBoxRow) -> int:
        ka, kb = ct.sort_key(a.contact, self.sort_by), ct.sort_key(b.contact, self.sort_by)
        # yours before the phone's when the names are equal
        ka, kb = ka + (a.contact.source,), kb + (b.contact.source,)
        return (ka > kb) - (ka < kb)

    def _header(self, row: Gtk.ListBoxRow, before: Optional[Gtk.ListBoxRow]) -> None:
        letter = ct.section(row.contact, self.sort_by)
        if before is not None and ct.section(before.contact, self.sort_by) == letter:
            row.set_header(None)
            return
        head = _label(letter, "heading")
        head.set_margin_top(10)
        head.set_margin_start(12)
        head.set_margin_bottom(4)
        row.set_header(head)

    def _refilter(self) -> None:
        self.list.invalidate_filter()
        self.list.invalidate_headers()
        shown = self.shown()
        query = self.search.get_text().strip()
        if not self.entries:
            self.empty.set_text("No contacts yet. Add one, import a .vcf file, or connect your "
                                "phone to see its contacts here.")
        elif not shown:
            self.empty.set_text(f"No contact matches “{query}”.")
        else:
            self.empty.set_text("")
        self.empty.set_visible(bool(self.empty.get_text()))
        self.list.set_visible(bool(shown))

    def shown(self) -> List[ct.Contact]:
        """The contacts the list shows now, in its order (searched and sorted)."""
        hits = [c for c in self.entries if ct.matches(c, self.search.get_text())]
        return sorted(hits, key=lambda c: ct.sort_key(c, self.sort_by) + (c.source,))

    def set_sort(self, by: str) -> None:
        self.sort_by = by
        self.list.invalidate_sort()
        self.list.invalidate_headers()

    def _activated(self, row: Gtk.ListBoxRow) -> None:
        c = row.contact
        if self.selecting:
            row.check.set_active(not row.check.get_active())
        elif self.pick_number:
            self.add_number_to(c)
        else:
            self.open_sheet(c)

    # --- selecting several ----------------------------------------------------------

    def set_selecting(self, on: bool) -> None:
        self.selecting = on
        if self.select_button.get_active() != on:
            self.select_button.set_active(on)
        if not on:
            self.selected.clear()
        for row in self.rows.values():
            row.check.set_visible(on)
            row.check.set_active(row.contact.uid in self.selected)
        self._update_select_bar()

    def _toggle(self, uid: str, on: bool) -> None:
        (self.selected.add if on else self.selected.discard)(uid)
        self._update_select_bar()

    def chosen(self) -> List[ct.Contact]:
        return [c for c in self.entries if c.uid in self.selected]

    def _update_select_bar(self) -> None:
        self.select_bar.set_visible(self.selecting)
        n = len(self.selected)
        own = [c for c in self.chosen() if c.source == "local"]
        self.select_count.set_text(common.plural(n, "contact") + " selected" if n else "Choose contacts")
        self.export_selected.set_sensitive(n > 0)
        # the phone's contacts are the phone's: deleting is for your own
        self.delete_selected_button.set_sensitive(bool(own))

    def delete_selected(self) -> None:
        self.delete([c.uid for c in self.chosen() if c.source == "local"])
        self.set_selecting(False)

    def delete(self, uids: List[str]) -> None:
        """Delete your contacts, with Undo on the toast."""
        gone = ct.book().delete(uids)
        if not gone:
            return
        self.undo_stack = gone
        self.refresh()
        self.stack.set_visible_child_name("list")
        title = (f"Deleted {gone[0].display_name()}" if len(gone) == 1
                 else f"Deleted {common.plural(len(gone), 'contact')}")
        self.toast(title, "Undo", self.undo)

    def undo(self) -> None:
        if self.undo_stack:
            ct.book().restore(self.undo_stack)
            self.undo_stack = []
            self.refresh()

    def toast(self, title: str, button: str = "", on_button: Optional[Callable[[], None]] = None) -> None:
        t = Adw.Toast(title=title, timeout=8 if button else 4)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: on_button())
        self.last_toast = t
        self.toasts.add_toast(t)

    # --- a contact's sheet ---------------------------------------------------------

    def open_sheet(self, c: ct.Contact) -> None:
        self.current = c
        common.clear(self.sheet)
        top = Gtk.Box(spacing=6)
        top.append(_icon_button("go-previous-symbolic", "Back to the list", self.back))
        top.append(Gtk.Box(hexpand=True))
        if c.source == "local":
            top.append(_text_button("Edit", lambda: self.edit(c)))
        else:
            top.append(_text_button("Save to my contacts", lambda: self.save_phone_contact(c),
                                    "suggested-action"))
        top.append(_icon_button("emblem-shared-symbolic",
                                "Share as a QR code", lambda: self.share_qr(c)))
        top.append(_icon_button("document-save-symbolic", "Export this contact",
                                lambda: self.pick_export([c])))
        if c.source == "local":
            top.append(_icon_button("user-trash-symbolic", "Delete this contact",
                                    lambda: self.delete([c.uid])))
        self.sheet.append(top)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        head.append(_avatar(c, 96))
        name = _label(c.display_name(), "title-2")
        name.set_xalign(0.5)
        head.append(name)
        sub = " · ".join(p for p in (c.nickname and f"“{c.nickname}”", c.role, c.org) if p)
        if c.source == "phone":
            sub = " · ".join(p for p in (sub, FROM_PHONE) if p)
        if sub:
            s = _label(sub, "dim-label")
            s.set_xalign(0.5)
            head.append(s)
        self.sheet.append(head)
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        group.add_css_class("boxed-list")
        for kind, number in c.phones:
            actions = Gtk.Box(spacing=4)
            actions.append(_icon_button("call-start-symbolic", f"Call {number}",
                                        lambda n=number: self.call(n)))
            actions.append(_icon_button("mail-message-new-symbolic", f"Message {number}",
                                        lambda n=number: self.message(n)))
            group.append(common.row(number, _kind_name(ct.PHONE_TYPES, kind), actions))
        for kind, address in c.emails:
            group.append(common.row(address, _kind_name(ct.PLACE_TYPES, kind) + " email",
                                    _icon_button("mail-send-symbolic", f"Write to {address}",
                                                 lambda a=address: self._open(f"mailto:{a}"))))
        for kind, adr in c.addresses:
            text = ", ".join(adr.get(k, "") for k, _n in ct.ADDRESS_PARTS if adr.get(k, ""))
            group.append(common.row(text, _kind_name(ct.PLACE_TYPES, kind) + " address"))
        if c.birthday:
            group.append(common.row(self._birthday_text(c.birthday), "Birthday"))
        for url in c.urls:
            group.append(common.row(url, "Website", _icon_button(
                "web-browser-symbolic", f"Open {url}", lambda u=url: self._open(u))))
        if c.note:
            group.append(common.row(c.note, "Note"))
        if group.get_first_child() is not None:
            self.sheet.append(group)
        self.stack.set_visible_child_name("sheet")

    @staticmethod
    def _birthday_text(value: str) -> str:
        import time
        try:
            if value.startswith("--"):
                return time.strftime("%-d %B", time.strptime("2000" + value[1:], "%Y-%m-%d"))
            return time.strftime("%-d %B %Y", time.strptime(value, "%Y-%m-%d"))
        except ValueError:
            return value

    def _open(self, uri: str) -> None:
        root = self.widget.get_root()
        Gtk.UriLauncher.new(uri).launch(root if isinstance(root, Gtk.Window) else None, None, None)

    def back(self) -> None:
        self.stack.set_visible_child_name("list")

    def save_phone_contact(self, c: ct.Contact) -> None:
        """Keep a copy of a phone contact as your own, and open it to edit."""
        mine = c.copy()
        mine.uid, mine.source = "", "local"
        mine.phones = [("cell", n) for n in c.numbers()]
        saved = ct.book().save(mine)
        self.refresh()
        self.toast(f"Saved {saved.display_name()} to your contacts")
        self.open_sheet(saved)

    # --- adding a number to a contact you already have ----------------------------------

    def pick_for_number(self, number: str) -> None:
        """Show the list so the person can choose whom `number` belongs to."""
        self.pick_number = number
        self.pick_bar.set_visible(bool(number))
        self.pick_label.set_text(f"Choose the contact to add {number} to." if number else "")
        if number:
            self.set_selecting(False)
            self.stack.set_visible_child_name("list")

    def add_number_to(self, c: ct.Contact) -> None:
        number = self.pick_number
        self.pick_for_number("")
        if c.source == "phone":
            mine = c.copy()
            mine.uid, mine.source = "", "local"
            mine.phones = [("cell", n) for n in c.numbers()]
            c = mine
        if not any(ct.same_number(number, n) for n in c.numbers()):
            c.phones.append(("cell", number))
        self.edit(c)                    # shown in the editor, saved when they press Save

    # --- the editor ----------------------------------------------------------------------

    def edit(self, c: Optional[ct.Contact], number: str = "") -> None:
        """Edit `c`, or a new contact (optionally with `number` already in it)."""
        c = c.copy() if c is not None else ct.Contact(phones=[("cell", number)] if number else [])
        self.editing = c
        self.photo = (c.photo, c.photo_type)
        common.clear(self.editor)
        top = Gtk.Box(spacing=6)
        top.append(_text_button("Cancel", lambda: self.open_sheet(self.current)
                                if c.uid and self.current is not None and self.current.uid == c.uid
                                else self.back(), "flat"))
        title = _label("Edit contact" if c.uid else "New contact", "heading", wrap=False)
        title.set_hexpand(True)
        title.set_xalign(0.5)
        top.append(title)
        top.append(_text_button("Save", self.save_editor, "suggested-action"))
        self.editor.append(top)
        self.editor_note = _label("", "error")
        self.editor_note.set_visible(False)
        self.editor.append(self.editor_note)

        photo = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.editor_avatar_box = Gtk.Box()
        self.editor_avatar_box.append(_avatar(c, 72))
        photo.append(self.editor_avatar_box)
        pbuttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        pbuttons.append(_text_button("Choose a photo...", self.pick_photo))
        pbuttons.append(_text_button("Remove the photo", lambda: self.set_photo(b"", ""), "flat"))
        photo.append(pbuttons)
        self.editor.append(photo)

        names = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        names.add_css_class("boxed-list")
        self.f_given = Adw.EntryRow(title="First name", text=c.given)
        self.f_family = Adw.EntryRow(title="Last name", text=c.family)
        self.f_nick = Adw.EntryRow(title="Nickname", text=c.nickname)
        if not c.given and not c.family and c.full_name:
            self.f_given.set_text(c.full_name)        # an imported card with only a full name
        for w in (self.f_given, self.f_family, self.f_nick):
            names.append(w)
        self.editor.append(names)

        self.f_phones = self._multi("Phone numbers", "Add a phone number", ct.PHONE_TYPES, c.phones, "cell",
                                    Gtk.InputPurpose.PHONE)
        self.f_emails = self._multi("Email addresses", "Add an email address", ct.PLACE_TYPES, c.emails,
                                    "home", Gtk.InputPurpose.EMAIL)
        self._addresses(c.addresses)

        more = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        more.add_css_class("boxed-list")
        self.f_bday = Adw.EntryRow(title="Birthday (year-month-day, the year optional)",
                                   text=c.birthday.lstrip("-") if c.birthday.startswith("--") else c.birthday)
        self.f_org = Adw.EntryRow(title="Organisation", text=c.org)
        self.f_role = Adw.EntryRow(title="Role", text=c.role)
        for w in (self.f_bday, self.f_org, self.f_role):
            more.append(w)
        self.editor.append(more)
        self.f_urls = self._multi("Websites", "Add a website", None, [("", u) for u in c.urls], "",
                                  Gtk.InputPurpose.URL)
        self.editor.append(_label("Note", "heading"))
        self.f_note = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.f_note.get_buffer().set_text(c.note)
        self.f_note.set_size_request(-1, 80)
        frame = Gtk.Frame(child=self.f_note)
        self.editor.append(frame)
        self.stack.set_visible_child_name("edit")

    def _multi(self, heading: str, add_label: str, kinds, values, default_kind: str,
               purpose: Gtk.InputPurpose) -> list:
        """A list of (type, value) fields with Add and a remove button on each."""
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        holder.append(_label(heading, "heading"))
        fields: list = []

        def add(kind: str = default_kind, value: str = "") -> None:
            line = Gtk.Box(spacing=6)
            dd = _dropdown(kinds, kind) if kinds else None
            if dd is not None:
                line.append(dd)
            entry = Gtk.Entry(text=value, hexpand=True, input_purpose=purpose,
                              placeholder_text=heading[:-1] if heading.endswith("s") else heading)
            line.append(entry)
            item = (dd, entry, line)
            line.append(_icon_button("list-remove-symbolic", "Remove",
                                     lambda: (fields.remove(item), holder.remove(line))))
            fields.append(item)
            holder.insert_child_after(line, rows_end())

        def rows_end():
            return fields[-2][2] if len(fields) > 1 else holder.get_first_child()
        for kind, value in values:
            add(kind or default_kind, value)
        button = _text_button(add_label, lambda: add(), "flat")
        button.set_halign(Gtk.Align.START)
        holder.append(button)
        holder.add_one = add
        self.editor.append(holder)
        return fields

    def _addresses(self, values) -> None:
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        holder.append(_label("Addresses", "heading"))
        self.f_addresses: list = []
        boxes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        holder.append(boxes)

        def add(kind: str = "home", adr: Optional[dict] = None) -> None:
            adr = adr or {}
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            head = Gtk.Box(spacing=6)
            dd = _dropdown(ct.PLACE_TYPES, kind)
            head.append(dd)
            head.append(Gtk.Box(hexpand=True))
            entries = {}
            item = (dd, entries, card)
            head.append(_icon_button("list-remove-symbolic", "Remove this address",
                                     lambda: (self.f_addresses.remove(item), boxes.remove(card))))
            card.append(head)
            for key, label in ct.ADDRESS_PARTS:
                e = Gtk.Entry(text=adr.get(key, ""), placeholder_text=label)
                entries[key] = e
                card.append(e)
            self.f_addresses.append(item)
            boxes.append(card)
        for kind, adr in values:
            add(kind, adr)
        button = _text_button("Add an address", lambda: add(), "flat")
        button.set_halign(Gtk.Align.START)
        holder.append(button)
        self.editor.append(holder)

    def pick_photo(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a photo", modal=True)
        images = Gtk.FileFilter(name="Pictures")
        images.add_mime_type("image/*")
        dialog.set_default_filter(images)
        root = self.widget.get_root()

        def chosen(dlg, result):
            try:
                f = dlg.open_finish(result)
            except GLib.Error:
                return                  # cancelled
            if f is not None and f.get_path():
                self.photo_from(f.get_path())
        dialog.open(root if isinstance(root, Gtk.Window) else None, None, chosen)

    def photo_from(self, path: str) -> bool:
        """Use a picture file as the photo, scaled down so the vCard stays small."""
        try:
            from gi.repository import GdkPixbuf
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 256, 256, True)
            ok, data = pix.save_to_bufferv("jpeg", ["quality"], ["85"])
        except (GLib.Error, ImportError, ValueError) as exc:
            self._editor_problem(f"That picture could not be read: {exc}")
            return False
        self.set_photo(bytes(data), "image/jpeg")
        return True

    def set_photo(self, data: bytes, mime: str) -> None:
        self.photo = (data, mime)
        preview = self.editing.copy()
        preview.photo, preview.photo_type = data, mime
        common.clear(self.editor_avatar_box)
        self.editor_avatar_box.append(_avatar(preview, 72))

    def _editor_problem(self, text: str) -> None:
        self.editor_note.set_text(text)
        self.editor_note.set_visible(bool(text))

    def save_editor(self) -> Optional[ct.Contact]:
        c = self.editing
        c.given, c.family = self.f_given.get_text().strip(), self.f_family.get_text().strip()
        c.nickname = self.f_nick.get_text().strip()
        joined = " ".join(p for p in (c.given, c.family) if p)
        c.full_name = joined or ("" if c.given or c.family else c.full_name if c.uid else "")
        c.phones = [(_kind_of(ct.PHONE_TYPES, dd), e.get_text().strip()) for dd, e, _l in self.f_phones
                    if e.get_text().strip()]
        c.emails = [(_kind_of(ct.PLACE_TYPES, dd), e.get_text().strip()) for dd, e, _l in self.f_emails
                    if e.get_text().strip()]
        c.addresses = []
        for dd, entries, _card in self.f_addresses:
            adr = {k: e.get_text().strip() for k, e in entries.items()}
            if any(adr.values()):
                c.addresses.append((_kind_of(ct.PLACE_TYPES, dd), adr))
        bday, problem = birthday_from(self.f_bday.get_text())
        if problem:
            self._editor_problem(problem)
            return None
        c.birthday = bday
        c.org, c.role = self.f_org.get_text().strip(), self.f_role.get_text().strip()
        c.urls = [e.get_text().strip() for _d, e, _l in self.f_urls if e.get_text().strip()]
        buf = self.f_note.get_buffer()
        c.note = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        c.photo, c.photo_type = self.photo
        if not (c.full_name or c.nickname or c.org or c.phones or c.emails):
            self._editor_problem("Give the contact a name, a number or an email address first.")
            return None
        try:
            saved = ct.book().save(c)
        except OSError as exc:
            self._editor_problem(f"Not saved: {exc}")
            return None
        self.refresh()
        self.open_sheet(saved)
        return saved

    # --- the QR code ----------------------------------------------------------------------

    def share_qr(self, c: ct.Contact) -> None:
        """The contact as a QR code another phone's camera adds in one tap.

        The photo is left out: a QR code holds about 2 kB, and a photo alone is
        bigger than that. Drawn by `qrencode`, the same as Android pairing."""
        from shani_link import adb_pair, paths
        common.clear(self.qr)
        top = Gtk.Box(spacing=6)
        top.append(_icon_button("go-previous-symbolic", "Back", lambda: self.open_sheet(c)))
        title = _label(f"Share {c.display_name()}", "heading", wrap=False)
        title.set_hexpand(True)
        top.append(title)
        self.qr.append(top)
        bare = c.copy()
        bare.photo, bare.photo_type = b"", ""
        png = adb_pair.qr_png(ct.serialize(bare))
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)) if png else None
        except GLib.Error:
            texture, png = None, None
        if texture is not None:
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_size_request(260, 260)
            picture.set_can_shrink(True)
            picture.set_halign(Gtk.Align.CENTER)
            self.qr.append(picture)
            note = _label("Point the other phone's camera at this code to add the contact.", "dim-label")
        else:
            note = _label(paths.tool_missing("qrencode", "draw the QR code"), "dim-label")
        note.set_xalign(0.5)
        self.qr.append(note)
        self.qr_png = png
        self.stack.set_visible_child_name("qr")

    # --- import and export ---------------------------------------------------------------------

    def pick_import(self) -> None:
        dialog = Gtk.FileDialog(title="Import contacts", modal=True)
        cards = Gtk.FileFilter(name="Contact files (.vcf)")
        cards.add_pattern("*.vcf")
        cards.add_pattern("*.vcard")
        cards.add_mime_type("text/vcard")
        dialog.set_default_filter(cards)
        root = self.widget.get_root()

        def chosen(dlg, result):
            try:
                files = dlg.open_multiple_finish(result)
            except GLib.Error:
                return
            self.import_paths([f.get_path() for f in files if f.get_path()])
        dialog.open_multiple(root if isinstance(root, Gtk.Window) else None, None, chosen)

    def import_paths(self, paths_: List[str]) -> int:
        added, problems = 0, []
        for p in paths_:
            try:
                added += len(ct.book().import_file(p))
            except (OSError, UnicodeError) as exc:
                problems.append(f"{p}: {exc}")
        self.refresh()
        if problems:
            self.toast("Could not read " + "; ".join(problems))
        else:
            self.toast(f"Imported {common.plural(added, 'contact')}" if added
                       else "That file has no contacts in it")
        return added

    def pick_export(self, chosen: Optional[List[ct.Contact]]) -> None:
        which = chosen if chosen is not None else [c for c in self.entries]
        if not which:
            self.toast("There are no contacts to export")
            return
        dialog = Gtk.FileDialog(title="Export contacts", modal=True,
                                initial_name=(f"{which[0].display_name()}.vcf" if len(which) == 1
                                              else "contacts.vcf"))
        root = self.widget.get_root()

        def chosen_file(dlg, result):
            try:
                f = dlg.save_finish(result)
            except GLib.Error:
                return
            if f is not None and f.get_path():
                self.export_to(f.get_path(), which)
        dialog.save(root if isinstance(root, Gtk.Window) else None, None, chosen_file)

    def export_to(self, path: str, which: List[ct.Contact]) -> bool:
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(ct.export(which))
        except OSError as exc:
            self.toast(f"Not exported: {exc}")
            return False
        self.toast(f"Exported {common.plural(len(which), 'contact')}")
        if self.selecting:
            self.set_selecting(False)
        return True
