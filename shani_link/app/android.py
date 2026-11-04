"""The Android tab: a USB-debugging phone, over adb.

**Neither desktop has a panel for this, and neither has any other app.** GNOME
Control Center's 26 panels and Plasma's KCM set were enumerated from the
installed packages rather than recalled — the same measurement that took the
Bluetooth and Sound candidates off the table for this session. A phone reached
over `adb` is not a wearable, not a Bluetooth peer and not a KDE Connect pair;
it is a device that happens to speak a protocol nobody has a settings page for.

**This tab adds no transport.** It asks `adb devices -l` and shows adb's own
answer. Moved from Shani Chronoa, where it went through the assistant's
`android_device` skill and its `phone-control-enabled` consent; here the person
opened the tab, which is the asking, so there is no switch in front of it.

**Read-mostly on purpose, and the panel says so rather than hiding it.** The
skill was built to a fixed set of verbs — devices, info, screenshot, apps, push,
pull, open_url — deliberately *not* taps and *not* `adb shell`. A person's phone
holds their messages, their photos and their location; a settings panel that
offered a shell prompt at it would be a very different thing from this. So there
is no `adb shell` button here.

**What a person does with a ready phone** sits under its row: battery and
storage, a screenshot saved to ~/Pictures, a link opened on the phone, a file
sent to the phone's Download folder, and the apps its owner installed. Each is
one verb of `shani_link.android` (the engine Chronoa's skill also calls), run
off the main loop, and its answer is shown as the sentence the engine says.

**The phone is the first consent, and it is not ours.** `adb` only reaches a
device whose owner turned on USB debugging and accepted this computer's key. A
machine with no such device is not a broken panel, it is a phone that has not
agreed to be spoken to.

**Nothing slow runs on the main thread.** `_adb` carries a 60-second timeout in
the module and `adb devices` plus an `info` read are two round trips; every read
here runs on a thread and hands its result back with `GLib.idle_add`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # type: ignore

from shani_link import android as engine
from shani_link.app import common

logger = logging.getLogger(__name__)

TITLE = "Android"
ICON = "drive-harddisk-usb-symbolic"
SUBTITLE = ("A phone connected with USB debugging - what it is, how much "
            "battery and storage it has, and a read-mostly way to move a file "
            "either way. Neither desktop has a panel for a phone reached over "
            "adb; the Bluetooth and Sound ones are elsewhere for a reason.")


STATE_PERMITTED = "permitted"
STATE_NO_ADB = "adb not installed"
STATE_NONE = "no phone connected"

HEADLINE = {
    STATE_PERMITTED: "Connected phones",
    STATE_NO_ADB: "adb is not installed",
    STATE_NONE: "No phone is connected with USB debugging",
}

#: Three of the four are "could not determine". None of them is a fault in the
#: machine, and none is a healthy reading either - a phone that has not agreed
#: to be spoken to is not the same as a phone that is working.
HEALTH = {
    STATE_NO_ADB: common.STATUS_UNKNOWN,
    STATE_NONE: common.STATUS_UNKNOWN,
}

BODY = {
    STATE_PERMITTED: "",
    # **No "Enable the switch in Settings".** The banner above names the
    # switch and turns it on from here, so pointing at a page is a second
    # way to say the same thing - and the old one sent the person away to
    # find a switch nobody had named.
    STATE_NO_ADB: (
        # **The package, because a refusal without a door is a dead end.** Named
        # from `adb(1)`'s own footer - *"Package name: extra/android-tools"* -
        # which is also what `files._PACKAGE_HINTS["adb"]` already answers, so
        # this names the package rather than restating the check.
        #
        # **"Shanios ships it" was verified rather than assumed**, and it is
        # `shani-chronoa`'s `optdepends` that is only an offer: `adb` reaches
        # every image because `shani-tools-extra` is in **Packages-Base of all
        # four profiles** and hard-depends on `android-tools`. So a machine
        # without it is not a normal Shanios install, which is why the sentence
        # says so rather than implying this is how the OS usually arrives.
        "`adb` is the tool that speaks to a phone over USB debugging. Every "
        "Shanios image has it - it comes in with the base tools - so this "
        "machine is missing something it should have; the package that "
        "provides it is android-tools, and without it there is no way to ask "
        "whether a phone is connected."),
    STATE_NONE: (
        "No device has USB debugging turned on and has accepted this "
        "computer's key. That is the phone owner's decision, and it is not "
        "something this page can change from here."),
}

#: state -> one clause for the status row, and **not** `BODY`. The row and the
#: empty state below it are two places saying one thing.
HEALTH_SUMMARY = {
    STATE_NO_ADB: "adb is not installed",
    STATE_NONE: "no phone is connected",
}



@dataclass(frozen=True)
class State:
    """The panel's own answer when there is no list to show.

    A property on the state rather than a word chosen at each call site, because
    the word is the claim and the state is the fact, and two places deciding it
    are two things that can disagree. `build()` reads it both for the status row
    and for the sidebar's dot, so those cannot drift.
    """

    state: str
    why: str = ""

    @property
    def health(self) -> str:
        return HEALTH.get(self.state, common.STATUS_UNKNOWN)

    @property
    def headline(self) -> str:
        return HEADLINE.get(self.state, self.state)

    @property
    def body(self) -> str:
        """The generic explanation, plus what *this* instance was refused for."""
        generic = BODY.get(self.state, "")
        # **A reason that names the switch is the banner's sentence.** The
        # banner above names it, says it is off and turns it on, so the
        # skill's own refusal - which says the same thing - would be a
        # second copy of it. A reason that does *not* name it is a
        # different fact ("this app has no settings to check it in"), and
        # that one is kept.
        if not self.why or self.why in generic:
            return generic or self.why or self.headline
        return f"{generic}\n\nThis time: {self.why}" if generic else self.why


def _ask_adb() -> str:
    """`adb devices -l`, as adb prints it - or why it could not be asked.

    The phrases "is not installed" and "No Android device" are the ones the
    classification below reads, the same words Chronoa's skill answered with.
    """
    import shutil
    import subprocess
    if shutil.which("adb") is None:
        return "adb (android-tools) is not installed."
    proc = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True,
                          timeout=60, check=False)
    out = (proc.stdout or "").strip()
    rows = [line for line in out.splitlines()[1:] if line.strip()]
    return out if rows else "No Android device is connected."


def _row(title: Any, subtitle: Any = "",
         suffix: Optional[Gtk.Widget] = None) -> Gtk.Widget:
    """One row.

    **Not pre-escaped.** `common.row()` escapes both the title and the subtitle
    itself, so escaping here as well renders an app named `Files & Docs` as
    `Files &amp; Docs` - which `devices.py` records having shipped in this repo's
    sibling panel.
    """
    return common.row(str(title), str(subtitle), suffix)


def _devices() -> Any:
    """Which phones adb can see, as the skill's own answer.

    **The skill's refusal text, kept whole.** `_run` distinguishes "adb is not
    installed" from "nothing is authorised" from "this device is unauthorised",
    and those are three different situations with three different remedies.
    Re-deriving which one happened here would be a second parser for a
    classification the skill has already made carefully.
    """
    return _ask_adb()


#: adb's device states, and the filter `adb(1)`'s own vocabulary justifies.
#:
#: `get-state` says *"Print offline | bootloader | device"*; `wait-for` says
#: *"STATE: device, recovery, rescue, sideload, bootloader, or disconnect"*;
#: and `reconnect offline` names the pair *"offline/unauthorized"*. `adb(1)`
#: nowhere documents `authorizing`, `connecting` or `host`, and those are words
#: adb is known to print while a link settles - so they are here because the
#: alternative is a refusal sentence read as a phone.
#:
#: **This filter exists because dropping the colon test exposed a worse bug.**
#: With a colon required, `adb is not installed - it is the tool that speaks to
#: a phone over USB debugging` was refused by accident. Without one, it parsed
#: as a phone named `adb` whose state was `is` - so a machine missing adb
#: entirely would have listed one device and reported "1 answering", which is
#: the absence-shaped wrong answer this file exists to refuse.
ADB_STATES = frozenset({
    "device", "offline", "unauthorized", "authorizing", "bootloader",
    "recovery", "sideload", "rescue", "disconnect", "connecting", "host",
    "no",  # adb prints "no permissions; see [http://...]" as the state
})


def _connected(answer: str) -> List[tuple]:
    """`(serial, state, model)` out of a `devices` answer, or `[]`.

    **The model is read because the skill asked for it.** `adb(1)` says of
    `devices -l` only *"Use long output"*, and the long form's extra fields are
    `model:`, `product:`, `device:` and `transport_id:` - `android_device`
    asks for `-l` and takes `model:` from it. A panel that discards the one
    field the caller requested `-l` for is asking for something and throwing it
    away, which is the same shape as the model row reading one table while the
    chip beside it read another.


    **The state is column 1, and this panel used to read the last field
    instead.** The skill asks for `devices -l`, so the real line is
    `R5CT10  unauthorized usb:1-1 model:Pixel_8 transport_id:3` - and
    `split()[-1]` on that is `transport_id:3`. The sentence about accepting the
    key never fired, and a phone the owner had not authorised read as
    `transport_id:3`. `android_device._devices()` reads column 1; so does this,
    because two answers to "what state is that phone in" is one too many.

    **The serial carries no guaranteed colon.** `adb(1)`, under `ADB_LIBUSB`:
    *"use `adb devices -l` (usb: prefix is omitted for libusb) to identify which
    is in use."* So the serial is `usb:1-1` on adb's own USB backend and `1-1`
    on libusb. Requiring a colon therefore reports **no phone at all** on a
    libusb system, which is what the first version of this function did - and
    which reads exactly like a machine with nothing plugged in.

    **Column 1 must be one of `ADB_STATES`.** The header check alone is not
    enough, and the reason is the bug this replaced: the skill's own refusal
    for a missing `adb` is a sentence of ordinary words, and it parses as a
    device line. Filtering on adb's own vocabulary is the classification the
    manual already made, rather than a second guess about what a line looks
    like.
    """
    out: List[tuple] = []
    for line in str(answer).splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] not in ADB_STATES:
            continue
        model = next((p.split(":", 1)[1] for p in parts[2:]
                      if p.startswith("model:")), "")
        out.append((parts[0], parts[1], model.replace("_", " ")))
    return out


def _device_row(serial: str, state: str, model: str = "") -> Gtk.Widget:
    """One phone, with adb's own word for it - and nothing more.

    **Three groups, from two manuals rather than from memory.**

    - `unauthorized` and `offline` are adb's *own* unreachables: `adb(1)`
      names both under `reconnect offline` - *"Reset offline/unauthorized
      devices to force reconnect"* - and each has something the phone's owner
      does about it.
    - `bootloader` is different, and **this panel used to say nothing about it**
      on the reasoning that a mode is not a fault. `fastboot(1)` is a second
      documented tool that exists precisely to reach a device in this state,
      with `fastboot continue` to *"Continue with autoboot"* out of it. So the
      state is not adb's word and nothing more: it is the one state where adb
      **cannot** talk to the phone and something else can.
    - everything else - `device`, `recovery`, `sideload`, `rescue`,
      `disconnect` (`adb(1) wait-for`) - keeps adb's bare word. Neither manual
      documents a second tool for any of them, and inventing a remedy is the
      absence-shaped wrong answer this file records.

    **The bootloader row names `fastboot` and stops.** `fastboot(1)` also
    carries `-w` (*"Erase userdata and cache"*), `erase`, `format`, `flash`,
    and `flashing unlock`. So a row saying "run fastboot" would be pointing at
    a tool that can empty the phone, which is why the sentence names the tool
    and says Shani Link does not drive it rather than telling anyone to use it.
    """
    if state == "unauthorized":
        subtitle = ("authorised by nobody - accept this computer's key on the "
                    "phone")
    elif state == "offline":
        subtitle = ("offline - the phone has USB debugging on but is not "
                    "answering")
    elif state == "bootloader":
        subtitle = ("in bootloader mode - adb cannot reach this phone, and "
                    "fastboot is the tool that can; Shani Link does not drive it")
    else:
        subtitle = state
    if model:
        subtitle = f"{model} - {subtitle}" if subtitle else model
    return _row(serial, subtitle)


class PhoneActions(Gtk.Box):
    """The buttons for one ready phone, each through `shani_link.android`.

    The engine is looked up when a button is pressed (`engine.info`, not a
    reference taken at build time), so a test that stubs the engine's verb
    stubs what the button runs.
    """

    def __init__(self, serial: str, model: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.serial = serial
        self.name = model or serial
        for side in ("start", "end"):
            getattr(self, f"set_margin_{side}")(12)
        buttons = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                              max_children_per_line=4, column_spacing=6, row_spacing=6)
        self.info_button = self._button("Battery and storage", self.on_info)
        self.shot_button = self._button("Take a screenshot", self.on_screenshot)
        self.send_button = self._button("Send a file...", self.pick_file)
        self.apps_button = self._button("List apps", self.on_apps)
        for button in (self.info_button, self.shot_button, self.send_button, self.apps_button):
            buttons.append(button)
        self.append(buttons)
        link = Gtk.Box(spacing=6)
        self.link_entry = Gtk.Entry(placeholder_text="https://...", hexpand=True)
        self.link_entry.connect("activate", lambda *_a: self.on_open_link())
        self.link_button = self._button("Open on the phone", self.on_open_link)
        link.append(self.link_entry)
        link.append(self.link_button)
        self.append(link)
        self.note = Gtk.Label(label="", xalign=0.0, wrap=True, selectable=True)
        self.note.add_css_class("dim-label")
        self.append(self.note)

    @staticmethod
    def _button(label: str, on_click) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda *_a: on_click())
        return button

    def say(self, text: str) -> None:
        self.note.set_text(str(text))

    def _do(self, busy: str, verb, *args) -> None:
        self.say(busy)
        common.in_thread(lambda: engine.do(verb, self.serial, *args),
                         lambda r: self.say(f"Could not reach the phone: {r}"
                                            if isinstance(r, Exception) else r),
                         name="link-android-action")

    def on_info(self) -> None:
        self._do(f"Asking {self.name}...", engine.info)

    def on_screenshot(self) -> None:
        self._do("Taking a screenshot - keep the phone unlocked...", engine.screenshot)

    def on_apps(self) -> None:
        self._do("Listing the apps...", engine.apps)

    def on_open_link(self) -> None:
        url = self.link_entry.get_text().strip()
        if not url:
            self.say("Type a link first, starting http:// or https://.")
            return
        self._do(f"Opening {url} on the phone...", engine.open_url, url)

    def send_file(self, path: str) -> None:
        import os
        self._do(f"Sending {os.path.basename(path)}...", engine.push, path)

    def pick_file(self) -> None:
        dialog = Gtk.FileDialog(title="Send to your phone", modal=True)
        root = self.get_root()

        def chosen(dlg, result) -> None:
            try:
                picked = dlg.open_finish(result)
            except GLib.Error as exc:
                if exc.code != Gtk.DialogError.DISMISSED:   # cancelled is silence
                    self.say(f"The file dialog did not open: {exc.message}")
                return
            if picked is not None and picked.get_path():
                self.send_file(picked.get_path())
        dialog.open(root if isinstance(root, Gtk.Window) else None, None, chosen)


def build(app) -> Gtk.Widget:
    """The phones adb can see for `app`, asked once at build time.

    Asked once, and never while the gate is shut - a permission that could not
    be checked is not a granted one.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    recorder = common.StatusRecorder()
    # **The recorder is attached to the page, not only written through.** The
    # sidebar's health dot is drawn from `page.status()`, and a panel that builds
    # a recorder without ever assigning it leaves the dot reading a page with no
    # answer - which is the defect `tests/test_surface_health_status.py` exists
    # for, and which this panel's own docstring about the sibling caught.
    page.status = recorder.status
    #: serial -> PhoneActions, for each phone adb reports ready
    page.actions = {}

    def gated(state: "State") -> Gtk.Widget:
        """An empty state, *with* the status row that matches its own word."""
        column = common.page_body(18)
        column.set_margin_top(12)
        column.append(recorder.row(state.health, state.headline,
                                   HEALTH_SUMMARY.get(state.state, "")))
        column.append(common.empty_state(
            ICON, state.headline, state.body,
            child=(common.connect_button(app, "android", "Connect an Android phone")
                   if state.state == STATE_NONE else None)))
        return common.scrolled(column)

    column = common.page_body(14)
    column.set_margin_top(12)
    set_content(common.scrolled(column))

    # **An interim row, because the dot answers at build time.** The read runs
    # on a thread and its real row arrives after it, but `page.status()` is
    # answerable the moment the page exists - and a recorder answering a word
    # with no row drawn for it is exactly what
    # `tests/test_surface_health_status.py`'s dot-agrees test catches. Measured:
    # on a machine with no adapter the read fails, `deliver` draws the row for
    # the failure, and before that the recorder answered `unknown` over an empty
    # panel. A sibling panel passed the same test by finishing first, which is a
    # race rather than a property.
    recorder.set(common.STATUS_UNKNOWN)
    interim = recorder.row(
        common.STATUS_UNKNOWN, "Reading connected phones",
        "asking the machine what is connected")
    column.append(interim)

    def deliver(widget: Gtk.Widget, health: str, headline: str,
                summary: str) -> None:
        column.append(widget)
        recorder.set(health)
        column.append(recorder.row(health, headline, summary))
        column.remove(interim)

    def when_ready(answer: Any) -> None:
        if isinstance(answer, Exception):
            deliver(gated(State(state=STATE_NO_ADB, why=str(answer))),
                    common.STATUS_UNKNOWN, HEADLINE[STATE_NO_ADB],
                    HEALTH_SUMMARY[STATE_NO_ADB])
            return
        text = str(answer)
        listing = common.page_body(8)
        devices = _connected(text)
        if not devices:
            # **Two states, chosen by which refusal the skill actually sent.**
            # `_run` separates "adb is not installed" from "nothing connected",
            # and those need different remedies - one is a missing tool on this
            # machine and the other is a phone whose owner has not agreed to be
            # spoken to. Deciding it here would be a second classifier for a
            # distinction the skill has already made carefully, and getting it
            # wrong would report a broken machine where there is only no phone.
            state = (STATE_NO_ADB if "not installed" in text.lower()
                     else STATE_NONE)
            deliver(gated(State(state=state, why=text.strip())),
                    common.STATUS_UNKNOWN, HEADLINE[state],
                    HEALTH_SUMMARY[state])
            return
        listing.append(_row("Connected phones",
                            f"{len(devices)} answering over USB debugging"))
        for serial, state, model in devices:
            listing.append(_device_row(serial, state, model))
            if state == "device":
                actions = PhoneActions(serial, model)
                page.actions[serial] = actions
                listing.append(actions)
        deliver(listing, common.STATUS_OK, "Connected phones",
                f"{len(devices)} answering")

    def on_devices() -> None:
        GLib.idle_add(when_ready, _devices())

    GLib.Thread.new("link-android-devices", lambda _d: on_devices(), ())
    return page