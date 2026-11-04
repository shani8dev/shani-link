"""The Paired tab of Devices, as a built widget.

Moved from Shani Chronoa's tests/test_surface_devices.py with the page; its gate,
trigger and sidebar-registry tests did not come along: they were about the
assistant's switch, its `phone` trigger and Chronoa's sidebar.

This page's one promise is that the three ways of there being no device list are
each reported as itself. "No phone link installed", "the link did not answer"
and "nothing is paired" are different facts about a machine, and
the failure mode this file is built against is the confident wrong answer: a
panel with no rows and no explanation, which reads as a bug until you know which
of the four it was.

Rows are read by walking the built widget tree and reading each row's own labels,
never off a list the module stashed on itself. A count read back out of the thing
being counted is the mistake that produced the visual-regression suite
AGENTS.md records as having been deleted: fifteen assertions that all passed
against a window which rendered nothing.

**The stub stands in at `devices.ph`** - the name `build()` reads - and not on
the real `shani_link.phone`. That is the rule the
package states about where to patch, and it also buys the assertion that matters
most here: the fake counts its calls, so "the gate is shut, so the link was never
asked" is checked against a real count rather than inferred from the text.

`common.adw_ready()` is branched on wherever the two row shapes differ, because
that is the condition `common.row()` itself uses to choose between an
`Adw.ActionRow` (whose `use-markup` is True on libadwaita 1.5) and a plain
`Gtk.Box` of `Gtk.Label`s (which take no markup). The two shapes want opposite
escaping, so a test asserting one behaviour unconditionally would be asserting a
difference that is not there.
"""

from __future__ import annotations

import ast
import signal
import threading
import types
from pathlib import Path
from typing import Any, Iterable, List, Optional

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_link import phone as real_phone  # noqa: E402
from shani_link.app import common, devices  # noqa: E402

from _gui import needs_display  # noqa: E402

pytestmark = needs_display

#: A name carrying all three markup-significant characters. A bare `&` is already
#: enough to make Pango refuse the whole string, which is the point: unescaped,
#: the row renders as *nothing* rather than as a wrong character.
HOSTILE = "Ann & <b>Bob</b> phone"


# --- stubs -----------------------------------------------------------------


class _FakePhone:
    """`shani_link.phone` at the three seams `build()` reads.

    `calls` records which of them were reached, so "the gate is shut, so nothing
    was asked of the link" is a measurement rather than an inference from text.
    `PhoneUnavailable` is the *real* exception class, because `_paired()` catches
    it by identity off this object's attribute.
    """

    PhoneUnavailable = real_phone.PhoneUnavailable

    def __init__(self, backend: Optional[str] = "kdeconnect",
                 found: Iterable[Any] = (), battery: Any = None,
                 raises: Optional[BaseException] = None,
                 battery_raises: Optional[BaseException] = None) -> None:
        self._backend = backend
        self._found = list(found)
        self._battery = battery
        self._raises = raises
        self._battery_raises = battery_raises
        self.calls: List[str] = []

    def backend(self) -> Optional[str]:
        self.calls.append("backend")
        return self._backend

    def devices(self) -> List[Any]:
        self.calls.append("devices")
        if self._raises is not None:
            raise self._raises
        return list(self._found)

    def battery(self, device: Any) -> Any:
        self.calls.append("battery")
        if self._battery_raises is not None:
            raise self._battery_raises
        return self._battery


def _device(ident: str, name: str, reachable: bool = True, paired: bool = True) -> Any:
    """A real `phone.Device`, so the panel reads the real attribute names."""
    return real_phone.Device(id=ident, name=name, reachable=reachable, paired=paired)


def _app() -> Any:
    return types.SimpleNamespace()


def _install(monkeypatch: pytest.MonkeyPatch, phone: _FakePhone) -> None:
    """Patch the seam where `build()` reads the phone link."""
    monkeypatch.setattr(devices, "ph", phone)


# --- reading the built widget ----------------------------------------------


def _labels(widget: Gtk.Widget) -> List[str]:
    """Every `Gtk.Label`'s text in the built tree, in order."""
    found: List[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_text())
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


def _marked(widget: Gtk.Widget, css: str) -> List[Gtk.Widget]:
    """Every widget carrying `css`, found by walking the tree."""
    found: List[Gtk.Widget] = []
    if css in widget.get_css_classes():
        found.append(widget)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_marked(child, css))
        child = child.get_next_sibling()
    return found


def _row_title(row: Gtk.Widget) -> str:
    """The row's title as a reader sees it - the rendered label's text.

    Deliberately *not* `Adw.ActionRow.get_title()`: measured on libadwaita 1.5,
    that getter hands back the stored Pango markup (`Ann &amp; <b>Bob</b>`) while
    `get_subtitle()` hands back the parsed text. Comparing a rendered name against
    a stored-markup getter compares two different alphabets and finds nothing.
    Reading labels works for both row shapes and means the same thing in both.
    """
    for text in _labels(row):
        if text.strip():
            return text
    raise AssertionError(f"row has no title text at all: {_labels(row)!r}")


def _row_text(row: Gtk.Widget) -> str:
    """Everything the row shows, title first, one label per line."""
    return "\n".join(_labels(row))


def _device_rows(widget: Gtk.Widget) -> List[Gtk.Widget]:
    """Only the device rows, which carry their own css class."""
    return _marked(widget, devices.DEVICE_CSS)


def _device_row_for(widget: Gtk.Widget, name: str) -> Gtk.Widget:
    for row in _device_rows(widget):
        if _row_title(row) == name:
            return row
    raise AssertionError(
        f"no device row for {name!r}; the panel has "
        f"{[_row_title(r) for r in _device_rows(widget)]!r}"
    )


def _said(widget: Gtk.Widget) -> str:
    """Every piece of text on the page, joined."""
    return "\n".join(_labels(widget))


# --- the module's own contract --------------------------------------------


class TestBuild:
    def test_build_returns_a_gtk_widget(self):
        assert isinstance(devices.build(_app()), Gtk.Widget)

    def test_a_stub_app_with_nothing_on_it_still_builds(self):
        """No settings is a normal state. An exception here takes the window down
        rather than one panel."""
        assert isinstance(devices.build(types.SimpleNamespace()), Gtk.Widget)

class TestTheRows:
    def test_a_stubbed_device_list_renders_one_row_per_device(self, monkeypatch):
        found = [
            _device("abc123", "Pixel 8"),
            _device("def456", "Galaxy Tab"),
            _device("ghi789", "Old Nook", reachable=False),
        ]
        _install(monkeypatch, _FakePhone(found=found, battery=(72, False)))
        widget = devices.build(_app())
        rows = _device_rows(widget)
        assert len(rows) == 3, [_row_title(r) for r in rows]
        assert [_row_title(r) for r in rows] == ["Pixel 8", "Galaxy Tab", "Old Nook"]

    def test_each_row_says_its_kind_its_battery_and_when_it_was_read(self, monkeypatch):
        _install(
            monkeypatch,
            _FakePhone(found=[_device("abc123", "Pixel 8")], battery=(72, True)),
        )
        row = _device_row_for(devices.build(_app()), "Pixel 8")
        text = _row_text(row)
        assert "kind: paired, reachable right now" in text, text
        assert "battery: 72% and charging" in text, text
        assert "last known at " in text, text
        stamps = _marked(row, devices.WHEN_CSS)
        assert len(stamps) == 1 and stamps[0].get_text(), "the row carries no timestamp"

    def test_an_unreachable_device_is_never_given_a_battery_reading(self, monkeypatch):
        """`skills/phone.py` asks only of a reachable device, and for the same
        reason: an unreachable phone's last battery reading is not a current one.
        A battery shown beside a fresh timestamp makes the timestamp lie."""
        phone = _FakePhone(found=[_device("abc123", "Old Nook", reachable=False)],
                           battery=(9, False))
        _install(monkeypatch, phone)
        row = _device_row_for(devices.build(_app()), "Old Nook")
        text = _row_text(row)
        assert "kind: paired, not reachable" in text, text
        assert "not read, the device is not reachable" in text, text
        assert "9%" not in text, text
        assert "battery" not in phone.calls, (
            f"an unreachable device's battery was read anyway: {phone.calls}"
        )

    def test_a_backend_that_reports_no_battery_says_that_rather_than_zero(
        self, monkeypatch
    ):
        """`phone.battery()` returns None on GSConnect and without busctl. No
        reading is not 0%."""
        _install(monkeypatch,
                 _FakePhone(found=[_device("abc123", "Pixel 8")], battery=None))
        row = _device_row_for(devices.build(_app()), "Pixel 8")
        text = _row_text(row)
        assert "this backend does not report one" in text, text
        assert "0%" not in text, text

    def test_an_unpaired_device_is_not_listed(self, monkeypatch):
        """`phone.devices()` returns everything the link has ever seen. A row for a
        device the user has unpaired is not one of theirs any more."""
        _install(monkeypatch, _FakePhone(found=[
            _device("abc123", "Pixel 8"),
            _device("def456", "Unpaired Tablet", paired=False),
        ]))
        rows = _device_rows(devices.build(_app()))
        assert [_row_title(r) for r in rows] == ["Pixel 8"]

    def test_the_backend_is_named_to_a_person_not_by_the_modules_own_slug(
        self, monkeypatch
    ):
        _install(monkeypatch, _FakePhone(backend="gsconnect",
                                         found=[_device("abc123", "Pixel 8")]))
        said = _said(devices.build(_app()))
        assert devices.BACKEND_TITLE["gsconnect"] in said, said
        assert "kdeconnect" not in said, said


# --- the honest states ----------------------------------------------------


class TestTheHonestStates:
    def test_no_backend_installed_is_its_own_message(self, monkeypatch):
        """`phone.backend()` returning None is this machine having no phone link.
        It is emphatically not this machine having no phone."""
        phone = _FakePhone(backend=None)
        _install(monkeypatch, phone)
        widget = devices.build(_app())
        said = _said(widget)
        assert devices.HEADLINE[devices.STATE_NO_LINK] in said, said
        assert "kdeconnect-cli" in said, said
        assert "GSConnect" in said, said
        assert "not a machine with no phone" in said, said
        assert "devices" not in phone.calls, (
            f"devices were asked for with no backend installed: {phone.calls}"
        )

    def test_a_link_that_will_not_answer_shows_the_error_it_gave(self, monkeypatch):
        """`PhoneUnavailable` is `phone.py`'s own "not no phone", and its message
        is the most specific thing available - so it is shown rather than replaced
        by a generic one."""
        _install(monkeypatch, _FakePhone(
            raises=real_phone.PhoneUnavailable(
                "GSConnect is not running (no session bus); it is a GNOME Shell "
                "extension that has to be switched on")))
        said = _said(devices.build(_app()))
        assert devices.HEADLINE[devices.STATE_LINK_DOWN] in said, said
        assert "has to be switched on" in said, said

    def test_no_device_paired_is_its_own_message(self, monkeypatch):
        _install(monkeypatch, _FakePhone(found=[]))
        widget = devices.build(_app())
        said = _said(widget)
        assert devices.HEADLINE[devices.STATE_NONE_PAIRED] in said, said
        assert "has nothing paired to it" in said, said
        assert _device_rows(widget) == [], "a row appeared with nothing paired"

    def test_the_four_states_render_as_four_different_pages(self, monkeypatch):
        """The three the panel is judged on, plus the fourth, checked against each
        other. A panel that printed one message for all of them would pass every
        assertion above individually and still be useless, because a reader could
        not tell which case they were in."""
        cases = (
            ("listed", None, _FakePhone(found=[_device("abc123", "Pixel 8")])),
            ("no link", None, _FakePhone(backend=None)),
            ("link down", None, _FakePhone(
                raises=real_phone.PhoneUnavailable("KDE Connect did not answer"))),
            ("none paired", None, _FakePhone(found=[])),
        )
        rendered = {}
        for label, config, phone in cases:
            with monkeypatch.context() as one:
                _install(one, phone)
                text = _said(devices.build(_app()))
            assert text.strip(), f"{label} rendered nothing at all"
            rendered[label] = text
        for label, text in rendered.items():
            for other, other_text in rendered.items():
                if label != other:
                    assert text != other_text, f"{label} and {other} render identically"

class TestEscaping:
    def test_a_hostile_device_name_is_rendered_as_itself_and_not_as_nothing(
        self, monkeypatch
    ):
        """The failure mode of getting this wrong is not a wrong character: with
        libadwaita, `use-markup` is True, so Pango refuses the whole string and the
        row renders *blank*, with only a Gtk-WARNING to say so. An empty page is
        exactly what this panel is not allowed to ship."""
        _install(monkeypatch, _FakePhone(found=[_device("abc123", HOSTILE)]))
        widget = devices.build(_app())
        assert len(_device_rows(widget)) == 1, _marked(widget, devices.ROW_CSS)
        assert HOSTILE in _labels(widget), _labels(widget)

    @pytest.mark.skipif(not common.adw_ready(),
                        reason="no libadwaita: the fallback row takes no markup")
    def test_with_adw_the_row_carries_entities_and_reports_use_markup(
        self, monkeypatch
    ):
        """`get_title()` returns the stored markup, so this is where the entities
        are visible; the label beside it is where they are shown to have worked.
        The second assertion is the one that would fail if the escaping were
        removed: the raw string renders as a *blank* row, not as a wrong one."""
        _install(monkeypatch, _FakePhone(found=[_device("abc123", HOSTILE)]))
        row = _device_row_for(devices.build(_app()), HOSTILE)
        assert row.get_property("use-markup") is True, (
            "if use-markup is ever False the escaping stops being load-bearing and "
            "this test asserts a difference that is not there"
        )
        assert row.get_title() == common.escape(HOSTILE), row.get_title()
        assert "&amp;" in row.get_title() and "&lt;b&gt;" in row.get_title()
        assert _labels(row)[0] == HOSTILE, _labels(row)

    @pytest.mark.skipif(not common.adw_ready(),
                        reason="no libadwaita: the Adw row is not built")
    def test_with_adw_the_subtitle_is_escaped_too(self, monkeypatch):
        """The battery line can carry a subprocess's own punctuation, and the
        subtitle is parsed as markup exactly like the title - so unescaped it is
        not rendered as `<failed>` but discarded whole. What must appear is the
        original text, with the angle brackets shown rather than acted on."""
        _install(monkeypatch, _FakePhone(
            found=[_device("abc123", "Pixel 8")],
            battery_raises=OSError("busctl: /org/kde/kdeconnect > & <failed>")))
        row = _device_row_for(devices.build(_app()), "Pixel 8")
        shown = _labels(row)
        assert any("<failed>" in text for text in shown), shown
        assert any("&lt;failed&gt;" not in text and "&amp;" not in text
                   for text in shown), shown
        assert not any(text == "" for text in shown[1:2]), f"the subtitle is blank: {shown}"

    @pytest.mark.skipif(common.adw_ready(),
                        reason="libadwaita is present: the Adw path is the real one")
    def test_without_adw_the_text_is_not_escaped(self, monkeypatch):
        """`Gtk.Label(label=...)` takes no markup, so escaping there would put a
        literal `&amp;` on screen. The branch is on `common.adw_ready()` because
        that is the same condition `common.row()` uses to choose the shape."""
        _install(monkeypatch, _FakePhone(found=[_device("abc123", HOSTILE)]))
        widget = devices.build(_app())
        assert HOSTILE in _labels(widget), _labels(widget)
        assert not any("&amp;" in text for text in _labels(widget)), _labels(widget)


# --- what this panel must not do ------------------------------------------


class TestBoundaries:
    def test_it_adds_no_transport_of_its_own(self):
        """Read from the module's imports and calls rather than its prose, with
        `ast` - a text search for "subprocess" or "busctl" matches this panel's
        own *explanations* to the reader ("no kdeconnect-cli", "busctl is absent"),
        which is the opposite of the thing being asserted.

        A panel that grew a subprocess call of its own would still build fine in a
        test. The only subprocesses and D-Bus calls in the phone path belong
        to `phone.py`, which this panel reads rather than reimplementing.
        """
        tree = ast.parse(Path(devices.__file__).read_text(encoding="utf-8"))
        imported: set = set()
        called: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Call):
                func = node.func
                called.add(func.attr if isinstance(func, ast.Attribute)
                           else func.id if isinstance(func, ast.Name) else "")

        for name in sorted(imported):
            root = name.split(".")[0]
            assert root not in ("subprocess", "shutil", "socket", "dbus", "asyncio"), (
                f"this module imports {name!r}; it must read phone.py and add no "
                "transport of its own"
            )
        for name in ("Popen", "check_output", "check_call", "bus_get_sync",
                     "call_sync", "spawn_sync"):
            assert name not in called, (
                f"this module calls {name}(); that is a transport of its own, and "
                "the phone link belongs to phone.py"
            )

    def test_the_only_phone_module_it_reaches_is_phone_py(self):
        """`devices.ph` is `shani_link.phone`. If it were ever repointed at a
        second module, this panel would be reading a different phone path from the
        one the `phone` skill and trigger use - which is the thing this panel
        promises not to be."""
        assert devices.ph.__name__ == "shani_link.phone"
        assert devices.ph is real_phone

    def test_it_starts_no_thread_and_arms_no_timer(self, monkeypatch):
        timer_before = signal.getitimer(signal.ITIMER_REAL)
        handler_before = signal.getsignal(signal.SIGALRM)
        threads_before = threading.active_count()
        _install(monkeypatch, _FakePhone(found=[_device("abc123", "Pixel 8")]))
        devices.build(_app())
        assert signal.getitimer(signal.ITIMER_REAL) == timer_before
        assert signal.getsignal(signal.SIGALRM) is handler_before
        assert threading.active_count() == threads_before
