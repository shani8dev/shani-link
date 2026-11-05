"""Radio, NFC and Remote: three small pages over engines that already speak.

Each is the same shape: what this computer has (the engine's own report, read
on a thread when the page opens), then the few things the engine can do, each a
button with the choices it needs, and the engine's own sentence under it. No
second parser of what the hardware said - the engines already explain "the
tool is missing" apart from "the hardware is missing", and the page shows that.

**Nothing has to be typed.** A frequency is picked from a list of FM
frequencies, a recording from a file chooser, a wait from a few lengths, an
infrared code from spin buttons; a typed value is only the "Other..." path.
A link to write to a sticker is the one thing a person types (or pastes).

**When the hardware is missing the page says so in plain words** - what to
plug in - and the engine's own sentence (device nodes, package names) is folded
under "Details". The buttons that need the hardware are greyed out until it is
there, so nothing on the page is a button that can only refuse; what works
without it (playing a recording, the bands) stays live.

The engines (`fm_radio`, `nfc`, `ir_remote`) were the assistant's skills in
Shani Chronoa; the assistant's consent keys (`fm-radio-enabled`, `nfc-enabled`,
`ir-remote-enabled`) stayed there. A person pressing Read or Send is the asking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from shani_link.app import common  # noqa: E402

OTHER = "Other..."


@dataclass
class Field:
    """One value an action takes, and how it is chosen.

    kind: "choice" (a list of (label, value), plus "Other..." for a typed one
    when `custom`), "file" (a file chooser), "number" (a spin button),
    "text" (typed or pasted - only where nothing could be offered).
    """
    key: str
    label: str
    kind: str = "choice"
    options: Sequence[Tuple[str, str]] = ()
    custom: bool = False
    placeholder: str = ""
    low: int = 0
    high: int = 255


@dataclass
class Action:
    label: str
    what: str
    fields: List[Field] = field(default_factory=list)
    run: Callable[[Dict[str, str]], str] = lambda values: ""
    #: greyed out until the page's hardware report says it is ready
    needs_hardware: bool = True


class _Input:
    """A field's widget and how to read its value back."""

    def __init__(self, spec: Field) -> None:
        self.spec = spec
        self.path = ""
        self.widget: Gtk.Widget
        if spec.kind == "choice":
            box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            names = [label for label, _v in spec.options] + ([OTHER] if spec.custom else [])
            self.dropdown = Gtk.DropDown.new_from_strings(names)
            self.dropdown.set_valign(Gtk.Align.CENTER)
            self.dropdown.update_property([Gtk.AccessibleProperty.LABEL], [spec.label])
            box.append(self.dropdown)
            self.entry = Gtk.Entry(placeholder_text=spec.placeholder, width_chars=8, visible=False)
            self.entry.update_property([Gtk.AccessibleProperty.LABEL], [spec.label])
            box.append(self.entry)
            self.dropdown.connect("notify::selected", lambda *_a: self.entry.set_visible(self._other()))
            self.widget = box
        elif spec.kind == "file":
            self.button = Gtk.Button(label="Choose a file...", valign=Gtk.Align.CENTER)
            self.button.update_property([Gtk.AccessibleProperty.LABEL], [spec.label])
            self.button.connect("clicked", lambda _b: self._choose())
            self.widget = self.button
        elif spec.kind == "number":
            self.spin = Gtk.SpinButton.new_with_range(spec.low, spec.high, 1)
            self.spin.set_valign(Gtk.Align.CENTER)
            self.spin.set_value(spec.low)
            self.spin.update_property([Gtk.AccessibleProperty.LABEL], [spec.label])
            self.widget = self.spin
        else:
            box = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER)
            box.add_css_class("linked")
            self.entry = Gtk.Entry(placeholder_text=spec.placeholder, width_chars=18)
            self.entry.update_property([Gtk.AccessibleProperty.LABEL], [spec.label])
            box.append(self.entry)
            paste = Gtk.Button(icon_name="edit-paste-symbolic", tooltip_text="Paste")
            paste.update_property([Gtk.AccessibleProperty.LABEL], [f"Paste the {spec.label.lower()}"])
            paste.connect("clicked", lambda _b: self._paste())
            box.append(paste)
            self.widget = box

    def _other(self) -> bool:
        return self.spec.custom and self.dropdown.get_selected() == len(self.spec.options)

    def _choose(self) -> None:
        dialog = Gtk.FileDialog(title=self.spec.label, modal=True)
        root = self.widget.get_root()

        def chosen(dlg, result) -> None:
            try:
                picked = dlg.open_finish(result)
            except GLib.Error:
                return                       # cancelled: nothing changes
            if picked is not None and picked.get_path():
                self.set_value(picked.get_path())
        dialog.open(root if isinstance(root, Gtk.Window) else None, None, chosen)

    def _paste(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return

        def done(clip, result) -> None:
            try:
                text = clip.read_text_finish(result)
            except GLib.Error:
                return
            if text:
                self.entry.set_text(text.strip())
        display.get_clipboard().read_text_async(None, done)

    def set_value(self, value: str) -> None:
        """Put `value` in the field (what a test, or a learned code, does)."""
        kind = self.spec.kind
        if kind == "choice":
            values = [v for _l, v in self.spec.options]
            if value in values:
                self.dropdown.set_selected(values.index(value))
            elif self.spec.custom:
                self.dropdown.set_selected(len(values))
                self.entry.set_text(value)
        elif kind == "file":
            import os
            self.path = value
            self.button.set_label(os.path.basename(value) or "Choose a file...")
        elif kind == "number":
            self.spin.set_value(int(str(value), 0))
        else:
            self.entry.set_text(value)

    def value(self) -> str:
        kind = self.spec.kind
        if kind == "choice":
            if self._other():
                return self.entry.get_text().strip()
            options = self.spec.options
            index = self.dropdown.get_selected()
            return options[index][1] if 0 <= index < len(options) else ""
        if kind == "file":
            return self.path
        if kind == "number":
            return str(int(self.spin.get_value()))
        return self.entry.get_text().strip()


def _action_page(title: str, subtitle: str, icon: str,
                 report: Callable[[], "tuple[bool, str]"],
                 actions: List[Action], missing: "tuple[str, str]",
                 ready_words: str = "") -> Gtk.Widget:
    """A page: the hardware report, then one row per action.

    `run(values)` is called on a worker with the chosen values and returns the
    engine's sentence. `report()` answers `(ready, the engine's own words)`;
    `missing` is (headline, what to plug in) for a person, shown when not ready.
    """
    page, set_content = common.surface(title, subtitle)
    recorder = common.StatusRecorder()
    page.status = recorder.status
    body = common.page_body(12)
    for side in ("start", "end"):
        getattr(body, f"set_margin_{side}")(12)

    status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    status_slot.append(common.loading("Checking this computer..."))
    body.append(status_slot)
    page.buttons = {}
    page.inputs = {}

    def reported(result: Any) -> None:
        common.clear(status_slot)
        if isinstance(result, Exception):
            ready, text = False, f"Could not ask: {result}"
        else:
            ready, text = result
        page.ready = bool(ready)
        lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()] or ["No answer"]
        if ready and ready_words:
            # the person's words in front, the engine's (paths, USB ids) under Details
            status_slot.append(recorder.row(common.STATUS_OK, ready_words))
            status_slot.append(common.details("Details", "\n".join(lines)))
        elif ready:
            status_slot.append(recorder.row(common.STATUS_OK, lines[0], " - ".join(lines[1:])[:400]))
        else:
            recorder.set(common.STATUS_ATTENTION)
            headline, hint = missing
            card = common.empty_state(icon, headline, hint, inline=True)
            card.set_vexpand(False)
            card.set_margin_top(6)
            card.set_margin_bottom(6)
            status_slot.append(card)
            status_slot.append(common.details("Details", "\n".join(lines)))
        for label, (button, _inputs) in page.buttons.items():
            if getattr(button, "needs_hardware", False):
                button.set_sensitive(bool(ready))
                button.set_tooltip_text("" if ready else headline_for(missing))
    page.ready = False

    def headline_for(pair: "tuple[str, str]") -> str:
        return pair[0]

    page.reload = lambda: common.in_thread(report, reported, name=f"link-{title.lower()}")

    output = Gtk.Label(xalign=0.0, wrap=True, selectable=True)
    output.add_css_class("dim-label")
    output.set_margin_start(12)
    output.set_margin_end(12)
    page.output = output

    group = common.group("What it can do")
    for action in actions:
        inputs: Dict[str, _Input] = {}
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        for spec in action.fields:
            made = _Input(spec)
            inputs[spec.key] = made
            box.append(made.widget)
        button = Gtk.Button(label=action.label, valign=Gtk.Align.CENTER)
        button.needs_hardware = action.needs_hardware
        if action.needs_hardware:
            button.set_sensitive(False)          # until the report says the hardware is there

        def clicked(_b, action=action, inputs=inputs, button=button) -> None:
            values = {k: i.value() for k, i in inputs.items()}
            values = {k: v for k, v in values.items() if v}
            button.set_sensitive(False)
            output.set_text(f"{action.label}...")

            def done(result: Any) -> None:
                button.set_sensitive(True)
                output.set_text(f"{action.label} failed: {result}" if isinstance(result, Exception)
                                else str(result))
            common.in_thread(lambda: action.run(values), done, name=f"link-{action.label.lower()}")
        button.connect("clicked", clicked)
        box.append(button)
        page.buttons[action.label] = (button, inputs)
        page.inputs.update({f"{action.label}.{k}": i for k, i in inputs.items()})
        row = common.row(action.label, action.what, suffix=box)
        group.add(row) if hasattr(group, "add") else group.append(row)
    body.append(group)
    body.append(output)
    set_content(common.scrolled(body))
    page.reload()
    return page


# --- FM radio ----------------------------------------------------------------

#: FM frequencies to pick from (MHz). Frequencies, not station names: which
#: station is on which frequency depends on the city, and a wrong name is worse
#: than a number.
FM_PRESETS = ("91.1", "91.9", "92.7", "93.5", "94.3", "95.0", "98.3", "100.1",
              "101.0", "102.8", "104.0", "104.8", "106.4", "107.2")


def build_radio(app: Any = None) -> Gtk.Widget:
    from shani_link import fm_radio

    def run(action: str) -> Callable[[Dict[str, str]], str]:
        return lambda values: fm_radio.run({"action": action, **values})

    return _action_page(
        "Radio", "FM radio with a USB radio stick, or played back from a recording "
        "of one.", "audio-speakers-symbolic",
        lambda: (lambda text: ("RTL2832U dongle is attached" in text
                               and "is installed" in text, text))(fm_radio.hardware()),
        [Action("Tune", "Get a station ready (live listening is not built yet: "
                "it shows how it would tune)",
                [Field("frequency", "Frequency", options=[(f"{f} MHz", f) for f in FM_PRESETS],
                       custom=True, placeholder="MHz")], run("tune")),
         Action("Play a recording", "Turn a recorded FM capture (.wav) into sound",
                [Field("file", "Recording", kind="file")], run("decode"), needs_hardware=False),
         Action("Find stations", "Look for stations in a recording of the whole band",
                [Field("file", "Recording", kind="file")], run("scan"), needs_hardware=False),
         Action("Bands", "What a radio stick can receive", [], run("bands"), needs_hardware=False)],
        ("No radio stick plugged in",
         "Plug in a USB radio stick (an RTL-SDR, RTL2832U) to tune FM here. "
         "You can still play a recording below."),
        ready_words="A radio stick is plugged in")


# --- NFC ---------------------------------------------------------------------

def build_nfc(app: Any = None) -> Gtk.Widget:
    from shani_link import nfc

    def report() -> "tuple[bool, str]":
        found, why = nfc.readers()
        return (True, "NFC reader(s) found: " + ", ".join(found)) if found else (False, why)

    def run(action: str) -> Callable[[Dict[str, str]], str]:
        return lambda values: nfc._run({"action": action, **values})

    return _action_page(
        "NFC", "Read an NFC tag, or put a link on an NFC sticker, with a USB "
        "NFC reader.", "auth-smartcard-symbolic",
        report,
        [Action("Read", "Hold a tag on the reader",
                [Field("seconds", "How long to wait",
                       options=[("Wait 6 s", "6"), ("Wait 15 s", "15"), ("Wait 30 s", "30")])],
                run("read")),
         Action("Write", "Put a link on a sticker (NTAG or Ultralight)",
                [Field("url", "Link", kind="text", placeholder="Link to write")], run("write"))],
        ("No NFC reader plugged in",
         "Plug in a USB NFC reader (one that works with libnfc, such as an "
         "ACR122U) to read tags and write stickers."))


# --- infrared remote ------------------------------------------------------------

def build_remote(app: Any = None) -> Gtk.Widget:
    from shani_link import ir_remote

    def run(action: str) -> Callable[[Dict[str, str]], str]:
        return lambda values: ir_remote._run({"action": action, **values})

    protocols = [(p.upper(), p) for p in ir_remote.PROTOCOLS]
    return _action_page(
        "Remote", "Learn the buttons of an infrared remote control with an "
        "infrared receiver.", "input-gaming-symbolic",
        lambda: (lambda found, why: (bool(found), why or "Infrared is available on this machine: "
                                     + ", ".join(found)))(*ir_remote.device_report()),
        [Action("Receive", "Point a remote at the receiver, press Receive, then "
                "press a button on the remote", [], run("receive")),
         Action("Send", "Get a button's code ready (sending is not built yet: "
                "it shows the code it would send)",
                [Field("protocol", "Kind of remote", options=protocols),
                 Field("address", "Address", kind="number"),
                 Field("command", "Button", kind="number")], run("send"))],
        ("No infrared device found",
         "Plug in a USB infrared receiver to learn the buttons of your "
         "remote controls."))
