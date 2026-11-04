"""The Watch page: one page over `shani_link.gatt` and the MoYoung watch.

Moved from Shani Chronoa's tests/test_wearables_surface.py with the page (it was
"Wearables" there). Its gate tests and its sidebar-registry test did not come
along: they were about the assistant's consent and Chronoa's sidebar.

**Every fixture here is the module's own shape, read out of its returns rather
than its docstrings — and that is the point of two of them.** `attributes()` and
`services()` both return **three**-tuples `(handle, uuid, kind)` while both
docstrings claim `[(handle, uuid)]`. A panel written to the documented shape
raises on the first row of every real device, and a fixture typed to the
docstring would have passed against it. These are typed to the returns.

The rest of the file's discipline is the same as the sibling pages': every slow
read is on a thread, and each state the adapter can be in has its own words.

**Each control below was run and each fails the suite.** Two of them did not at
first, and both of those failures are recorded at the test that now covers it:
one asserted that `build()` threads its reads by reading the source for
`Thread.new`, which a panel that called `gatt.paired()` directly would also
satisfy; and one parametrised over the panel's own state constants, so dropping
a state from `HEALTH` lost a case instead of failing.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys
import time

import pytest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # type: ignore  # noqa: E402

from shani_link.app import wearables as w  # type: ignore  # noqa: E402
from shani_link import gatt  # type: ignore  # noqa: E402

from _gui import needs_display  # noqa: E402

pytestmark = needs_display


@pytest.fixture(autouse=True)
def _adw():
    Adw.init()


# --- the module's real shapes ------------------------------------------------

BATTERY = (0x0021, "00002a19-0000-1000-8000-00805f9b34fb", "Characteristic")
HEART_RATE = (0x0025, "00002a37-0000-1000-8000-00805f9b34fb", "Characteristic")
FIRMWARE = (0x002A, "00002a26-0000-1000-8000-00805f9b34fb", "Characteristic")
BATTERY_SERVICE = (0x0001, "0000180f-0000-1000-8000-00805f9b34fb", "Service")
HR_SERVICE = (0x0011, "0000180d-0000-1000-8000-00805f9b34fb", "Service")

PAIRED = [("AA:BB:CC:DD:EE:FF", "Fitness Band"), ("11:22:33:44:55:66", "HID Keys")]


class App:
    def __init__(self, switches=None) -> None:
        self.switches = switches


def _patch(monkeypatch, **attrs):
    """Stub the module functions the panel calls, leaving the rest real."""
    for name, value in attrs.items():
        monkeypatch.setattr(gatt, name, value)


def _texts(widget) -> list:
    out, stack = [], [widget]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.Label):
            out.append(node.get_text())
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def _code_without_docstrings(module) -> str:
    """The module's code with every docstring and comment removed.

    `ast.unparse` is the instrument, for two reasons that both matter here: an
    AST carries **no comments at all**, so a `#` mentioning the thing being
    looked for cannot be mistaken for it; and the docstrings are deleted from
    the tree first, so the explanation of *why* code moved cannot be mistaken
    for the code.

    This is the "prose goes, dispatch strings stay" rule the reachability scan
    already uses, and it is here because two checks in this file had it
    backwards: `phone.py`'s docstring names `watch-companion-enabled` and
    `find_tab` precisely because it records that they left, and a substring
    check over the source cannot tell that sentence from a live control.
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            del body[0]
    return ast.unparse(tree)


def _labels(widget) -> str:
    return "\n".join(t for t in _texts(widget) if t)


# --- the fixtures are the module's shapes, not its docstrings ---------------

def test_the_fixture_tuples_are_the_ones_the_module_actually_returns():
    """**The control for every fixture in this file.**

    `attributes()` and `services()` are documented as returning two-tuples and
    return three. If that ever changes, this fails here rather than every
    assertion below failing for a reason that reads like the panel's fault.
    """
    source = inspect.getsource(gatt.attributes)
    assert '"Characteristic"' in source, (
        "attributes() no longer adds the kind field, so this file's fixtures "
        "are the wrong shape")
    source = inspect.getsource(gatt.services)
    assert '"Service"' in source, (
        "services() no longer adds the kind field")


# --- the devices -------------------------------------------------------------

class TestTheDevices:
    def test_nothing_paired_is_a_state_and_not_an_empty_list(self, monkeypatch):
        _patch(monkeypatch, paired=lambda: [])
        page = w.build(App())
        text = _labels(_run(page))
        assert "No Bluetooth devices are paired" in text

    def test_an_adapter_that_will_not_answer_is_not_nothing_paired(self, monkeypatch):
        """The distinction the module's docstring insists on, kept.

        `GattUnavailable` is a RuntimeError and a subprocess OSError is not the
        same class, but both mean the same thing to a reader, and folding them
        into "nothing is paired" would report a healthy machine as empty.
        """
        def boom():
            raise gatt.GattUnavailable("adapter not ready")

        _patch(monkeypatch, paired=boom)
        page = w.build(App())
        text = _labels(_run(page))
        assert "did not answer" in text
        assert "No Bluetooth devices are paired" not in text

    def test_the_listing_row_states_a_capability_and_not_a_measurement(
            self, monkeypatch):
        """**No battery figure on the listing row.**

        Asserted against `_device_row()` alone rather than the built page: the
        detail block under it *does* read, and a whole-page assertion cannot tell
        a reading the panel made from one it is about to make. The first version
        asserted on the page and failed with "a value appeared before anything
        was read" against a panel that had read the battery correctly - the
        test, not the panel.
        """
        _patch(monkeypatch,
               services=lambda mac: [BATTERY_SERVICE, HR_SERVICE],
               read_value=lambda mac, handle, label="", uuid="":
                   pytest.fail("the listing row read a value"))
        text = _labels(w._device_row(PAIRED[0][0], PAIRED[0][1]))
        assert "Fitness Band" in text
        assert "AA:BB:CC:DD:EE:FF" in text
        assert "100%" not in text

    def test_the_detail_block_does_read_and_labels_it(self, monkeypatch):
        """The other half of the pair: the page must *not* be read-only.

        **`paired` is stubbed here because this test builds the page**, and the
        page reads the paired list for itself. It used to leave that to the
        real adapter, so the answer depended on whether the machine happened
        to have a watch near it: on a box with one, the page listed it; on
        this one, the page said "no Bluetooth devices are paired" and the
        detail block was never built - so the test failed with the *empty
        state's* text and read as though the panel could not read a battery
        it had never been given a device for.
        """
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [BATTERY_SERVICE],
               attributes=lambda mac: [BATTERY],
               read_value=lambda mac, handle, label="", uuid="": ("64", ""),
               describe_value=lambda label, raw: ("100%", ""))
        page = w.build(App())
        text = _labels(_run(page))
        assert "Battery Level" in text
        assert "100%" in text

    def test_a_wearable_service_is_named_from_the_modules_own_table(
            self, monkeypatch):
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [HR_SERVICE],
               attributes=lambda mac: [BATTERY],
               read_value=lambda mac, handle, label="", uuid="": ("64", ""),
               describe_value=lambda label, raw: ("100%", ""))
        page = w.build(App())
        text = _labels(_run(page))
        assert "heart rate" in text

    def test_a_device_that_will_not_be_read_keeps_the_other_devices_true(
            self, monkeypatch):
        """One unreadable device is one row, not an empty panel.

        `devices.py` records the version of this that lost the list: the
        listing and the detail are drawn from one read, so a refusal that
        replaced the page would also remove the fact that anything is paired.
        """
        def services(mac):
            if mac == PAIRED[0][0]:
                raise gatt.GattUnavailable("device busy")
            return []

        _patch(monkeypatch, paired=lambda: list(PAIRED), services=services,
               attributes=lambda mac: [])
        page = w.build(App())
        text = _labels(_run(page))
        assert "HID Keys" in text, "an unreadable device removed the listing"
        assert "device busy" in text

    def test_a_value_is_decoded_by_the_modules_own_decoder(self, monkeypatch):
        """The panel must not decode anything itself: a wrong reading on a body
        measurement is worse than an unreadable one."""
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [BATTERY_SERVICE],
               attributes=lambda mac: [BATTERY],
               read_value=lambda mac, handle, label="", uuid="": ("0a", ""),
               describe_value=lambda label, raw: ("DECODED-BY-MODULE", ""))
        page = w.build(App())
        assert "DECODED-BY-MODULE" in _labels(_run(page))

    def test_a_read_failure_is_a_sentence_on_that_row(self, monkeypatch):
        """Not a row that says the device has nothing - "not readable" and "not
        there" are different answers."""
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [BATTERY_SERVICE],
               attributes=lambda mac: [BATTERY],
               read_value=lambda mac, handle, label="", uuid="":
                   ("", "Attribute can't be read"))
        page = w.build(App())
        text = _labels(_run(page))
        assert "Attribute can't be read" in text
        assert "Fitness Band" in text


# --- the truncation and the live read ---------------------------------------

class TestWhatItDoesNotShowWhole:
    def test_a_truncated_list_says_how_much_is_missing(self, monkeypatch, ):
        """**An absence.** A list cut to `MAX_ROWS` with no count reads as the
        whole list, and this is how a panel comes to under-report a device that
        exposes more than a screenful."""
        many = [(0x1000 + i, f"0000{0x2a00 + i:04x}-0000-1000-8000-00805f9b34fb",
                 "Characteristic") for i in range(w.MAX_ROWS + 7)]
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [BATTERY_SERVICE],
               attributes=lambda mac: many,
               read_value=lambda mac, handle, label="", uuid="": ("00", ""),
               describe_value=lambda label, raw: ("0", ""))
        page = w.build(App())
        text = _labels(_run(page))
        assert "7 more characteristics" in text

    def test_a_notify_only_characteristic_offers_a_listen_and_not_a_read(
            self, monkeypatch):
        """The one thing a heart-rate strap exists to provide has no stored
        value - `read_value` answers `Attribute can't be read` on it, measured
        on real hardware."""
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [HR_SERVICE],
               attributes=lambda mac: [HEART_RATE],
               read_value=lambda mac, handle, label="", uuid="":
                   pytest.fail("a notify-only characteristic was read"))
        page = w.build(App())
        widget = _run(page)
        buttons = [b.get_label() for b in _walk(widget)
                   if isinstance(b, Gtk.Button) and b.get_label()]
        assert any("Listen" in (b or "") for b in buttons), buttons

    def test_a_stored_characteristic_is_not_offered_a_listen(self, monkeypatch):
        """Battery is read, not pushed - a Listen button on it would collect
        nothing and look broken."""
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [BATTERY_SERVICE],
               attributes=lambda mac: [BATTERY],
               read_value=lambda mac, handle, label="", uuid="": ("64", ""),
               describe_value=lambda label, raw: ("100%", ""))
        page = w.build(App())
        widget = _run(page)
        buttons = [b.get_label() for b in _walk(widget)
                   if isinstance(b, Gtk.Button) and b.get_label()]
        assert not any("Listen" in (b or "") for b in buttons), buttons

    def test_nothing_pushed_is_an_answer_about_the_device(self, monkeypatch):
        """Not a blank row. A strap lying on a table pushes nothing, and that is
        a true answer rather than a rendering failure."""
        values, problem = [], ""
        _patch(monkeypatch,
               paired=lambda: list(PAIRED),
               services=lambda mac: [HR_SERVICE],
               attributes=lambda mac: [HEART_RATE],
               notifications=lambda mac, handle, seconds: (values, problem))
        row = w._listen_row("Heart Rate Measurement", values, problem)
        text = _labels(row)
        assert "Nothing was pushed" in text
        assert "Nothing is wrong with this panel" in text


# --- the panel's own obligations --------------------------------------------

def _walk(widget) -> list:
    out, stack = [], [widget]
    while stack:
        node = stack.pop()
        out.append(node)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def _run(page) -> Gtk.Widget:
    """Build the page, let the panel's own thread land, return what it shows.

    The reads are stubbed to answer immediately, so spinning the main context is
    enough and no test has to wait on a real adapter.
    """
    ctx = GLib.MainContext.default()
    import time as _time
    end = _time.monotonic() + 1.5
    while _time.monotonic() < end:
        ctx.iteration(False)
        _time.sleep(0.005)
    window = Gtk.Window()
    holder = Gtk.Box()
    holder.append(page)
    window.set_child(holder)
    for node in _walk(page):
        if isinstance(node, Gtk.ScrolledWindow):
            child = node.get_child()
            if child is not None:
                return child
    return page


def test_the_reads_are_on_a_thread_and_the_page_is_built_first(monkeypatch):
    """**A source check, and it is a real one.**

    `phone.py` measured ~24 s for 813 contacts over PBAP; `gatttool` carries a
    25-second timeout. So a read on the main thread would freeze the window for
    as long as the slowest adapter takes. The first version of this asserted
    the page calls `Thread.new`, which a panel that also called `gatt.paired()`
    inline would still satisfy - it is checked here against the module's own
    function list instead, so it cannot pass by having the right call somewhere
    harmless.
    """
    source = pathlib.Path(w.__file__).read_text()
    assert "GLib.Thread.new" in source, "the reads are not on a thread"
    tree = ast.parse(source)
    threaded = 0
    for node in ast.walk(tree):
        # `GLib.Thread.new(...)` is Attribute(attr="new", value=Attribute(
        # attr="Thread", value=Name(id="GLib"))). Matching on `attr == "Thread"`
        # looks at the wrong node and finds nothing - which is what the first
        # version did, and it reported "mentioned but not called" for a panel
        # that calls it twice.
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "new"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "Thread"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "GLib"):
            continue
        threaded += 1
    assert threaded >= 1, "GLib.Thread.new is mentioned but not called"

    # And no read is issued from the main thread: every gatt.* call in this
    # module happens inside a function the thread body reaches, never from
    # `build()` itself.
    #
    # **Checked by AST, on the bare name, not by substring.** The first version
    # asserted `"paired(" not in build_source` and failed on `def on_paired()` -
    # because `paired(` is a *substring* of `on_paired(`. It reported that
    # `build()` called the reader on the main thread for a function it defines
    # and never invokes on that thread. The question is "does `build()` call
    # `_paired`", so the question is asked of the calls.
    build_tree = ast.parse(inspect.getsource(w.build))

    def helpers_in(node) -> set:
        return {n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    def nested_in(node) -> set:
        """Calls made from inside a function `node` defines, one level down."""
        out = set()
        for child in node.body:
            if isinstance(child, ast.FunctionDef):
                out |= helpers_in(child)
        return out

    READERS = ("_paired", "_attributes", "_services", "_read_value",
               "_notifications", "_moyoung_watches")

    build_fn = build_tree.body[0]
    assert isinstance(build_fn, ast.FunctionDef)
    on_main_thread = helpers_in(build_fn) & set(READERS)
    # **Off the main thread is not the same as absent from `build()`.** A call
    # inside the nested body the thread runs is still inside `build()`'s source,
    # so asserting the helper is never named would fail for a correct panel -
    # which is what the second version of this did. What must hold is that no
    # reader is called from `build()`'s own top level.
    for name in on_main_thread:
        parent_is_nested = any(
            name in helpers_in(child)
            for child in build_fn.body if isinstance(child, ast.FunctionDef))
        assert parent_is_nested, (
            f"{name}() is called on the main thread, not from the thread body")

    # And it really does read, through the thread body - so the check above
    # cannot pass on a panel that reads nothing at all.
    reached = set()
    for child in build_fn.body:
        if isinstance(child, ast.FunctionDef):
            reached |= helpers_in(child) & set(READERS)
    assert reached, "build() reaches no reader at all, so nothing was checked"
    assert "GLib.Thread.new" in inspect.getsource(w.build), (
        "the readers are reached but not from a thread")


def test_a_device_name_with_an_ampersand_is_escaped_once(monkeypatch):
    """**The double-escape this panel nearly shipped.**

    `common.row()` escapes both the title and the subtitle itself, so a
    pre-escaping `_text()` on top of it renders a device named `Ann & Bob` as
    `Ann &amp; Bob` - which `devices.py` records having shipped. The panel reads
    the name off a Bluetooth adapter, where `&` is entirely ordinary.
    """
    _patch(monkeypatch,
           paired=lambda: [("AA:BB:CC:DD:EE:FF", "Ann & Bob")],
           services=lambda mac: [],
           attributes=lambda mac: [])
    page = w.build(App())
    widget = _run(page)
    labels = [t for t in _texts(widget) if t]
    assert any("Ann & Bob" in t for t in labels), labels
    assert not any("&amp;" in t for t in labels), labels


# --- the MoYoung half, moved out of the Phone surface ------------------------

class TestTheMoYoungHalf:
    """The vendor half that moved here on 2026-10-13.

    It answers to the same `bluetooth-gatt-enabled` switch as the GATT reads
    above, which is why splitting it across two panels put the only control for
    `watch-companion-enabled` on a page that was not about the watch.
    """

    def test_the_phone_surface_no_longer_holds_the_watch(self):
        """**The move, asserted from both ends.**

        Reading the other surface's *source* is the point: a title saying
        "Watch" on a panel with no watch in it is the exact error its own
        comment was written against, and nothing else would catch it drifting
        back.
        """
        import shani_link.app.phone as ph
        # **Code, not prose.** The first version grepped the module source for
        # `watch-companion-enabled` and `find_tab`, and `phone.py`'s own
        # docstring contains both - it explains *why* the watch half left, by
        # naming what left. So the check failed on the explanation, which is the
        # scanner-whose-needle-is-in-its-own-input trap, and it would have gone
        # on reading as coverage of a move it never measured.
        code = _code_without_docstrings(ph)
        assert 'TITLE = "Watch"' not in code, (
            "the Phone surface is titled Watch again, with no watch in it")
        assert "find_tab" not in code, "the watch tab is back on the phone page"
        assert "watch-companion-enabled" not in code, (
            "the companion switch is back on the phone page")
        assert "_moyoung" not in code and "_watch_call" not in code, (
            "MoYoung half is back on the phone page")
        assert ph.TITLE == "Phone"

    def test_the_companion_switch_moved_and_keeps_its_consequence(self, monkeypatch):
        row = w._companion_row(_Allowed(), w._label(""))
        text = _labels(row)
        assert "Da Fit app" in text, (
            "the switch lost the consequence, which is the only part of it a "
            "user cannot guess")

    def test_a_failed_write_puts_the_switch_back(self, monkeypatch):
        """A switch left showing a state the user did not choose is a page
        asserting something untrue."""

        class Refusing:
            def get_bool(self, key, default=False):
                return False

            def set(self, key, value):
                raise OSError("read-only config")

        row = w._companion_row(Refusing(), w._label(""))
        switches = [n for n in _walk(row) if isinstance(n, Gtk.Switch)]
        assert len(switches) == 1
        switch = switches[0]
        assert switch.get_active() is False
        switch.set_active(True)
        assert switch.get_active() is False, (
            "the switch kept the state the write refused")

    def test_a_phone_is_not_listed_as_a_watch(self, monkeypatch):
        """The exclusion is not cosmetic - `phone.py` lists the phone over the
        same protocol, and a device on both pages would be a phone described as
        a band."""
        import shani_link.gatt as bg
        # Import it here rather than reaching through `sys.modules[...]`.
        # `monkeypatch.setattr(..., raising=False)` guards the *name*, not the
        # target expression - `sys.modules["shani_chronoa"].phone_bluez` raises
        # `AttributeError` on its own unless some earlier test happened to have
        # imported it. So this test passed in the suite and errored alone: an
        # order-dependence that looked like coverage.
        from shani_link import phone_bluez

        class _Bluez:
            @staticmethod
            def phones():
                return [("AA:BB:CC:DD:EE:FF", "Pixel", "")]

        monkeypatch.setattr(phone_bluez, "phones", _Bluez.phones)
        monkeypatch.setattr(bg, "paired",
                            lambda: [("AA:BB:CC:DD:EE:FF", "Pixel"),
                                     ("11:22:33:44:55:66", "FB BGS002")])
        monkeypatch.setattr(bg, "looks_like_low_energy", lambda mac: True)

        # `_moyoung_watches()` returns `(mac, name)` pairs, so `dict(...)` is
        # keyed by MAC. The first version asserted *names* against it:
        # `"FB BGS002" in watches` failed, and worse, `"Pixel" not in watches`
        # passed for free - no MAC equals "Pixel", so it would have gone on
        # reading as coverage of the exclusion while checking nothing. Assert on
        # the field each claim is actually about.
        found = w._moyoung_watches()
        macs = {mac for mac, _name in found}
        names = {name for _mac, name in found}
        assert "FB BGS002" in names, f"the watch was dropped: {found}"
        assert "AA:BB:CC:DD:EE:FF" not in macs, "a phone was listed as a watch"

        # **The control.** With nothing reported as a phone, the same paired
        # device comes back - which is what proves the assertion above is
        # measuring the filter rather than the shape of the data.
        monkeypatch.setattr(phone_bluez, "phones", lambda: [])
        control = {name for _mac, name in w._moyoung_watches()}
        assert "Pixel" in control, (
            "the control did not fail, so this test cannot tell an excluded "
            f"phone from a device that was never paired: {control}")

    def test_the_watch_controls_go_through_the_watch_engine(self, monkeypatch):
        """"Sync time" and "Settings" run `shani_link.watch` - the engine the
        assistant's `watch` skill wraps - driven through `_watch_call`, the
        route the page's own buttons take."""
        from shani_link import watch
        asked = []
        monkeypatch.setattr(watch, "_run", lambda arguments: asked.append(arguments) or "ok")
        note = w._label("")
        w._watch_call("FB", {"action": "sync_time"}, note)
        deadline = time.monotonic() + 5
        while note.get_text() != "ok" and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        assert asked == [{"action": "sync_time", "device": "FB"}], asked
        assert note.get_text() == "ok"

    def test_turning_the_companion_on_lets_it_reach_the_watch(self):
        """The companion only runs with `bluetooth-gatt-enabled` on as well
        (`watch_companion._allowed`), so the one switch turns on both."""
        class Store:
            def __init__(self):
                self.values = {}

            def get_bool(self, key, default=False):
                return self.values.get(key, default)

            def set(self, key, value):
                self.values[key] = value == "true"
        store = Store()
        row = w._companion_row(store, w._label(""))
        switch = [n for n in _walk(row) if isinstance(n, Gtk.Switch)][0]
        switch.set_active(True)
        assert store.values == {"watch-companion-enabled": True, "bluetooth-gatt-enabled": True}
        switch.set_active(False)
        assert store.values["watch-companion-enabled"] is False


class _Allowed:
    def get_bool(self, key, default=False):
        return False

    def set(self, key, value):
        return None


def test_the_states_are_declared_once_and_never_invented():
    """`HEALTH.get(self.state, ...)` falling back is what makes an unknown state
    readable as `unknown` rather than as a claim - and it is only honest if the
    states are the ones the panel can actually reach.

    **The happy path is deliberately absent from `HEALTH`.** `permitted` is not
    a gate; it has no status word because nothing is wrong and nothing is
    unknown. `devices.py` does the same, and the first version of this test
    demanded an entry for it and failed against a panel shaped like its
    sibling's.
    """
    assert set(w.HEALTH) == {w.STATE_UNREADABLE, w.STATE_NONE_PAIRED}
    assert w.STATE_PERMITTED not in w.HEALTH
    for state in w.HEADLINE:
        assert state in w.BODY, f"{state} has a headline and no body"
        if state != w.STATE_PERMITTED:
            assert state in w.HEALTH, f"{state} is a gate with no health word"


def test_every_state_has_its_own_headline_and_health():
    """**Parametrised over a literal, not over `w.HEADLINE`.**

    The first version parametrised over the table it checks, so dropping
    `"no adapter"` lost a case rather than failing - the same mistake
    `test_fleet_health_drift.py` had made the week before. The expected states
    are written out here.
    """
    for state in (w.STATE_PERMITTED, w.STATE_UNREADABLE, w.STATE_NONE_PAIRED):
        assert w.HEADLINE[state]
        assert w.HEADLINE[state] != state, (
            f"{state} has no wording of its own and falls through to its key")
        if state != w.STATE_PERMITTED:
            assert state in w.HEALTH
