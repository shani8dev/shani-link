#!/usr/bin/env python3
"""Render Shani Link's pages to PNG, for the README (docs/screenshots/).

    python3 tools/screenshots.py [OUTDIR]        # default: docs/screenshots

**Never on the person's desktop.** The script starts its own `gtk4-broadwayd`
on a free display, in a private runtime folder, and re-runs itself inside a
clean environment: a temporary HOME and XDG folders, both D-Bus buses pointed
nowhere, GSettings in memory. Nothing reaches the real phone, Bluetooth, the
landline or `~/.config`.

**Sample data, not a real phone.** The address book (vCards, through
`shani_link.contacts`) and the call history (`shani_link.calls_log`) are
written into the temporary XDG folders with made-up people. A phone, its texts,
a paired watch and an Android device are stood in for at the names the pages
call - the same way the app's GTK tests stub engines (AGENTS.md) - so a page
shows what it looks like with a phone connected, not an empty state. The
empty states are rendered too (`*-empty.png`), from a second window with no
stand-ins.

Each picture is the window drawn through `Gtk.WidgetPaintable` and the
window's own renderer, so it is exactly what GTK paints.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 1100, 760
#: The pages whose first-run state (nothing connected) is pictured too.
EMPTY_PAGES = ("phone", "watch", "devices", "radio")


# --- the outer process: a private display and a clean environment ---------------

def _free_display() -> int:
    for n in range(5300, 6000):
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("::", 8080 + n))
            except OSError:
                continue
        return n
    raise SystemExit("no free Broadway display")


def outer(outdir: Path) -> int:
    daemon = shutil.which("gtk4-broadwayd")
    if not daemon:
        raise SystemExit("gtk4-broadwayd is not installed (package: gtk4 / libgtk-4-bin)")
    tmp = Path(tempfile.mkdtemp(prefix="shani-link-shots-"))
    tmp.chmod(0o700)
    number = _free_display()
    env_d = {"XDG_RUNTIME_DIR": str(tmp)}
    proc = subprocess.Popen([daemon, f":{number}"], env={**os.environ, **env_d},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not (tmp / f"broadway{number + 1}.socket").exists():
            time.sleep(0.05)
        env = {
            "PATH": "/usr/bin:/bin", "HOME": str(tmp), "LANG": "C.UTF-8",
            "XDG_CONFIG_HOME": str(tmp / "c"), "XDG_DATA_HOME": str(tmp / "d"),
            "XDG_STATE_HOME": str(tmp / "s"), "XDG_CACHE_HOME": str(tmp / "x"),
            "XDG_RUNTIME_DIR": str(tmp), "PYTHONPATH": str(ROOT),
            "GDK_BACKEND": "broadway", "BROADWAY_DISPLAY": f":{number}",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={tmp}/none",
            "DBUS_SYSTEM_BUS_ADDRESS": f"unix:path={tmp}/none",
            "GSETTINGS_BACKEND": "memory", "SHANI_LINK_SHOTS_INNER": "1",
            "SHOTS": os.environ.get("SHOTS", ""),
        }
        outdir.mkdir(parents=True, exist_ok=True)
        return subprocess.call([sys.executable, str(Path(__file__).resolve()), str(outdir.resolve())],
                               env=env, cwd=str(tmp), timeout=600)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


# --- sample data -------------------------------------------------------------------

#: (name, number, email, org, birthday) - made up
PEOPLE = (
    ("Aarav Sharma", "+91 98200 14523", "aarav.sharma@example.in", "", "1990-04-12"),
    ("Priya Iyer", "+91 99870 33210", "priya@example.in", "Iyer Architects", ""),
    ("Meera Kulkarni", "+91 94220 77815", "", "", "--08-23"),
    ("Rohan Deshpande", "+91 020 2565 4411", "rohan.d@example.in", "Pune Cycles", ""),
    ("Fatima Sheikh", "+91 98190 60042", "fatima@example.org", "", ""),
    ("Lukas Weber", "+49 151 2345 6789", "lukas.weber@example.de", "", "1985-11-02"),
    ("Sofia Rossi", "+39 347 555 0192", "sofia.rossi@example.it", "", ""),
    ("Kenji Watanabe", "+81 90 1234 5678", "", "Watanabe Studio", ""),
    ("Grace Okafor", "+44 7700 900123", "grace@example.co.uk", "", ""),
)

#: phone-only contacts (the phone's own book, read over Bluetooth)
PHONE_BOOK = (("Amma", ("+91 98220 11111",)), ("Dr. Joshi Clinic", ("+91 20 2612 3456",)),
              ("Vikram (office)", ("+91 98330 45678",)))


def seed() -> None:
    from shani_link import calls_log, contacts
    book = contacts.book()
    for name, number, email, org, bday in PEOPLE:
        given, _, family = name.partition(" ")
        book.save(contacts.Contact(full_name=name, given=given, family=family,
                                   phones=[("cell", number)],
                                   emails=[("home", email)] if email else [],
                                   org=org, birthday=bday))
    now = time.time()
    hour = 3600
    for line, who, direction, answered, seconds, ago in (
            ("landline", "+91 98200 14523", "in", True, 312, 0.4 * hour),
            ("phone", "+91 99870 33210", "out", True, 95, 1.5 * hour),
            ("modem", "+49 151 2345 6789", "in", False, 0, 3 * hour),
            ("phone", "+91 98220 11111", "in", True, 1260, 26 * hour),
            ("landline", "+91 020 2565 4411", "out", True, 48, 28 * hour),
            ("phone", "+91 80 4567 8901", "in", False, 0, 30 * hour),
            ("modem", "+44 7700 900123", "out", True, 610, 50 * hour),
            ("landline", "+91 94220 77815", "in", True, 175, 52 * hour)):
        calls_log.add(line, who, direction, answered, seconds=seconds, when=now - ago)


def stand_ins() -> None:
    """A phone, a watch and an Android device, at the names the pages call."""
    from shani_link import phone as ph
    from shani_link.app import phone as phone_page
    now_ms = int(time.time() * 1000)
    me = ph.Device("AA:BB:CC:DD:EE:01", "Pixel 8", True, True)
    msgs = [
        ph.Message("1", ("+91 99870 33210",), "Reached the site, sending photos in a bit", now_ms - 600_000, True, False),
        ph.Message("2", ("+91 98220 11111",), "Did you eat? Call me when you are free", now_ms - 5_400_000, True, True),
        ph.Message("3", ("+49 151 2345 6789",), "Flight lands at 6:40, see you at arrivals", now_ms - 86_400_000, True, True),
        ph.Message("4", ("AX-HDFCBK",), "Your OTP is never shared by the bank. Txn of Rs 1,250 at Pune Cycles", now_ms - 90_000_000, True, True),
    ]
    thread = [
        ph.Message("1", ("+91 99870 33210",), "Are you at the site today?", now_ms - 3_600_000, False, True),
        ph.Message("1", ("+91 99870 33210",), "Yes, after lunch", now_ms - 3_000_000, True, True),
        ph.Message("1", ("+91 99870 33210",), "Reached the site, sending photos in a bit", now_ms - 600_000, True, False),
    ]
    ph.backend = lambda: "bluetooth"
    ph.devices = lambda: [me]
    ph.battery = lambda _d: (78, False)
    ph.conversations = lambda _d: msgs
    ph.thread_messages = lambda _d, _t, _n=10: thread
    ph.contacts = lambda _d: list(PHONE_BOOK)
    phone_page.PhonePanel.show_route = lambda self: None
    phone_page._media = lambda action: "Playing \u201cMonsoon Evenings\u201d by The Sample Band, on Pixel 8."

    # a radio stick, an NFC reader and an infrared receiver
    from shani_link import fm_radio, ir_remote, nfc
    fm_radio.hardware = lambda: ("rtl_fm is installed (/usr/bin/rtl_fm)\nan RTL2832U dongle is attached:\n"
                                 "  Bus 001 Device 004: ID 0bda:2838 Realtek RTL2838UHIDIR")
    nfc.readers = lambda: (["ACS ACR122U PICC Interface"], "")
    ir_remote.device_report = lambda: (["/dev/lirc0"], "")
    ir_remote.send_abilities = lambda: {"send": "", "send-code": ""}
    ir_remote.remotes = lambda: (["tv"], "")
    ir_remote.keys = lambda _remote: (["KEY_POWER", "KEY_VOLUMEUP", "KEY_MUTE"], "")

    # an Android phone on USB, and the Wi-Fi phone link paired
    from shani_link.app import android as android_page
    android_page._ask_adb = lambda: ("List of devices attached\n"
                                     "R5CT10XYZ  device usb:1-1 product:a54 model:Galaxy_A54 transport_id:1")
    from shani_link import lan_link, lan_phone
    lan_link.pairing_status = lambda: ("paired", "with Pixel 8 at 192.168.1.23")
    lan_phone.default_host = lambda: "192.168.1.23"

    # the watch: one MoYoung watch, its dashboard filled from a sample summary
    from datetime import datetime, timedelta
    from shani_link import moyoung
    from shani_link.app import wearables
    watch = ("C4:11:22:33:44:55", "FB BGS002")
    wearables._paired = lambda: ([watch], None)
    from shani_link import gatt
    gatt.services = lambda _mac: []
    gatt.attributes = lambda _mac: []
    wearables._moyoung_watches = lambda: [watch]
    today = datetime.now().replace(second=0, microsecond=0)
    bed = (today - timedelta(days=1)).replace(hour=23, minute=10)
    stages = [("light", 40), ("deep", 55), ("light", 70), ("awake", 6), ("deep", 48),
              ("light", 95), ("awake", 4), ("light", 52)]
    sleep, t = [], bed
    for stage, minutes in stages:
        sleep.append({"stage": stage, "minutes": minutes, "start": t})
        t += timedelta(minutes=minutes)
    # readings every half hour through the day so far (the chart is of today)
    start = today.replace(hour=0, minute=30)
    levels = (22, 18, 15, 14, 16, 20, 25, 31, 35, 42, 48, 41, 37, 45, 57, 50, 38, 33, 29, 36)
    stress, at = [], start
    while at <= today:
        stress.append((at, levels[len(stress) % len(levels)]))
        at += timedelta(minutes=30)
    summary = {
        "device": {"manufacturer": "MoYoung", "software": "MOY-BGS2-1.7.4"},
        "battery": 64, "step_goal": 8000,
        "steps_today": {"steps": 6240, "distance_m": 4380, "calories": 231},
        "steps_yesterday": {"steps": 9120}, "steps_2_days_ago": {"steps": 7310},
        "sleep": sleep,
        "sleep_summary": {"asleep_minutes": 360, "deep_minutes": 103, "light_minutes": 257,
                          "awake_minutes": 10, "from": bed, "to": t},
        "stress_today": stress,
        "training": [{"type": "brisk_walking", "start": today - timedelta(days=1, hours=5),
                      "active_seconds": 2460, "steps": 4120, "calories": 168, "avg_hr": 112}],
    }
    moyoung.health_summary = lambda _mac: summary


# --- the inner process: build, render, save --------------------------------------------

def inner(outdir: str) -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk

    Adw.init()
    from shani_link.app import window as W
    from shani_link.app.switches import Switches

    def pump(seconds: float) -> None:
        ctx = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.01)

    def shot(win, name: str) -> None:
        pump(0.8)
        width, height = win.get_width(), win.get_height()
        paintable = Gtk.WidgetPaintable.new(win)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, width, height)
        node = snap.to_node()
        if node is None:
            print("nothing drawn:", name)
            return
        win.get_renderer().render_texture(node, None).save_to_png(f"{outdir}/{name}.png")
        print("wrote", name, f"{width}x{height}")

    def no_transitions(widget) -> None:
        if isinstance(widget, Gtk.Stack):
            widget.set_transition_type(Gtk.StackTransitionType.NONE)
        child = widget.get_first_child()
        while child is not None:
            no_transitions(child)
            child = child.get_next_sibling()

    def window():
        win = W.LinkWindow(switches=Switches(bus=False))
        win.set_default_size(WIDTH, HEIGHT)
        win.set_size_request(WIDTH, HEIGHT)
        win.present()
        pump(1.0)
        return win

    styles = Adw.StyleManager.get_default()
    want = set(os.environ.get("SHOTS", "").split(",")) - {""}

    def wanted(name: str) -> bool:
        return not want or any(name.startswith(w) for w in want)

    # 1. empty: nothing connected
    styles.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    win = window()
    for name, *_ in W.PAGES:
        if name in EMPTY_PAGES and wanted(f"{name}-empty"):
            win.show_page(name)
            no_transitions(win)
            pump(1.5)
            shot(win, f"{name}-empty")
    win.destroy()

    # 2. with a phone, a watch and sample history
    seed()
    stand_ins()
    for scheme, suffix in ((Adw.ColorScheme.FORCE_LIGHT, ""), (Adw.ColorScheme.FORCE_DARK, "-dark")):
        styles.set_color_scheme(scheme)
        W.LinkWindow._css_done = False
        win = window()
        dark = bool(suffix)
        for name, *_ in W.PAGES:
            if dark and name not in ("phone", "watch"):
                continue
            widget = win.show_page(name)
            no_transitions(win)
            pump(2.0)
            if name == "phone":
                panel = widget.panel
                for tab in ("calls", "messages", "contacts", "music", "files", "remote"):
                    if dark and tab not in ("calls", "contacts"):
                        continue
                    if not wanted(f"phone-{tab}"):
                        continue
                    panel.show_tab(tab)
                    pump(1.0)
                    if tab == "messages" and getattr(panel, "threads", None) is not None:
                        _open_first_thread(panel, pump)
                    shot(win, f"phone-{tab}{suffix}")
                    if tab == "calls" and not dark:
                        _scroll_to_end(win, pump)
                        shot(win, "phone-history")
                        _scroll_to_end(win, pump, end=False)
                    if tab == "contacts" and not dark:
                        _open_contact(panel, pump)
                        shot(win, "phone-contact")
                        _close_contact(panel, pump)
            elif name == "devices":
                for tab, *_rest in W.DEVICE_TABS:
                    if not wanted(f"devices-{tab}"):
                        continue
                    widget.stack.set_visible_child_name(tab)
                    no_transitions(win)
                    pump(2.0)
                    shot(win, f"devices-{tab}")
            elif wanted(name):
                shot(win, name + suffix)
        if not dark and wanted("connect"):
            win.open_connect("")
            pump(1.5)
            shot(win.connect_window, "connect")
            win.connect_window.destroy()
        win.destroy()
        pump(0.3)
    return 0


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _scroll_to_end(win, pump, end: bool = True) -> None:
    """Scroll the Calls tab down to the history (or back to the top)."""
    from gi.repository import Gtk
    for w in _walk(win):
        if isinstance(w, Gtk.ScrolledWindow) and w.get_mapped():
            adj = w.get_vadjustment()
            if adj.get_upper() > adj.get_page_size() + 50:
                adj.set_value(adj.get_upper() if end else 0)
    pump(0.8)


def _open_first_thread(panel, pump) -> None:
    first = next((w for w in _walk(panel.threads) if hasattr(w, "open_thread")), None)
    if first is not None:
        first.open_thread()
    pump(1.0)


def _open_contact(panel, pump) -> None:
    view = panel.contacts_view
    from shani_link import contacts
    people = contacts.book().all()
    target = next((c for c in people if c.full_name.startswith("Priya")), people[0] if people else None)
    if target is not None:
        view.open_sheet(target)
    pump(1.0)


def _close_contact(panel, pump) -> None:
    panel.contacts_view.back()
    pump(0.5)


if __name__ == "__main__":
    if os.environ.get("SHANI_LINK_SHOTS_INNER"):
        sys.exit(inner(sys.argv[1]))
    sys.exit(outer(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"))
