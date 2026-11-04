"""Radio, NFC and Remote: three small pages over engines that already speak.

Each is the same shape: what this computer has (the engine's own report, read
on a thread when the page opens), then the few things the engine can do, each a
button with the fields it needs, and the engine's own sentence under it. No
second parser of what the hardware said - the engines already explain "the
tool is missing" apart from "the hardware is missing", and the page shows that.

The engines (`fm_radio`, `nfc`, `ir_remote`) were the assistant's skills in
Shani Chronoa; the assistant's consent keys (`fm-radio-enabled`, `nfc-enabled`,
`ir-remote-enabled`) stayed there. A person pressing Read or Send is the asking.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_link.app import common  # noqa: E402

#: (label, placeholder) for each field an action takes
Field = Tuple[str, str]


def _action_page(title: str, subtitle: str, icon: str,
                 report: Callable[[], "tuple[bool, str]"],
                 actions: List[Tuple[str, str, List[Tuple[str, str, str]], Callable[[Dict[str, str]], str]]],
                 ) -> Gtk.Widget:
    """A page: the hardware report, then one row per action.

    `actions` is `[(button label, what it does, [(key, label, placeholder)], run)]`;
    `run(values)` is called on a worker with the typed fields and returns the
    engine's sentence. `report()` answers `(ready, the engine's own words)`.
    """
    page, set_content = common.surface(title, subtitle)
    recorder = common.StatusRecorder()
    page.status = recorder.status
    body = common.page_body(12)
    for side in ("start", "end"):
        getattr(body, f"set_margin_{side}")(12)

    status = common.group("This computer")
    status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    status.add(status_slot) if hasattr(status, "add") else status.append(status_slot)
    status_slot.append(recorder.row(common.STATUS_UNKNOWN, "Looking", "asking the hardware"))
    body.append(status)

    def reported(result: Any) -> None:
        common.clear(status_slot)
        if isinstance(result, Exception):
            ready, text = False, f"Could not ask: {result}"
        else:
            ready, text = result
        lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()] or ["No answer"]
        status_slot.append(recorder.row(common.STATUS_OK if ready else common.STATUS_ATTENTION,
                                        lines[0], " - ".join(lines[1:])[:400]))

    page.reload = lambda: common.in_thread(report, reported, name=f"link-{title.lower()}")
    page.reload()

    output = Gtk.Label(xalign=0.0, wrap=True, selectable=True)
    output.add_css_class("dim-label")
    output.set_margin_start(12)
    output.set_margin_end(12)
    page.output = output
    page.buttons = {}

    group = common.group("What it can do")
    for label, what, fields, run in actions:
        entries: Dict[str, Gtk.Entry] = {}
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        for key, name, placeholder in fields:
            entry = Gtk.Entry(placeholder_text=placeholder, width_chars=max(8, len(placeholder)))
            entry.update_property([Gtk.AccessibleProperty.LABEL], [name])
            entries[key] = entry
            box.append(entry)
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)

        def clicked(_b, run=run, entries=entries, label=label, button=button) -> None:
            values = {k: e.get_text().strip() for k, e in entries.items()}
            button.set_sensitive(False)
            output.set_text(f"{label}...")

            def done(result: Any) -> None:
                button.set_sensitive(True)
                output.set_text(f"{label} failed: {result}" if isinstance(result, Exception) else str(result))
            common.in_thread(lambda: run(values), done, name=f"link-{label.lower()}")
        button.connect("clicked", clicked)
        box.append(button)
        page.buttons[label] = (button, entries)
        row = common.row(label, what, suffix=box)
        group.add(row) if hasattr(group, "add") else group.append(row)
    body.append(group)
    body.append(output)
    set_content(common.scrolled(body))
    return page


# --- FM radio ----------------------------------------------------------------

def build_radio(app: Any = None) -> Gtk.Widget:
    from shani_link import fm_radio

    def run(action: str) -> Callable[[Dict[str, str]], str]:
        return lambda values: fm_radio.run({"action": action, **{k: v for k, v in values.items() if v}})

    return _action_page(
        "Radio", "FM broadcast radio through an RTL-SDR dongle, demodulated on this "
        "computer - or from a recording of one.", "audio-speakers-symbolic",
        lambda: (lambda text: ("RTL2832U dongle is attached" in text
                               and "is installed" in text, text))(fm_radio.hardware()),
        [("Tune", "Listen to a station (MHz)", [("frequency", "Frequency", "98.3")], run("tune")),
         ("Decode", "Demodulate a recorded IQ capture (.wav)", [("file", "File", "~/fm.iq.wav")], run("decode")),
         ("Scan", "Find the stations in a whole-band capture", [("file", "File", "~/band.iq.wav")], run("scan")),
         ("Bands", "What this receiver could cover", [], run("bands"))])


# --- NFC ---------------------------------------------------------------------

def build_nfc(app: Any = None) -> Gtk.Widget:
    from shani_link import nfc

    def report() -> str:
        found, why = nfc.readers()
        return (True, "NFC reader(s) found: " + ", ".join(found)) if found else (False, why)

    def run(action: str) -> Callable[[Dict[str, str]], str]:
        return lambda values: nfc._run({"action": action, **{k: v for k, v in values.items() if v}})

    return _action_page(
        "NFC", "Read an NFC tag, or write a link to an NFC sticker, with a USB "
        "reader (libnfc).", "auth-smartcard-symbolic",
        report,
        [("Read", "Hold a tag on the reader", [("seconds", "Seconds to wait", "6")], run("read")),
         ("Write", "Write a link to an Ultralight/NTAG sticker", [("url", "Link", "https://example.org")], run("write"))])


# --- infrared remote ------------------------------------------------------------

def build_remote(app: Any = None) -> Gtk.Widget:
    from shani_link import ir_remote

    def run(action: str) -> Callable[[Dict[str, str]], str]:
        return lambda values: ir_remote._run({"action": action, **{k: v for k, v in values.items() if v}})

    return _action_page(
        "Remote", "Send and receive infrared remote-control codes (NEC, RC-6) "
        "through this computer's IR transmitter or receiver.", "input-gaming-symbolic",
        lambda: (lambda found, why: (bool(found), why or "Infrared is available on this machine: "
                                     + ", ".join(found)))(*ir_remote.device_report()),
        [("Send", "Send one code", [("protocol", "Protocol", "nec"), ("address", "Address", "0x04"),
                                    ("command", "Command", "0x08")], run("send")),
         ("Receive", "Point a remote at the receiver and press a button", [], run("receive")),
         ("Decode", "Name a code from ir-ctl's timings", [("timings", "Timings", "pulse 9000 space 4500 ...")],
          run("decode"))])
