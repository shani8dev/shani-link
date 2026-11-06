"""Radio, NFC, Remote and Background: the pages written for the app.

Radio, NFC and Remote are each the engine's own report and the engine's own
actions; Background is the switches the service reads. Driven with the engines
stubbed at the names the pages call, so no radio, reader, IR device or bus is
touched (conftest.py also points both buses nowhere).
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from _gui import needs_display, pump, texts  # noqa: E402

pytestmark = needs_display

Adw.init()


def _press(page, label, **fields):
    button, inputs = page.buttons[label]
    for key, value in fields.items():
        inputs[key].set_value(value)
    pump(lambda: button.get_sensitive(), 5)
    button.emit("clicked")
    pump(lambda: button.get_sensitive() and not page.output.get_text().endswith("..."), 5)
    return page.output.get_text()


def _details(page):
    """The engine's own words, folded under Details."""
    out = []

    def walk(w):
        if hasattr(w, "details_text"):
            out.append(w.details_text)
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()
    walk(page)
    return "\n".join(out)


def _typed_entries(page):
    """Entries a person would have to type into, shown without asking."""
    out = []

    def walk(w):
        if isinstance(w, Gtk.Entry) and w.get_visible() and not isinstance(w.get_parent(), Gtk.SpinButton):
            out.append(w)
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()
    walk(page)
    return out


def test_radio_reports_the_hardware_and_tunes_through_the_engine(monkeypatch):
    from shani_link import fm_radio
    from shani_link.app import tools_pages
    monkeypatch.setattr(fm_radio, "hardware", lambda: "no /dev/dvb: no tuner\nrtl_fm is installed (/usr/bin/rtl_fm)")
    asked = []
    monkeypatch.setattr(fm_radio, "run", lambda arguments: asked.append(arguments) or "Would tune 98.3 MHz")
    page = tools_pages.build_radio()
    pump(lambda: "no /dev/dvb: no tuner" in _details(page), 5)
    # plain words in front, the engine's own folded under Details
    assert any("No radio stick plugged in" in t for t in texts(page)), texts(page)
    assert "no /dev/dvb: no tuner" in _details(page)
    assert not any("/dev/dvb" in t for t in texts(page)), "device nodes are not in front of a person"
    assert page.status() == "attention", "no dongle is not ready"
    tune = page.buttons["Tune"][0]
    assert not tune.get_sensitive(), "Tune needs the stick: greyed out, not a button that refuses"
    assert page.buttons["Bands"][0].get_sensitive(), "what works without the stick stays live"
    _press(page, "Bands")
    assert asked[-1] == {"action": "bands"}, "an empty field is not passed as an empty value"


def test_radio_is_ready_only_with_a_dongle_and_the_tools(monkeypatch):
    from shani_link import fm_radio
    from shani_link.app import tools_pages
    monkeypatch.setattr(fm_radio, "hardware", lambda: "rtl_fm is installed (/x)\nan RTL2832U dongle is attached:")
    asked = []
    monkeypatch.setattr(fm_radio, "run", lambda arguments: asked.append(arguments) or "Would tune 98.3 MHz")
    page = tools_pages.build_radio()
    pump(lambda: page.status() == "ok", 5)
    assert page.status() == "ok"
    # a frequency is picked, not typed; "Other..." is the typed path
    assert _press(page, "Tune", frequency="98.3") == "Would tune 98.3 MHz"
    assert asked == [{"action": "tune", "frequency": "98.3"}]
    _press(page, "Tune", frequency="88.8")
    assert asked[-1] == {"action": "tune", "frequency": "88.8"}
    _press(page, "Volume", volume="50")
    _press(page, "Stop")
    assert asked[-2:] == [{"action": "volume", "volume": "50"}, {"action": "stop"}]


def test_radio_tune_stop_and_volume_need_the_stick(monkeypatch):
    from shani_link import fm_radio
    from shani_link.app import tools_pages
    # rtl_fm is installed but no dongle: Tune, Stop and Volume stay greyed out
    monkeypatch.setattr(fm_radio, "hardware", lambda: "rtl_fm is installed (/x)\nno RTL2832U attached.")
    page = tools_pages.build_radio()
    pump(lambda: page.status() == "attention", 5)
    for label in ("Tune", "Stop", "Volume"):
        assert not page.buttons[label][0].get_sensitive(), label


def test_no_page_asks_for_a_typed_value_up_front(monkeypatch):
    """A person picks: presets, a file chooser, spin buttons. The only entry
    shown is the link to write to a sticker, which nothing could offer."""
    from shani_link import fm_radio, ir_remote, nfc
    from shani_link.app import tools_pages
    monkeypatch.setattr(fm_radio, "hardware", lambda: "nothing")
    monkeypatch.setattr(nfc, "readers", lambda: ([], "none"))
    monkeypatch.setattr(ir_remote, "device_report", lambda: ([], "none"))
    assert _typed_entries(tools_pages.build_radio()) == []
    assert _typed_entries(tools_pages.build_remote()) == []
    nfc_page = tools_pages.build_nfc()
    assert [e.get_placeholder_text() for e in _typed_entries(nfc_page)] == ["Link to write"]


def test_radio_plays_a_chosen_recording(monkeypatch, tmp_path):
    from shani_link import fm_radio
    from shani_link.app import tools_pages
    monkeypatch.setattr(fm_radio, "hardware", lambda: "nothing attached")
    asked = []
    monkeypatch.setattr(fm_radio, "run", lambda arguments: asked.append(arguments) or "Wrote x.wav")
    page = tools_pages.build_radio()
    recording = str(tmp_path / "fm.iq.wav")
    assert _press(page, "Play a recording", file=recording) == "Wrote x.wav"
    assert asked == [{"action": "decode", "file": recording}]
    assert page.buttons["Play a recording"][1]["file"].button.get_label() == "fm.iq.wav"


def test_nfc_says_why_there_is_no_reader_and_reads_through_the_engine(monkeypatch):
    from shani_link import nfc
    from shani_link.app import tools_pages
    found = {"readers": ([], "Cannot use an NFC reader: `nfc-scan-device` is not installed")}
    monkeypatch.setattr(nfc, "readers", lambda: found["readers"])
    asked = []
    monkeypatch.setattr(nfc, "_run", lambda arguments: asked.append(arguments) or "No tag was read: nothing was tapped")
    page = tools_pages.build_nfc()
    pump(lambda: "not installed" in _details(page), 5)
    assert "nfc-scan-device" in _details(page)
    assert any("No NFC reader plugged in" in t for t in texts(page))
    found["readers"] = (["ACR122U"], "")
    page = tools_pages.build_nfc()
    pump(lambda: page.status() == "ok", 5)
    assert "nothing was tapped" in _press(page, "Read", seconds="15")
    assert asked == [{"action": "read", "seconds": "15"}]


def _transmitter(monkeypatch, ir_remote, why=""):
    """A transmitter lircd drives, with one remote: no /dev/lirc* is opened."""
    monkeypatch.setattr(ir_remote, "send_abilities", lambda: {"send": why, "send-code": why})
    monkeypatch.setattr(ir_remote, "remotes", lambda: (["tv"], ""))
    monkeypatch.setattr(ir_remote, "keys", lambda remote: (["KEY_POWER", "KEY_MUTE"], ""))


def test_remote_sends_a_code_through_the_engine(monkeypatch):
    from shani_link import ir_remote
    from shani_link.app import tools_pages
    monkeypatch.setattr(ir_remote, "device_report", lambda: (["/dev/lirc0"], ""))
    _transmitter(monkeypatch, ir_remote)
    asked = []
    monkeypatch.setattr(ir_remote, "_run", lambda arguments: asked.append(arguments) or "Sent.")
    page = tools_pages.build_remote()
    pump(lambda: page.status() == "ok", 5)
    assert any("/dev/lirc0" in t for t in texts(page))
    assert _press(page, "Send a code", protocol="nec", address="4", command="8") == "Sent."
    assert asked == [{"action": "send", "protocol": "nec", "address": "4", "command": "8"}]


def test_remote_presses_a_button_lircd_listed(monkeypatch):
    """Remote and button are picked from irsend's lists; nothing is typed."""
    from shani_link import ir_remote
    from shani_link.app import tools_pages
    monkeypatch.setattr(ir_remote, "device_report", lambda: (["/dev/lirc0"], ""))
    _transmitter(monkeypatch, ir_remote)
    asked = []
    monkeypatch.setattr(ir_remote, "_run", lambda arguments: asked.append(arguments) or "Sent KEY_MUTE of tv.")
    page = tools_pages.build_remote()
    key = page.inputs["Send.key"]
    pump(lambda: [v for _l, v in key.options] == ["KEY_POWER", "KEY_MUTE"], 5)
    assert [label for label, _v in key.options] == ["Power", "Mute"]
    assert page.buttons["Send"][0].get_sensitive()
    assert _press(page, "Send", key="KEY_MUTE") == "Sent KEY_MUTE of tv."
    assert asked == [{"action": "send-key", "remote": "tv", "key": "KEY_MUTE"}]
    assert _typed_entries(page) == []


def test_send_is_greyed_out_without_a_transmitter(monkeypatch):
    from shani_link import ir_remote
    from shani_link.app import tools_pages
    monkeypatch.setattr(ir_remote, "device_report", lambda: (["/dev/lirc0"], ""))
    _transmitter(monkeypatch, ir_remote, why="no infrared transmitter: /dev/lirc0 can only receive")
    page = tools_pages.build_remote()
    pump(lambda: page.status() == "ok", 5)
    assert page.buttons["Receive"][0].get_sensitive(), "a receiver still receives"
    for label in ("Send", "Send a code"):
        button = page.buttons[label][0]
        assert not button.get_sensitive(), label
        assert "can only receive" in button.get_tooltip_text()


def test_an_engine_that_raises_is_a_sentence(monkeypatch):
    from shani_link import ir_remote
    from shani_link.app import tools_pages
    monkeypatch.setattr(ir_remote, "device_report", lambda: (["/dev/lirc0"], ""))
    _transmitter(monkeypatch, ir_remote)

    def boom(arguments):
        raise OSError("ir-ctl vanished")
    monkeypatch.setattr(ir_remote, "_run", boom)
    page = tools_pages.build_remote()
    pump(lambda: page.status() == "ok", 5)
    assert "ir-ctl vanished" in _press(page, "Receive")
    assert page.buttons["Receive"][0].get_sensitive()


# --- Background -------------------------------------------------------------

class _Switches:
    def __init__(self, **on):
        self.values = dict(on)
        self.writes = []

    def all(self):
        return dict(self.values)

    def get_bool(self, key, default=False):
        return self.values.get(key, default)

    def set_bool(self, key, on):
        self.writes.append((key, on))
        self.values[key] = on


def test_every_setting_has_a_switch_with_words(monkeypatch):
    from shani_link import settings
    from shani_link.app import background
    from shani_link.app.switches import LABELS
    assert set(LABELS) == set(settings.KEYS), "a setting with no words, or words for no setting"
    shown = background.keys_shown()
    assert sorted(shown + sorted(background.ELSEWHERE)) == sorted(settings.KEYS), (
        "every switch is on the page, or named as being somewhere else")
    assert len(shown) == len(set(shown))


def test_a_switch_shows_its_value_and_writes_only_when_turned(monkeypatch):
    from shani_link.app import background
    switches = _Switches(**{"watch-weather-enabled": True})
    page = background.build(type("App", (), {"switches": switches})())
    assert switches.writes == [], "drawing the page wrote a setting"
    weather = page.rows["watch-weather-enabled"].toggle
    calls = page.rows["bluetooth-call-enabled"].toggle
    assert weather.get_active() and not calls.get_active()
    calls.set_active(True)
    assert switches.writes == [("bluetooth-call-enabled", True)]
    assert page.status() == "ok"
    assert not any("Chronoa" in t for t in texts(page))


def test_with_everything_off_the_page_says_nothing_runs():
    from shani_link.app import background
    page = background.build(type("App", (), {"switches": _Switches()})())
    assert page.status() == "off"
    assert any("Nothing runs in the background" in t for t in texts(page))


def test_switches_fall_back_to_the_file_when_no_service_answers(tmp_path):
    """No bus (conftest points it nowhere): the switch lands in settings.json,
    which the service reads when it next starts."""
    from shani_link import settings
    from shani_link.app.switches import Switches
    store = settings.Settings(path=tmp_path / "settings.json")
    switches = Switches(store=store)
    switches.set_bool("phone-remote-enabled", True)
    assert settings.Settings(path=tmp_path / "settings.json").get_bool("phone-remote-enabled")
    assert switches.get_bool("phone-remote-enabled") is True
    switches.set("watch-weather-enabled", "true")
    assert store.get_bool("watch-weather-enabled")


def test_the_message_preview_is_not_cut_mid_cluster():
    """Moved from Chronoa's tests/test_clip.py: the Phone page's message preview
    goes through `common.clip`, which never leaves a joiner with no emoji."""
    from shani_link.app import phone as phone_panel
    family = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"
    preview = phone_panel._preview(f"see {family} " + "and a long message body " * 6)
    assert "..." in preview, preview
    assert preview.count("‍") % 3 == 0, preview
    assert phone_panel._preview("hi there") == "hi there"
    assert phone_panel._preview("") == "(no text)"
    from shani_link.app.common import clip
    assert clip("é" * 10, 3) == "é" * 0 + "..." or not clip("é" * 10, 3).endswith("e")
    assert clip("abc", 3) == "abc" and clip("abcdef", 5) == "ab..."


def test_the_watch_dashboard_draws_a_full_summary():
    """Moved from Chronoa's tests/test_charts.py with the dashboard: every card
    filled at once - stress readings included, which once recursed through
    `Line.set_times`."""
    from datetime import datetime, timedelta
    from shani_link.app.watch_dashboard import WatchDashboard
    now = datetime.now().replace(second=0, microsecond=0)
    night = now - timedelta(hours=8)
    summary = {
        "battery": 64, "step_goal": 10000,
        "steps_today": {"steps": 6842, "distance_m": 4920, "calories": 231},
        "steps_yesterday": {"steps": 11204, "distance_m": 8100, "calories": 402},
        "steps_2_days_ago": {"steps": 8310, "distance_m": 5990, "calories": 296},
        "sleep": [{"start": night, "end": night + timedelta(minutes=90), "stage": "deep", "minutes": 90}],
        "sleep_summary": {"asleep_minutes": 90, "deep_minutes": 90, "light_minutes": 0,
                          "awake_minutes": 0, "from": night, "to": night},
        "stress_today": [(now.replace(hour=9, minute=0), 40), (now.replace(hour=10, minute=0), 55)],
        "training": [{"type": "walk", "start": now, "active_seconds": 600, "steps": 900,
                      "calories": 40, "avg_hr": 100}],
        "device": {"manufacturer": "MOYOUNG-V2", "software": "MOY-7OT4-2.0.1"},
    }
    dash = WatchDashboard("AA:BB", "Watch")
    dash.show(summary)
    assert dash.stress_now.get_text() == "55"
    assert dash.spark.points == [(9.0, 40), (10.0, 55)]
    assert "MOYOUNG-V2" in dash.synced.get_text()
