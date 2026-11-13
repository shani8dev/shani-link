"""A virtual touchscreen, so fingers on the phone can drive this computer.

**Why uinput and not the portal.** The RemoteDesktop portal would be the tidy
way (`NotifyTouchDown/Motion/Up`), but niri has no `org.gnome.Mutter.RemoteDesktop`
(GAPS.md; issue #390, PR #1966 open, not merged), and libei is not implemented
either. `/dev/uinput` is compositor-agnostic and already how this platform
injects input: ShaniOS ships `80-uinput.rules` (the `input` group owns the
node) and `shani-tools-extra` for ydotool.

**What the kernel sees.** A type-B multitouch device: `INPUT_PROP_DIRECT` (an
absolute panel, not a touchpad), `BTN_TOUCH`, `ABS_MT_SLOT`,
`ABS_MT_TRACKING_ID`, `ABS_MT_POSITION_X/Y` and `ABS_MT_PRESSURE`. libinput
normalises those axes against their declared range and hands the compositor a
fraction of the output, so the device range is a fixed 0..32767 - the desktop's
resolution is not needed and the precision is the same on any screen.

**Nothing here moves a pointer until a finger is down.** A `SYN_REPORT` with no
active slot is a frame with nothing in it; the device is created on Start and
destroyed on Stop.

The tests write to a file instead of the device node (`TouchScreen.path`), so
the exact `input_event` stream can be asserted without root.
"""

from __future__ import annotations

import fcntl
import os
import struct
import threading
from typing import Dict, List, Optional

DEVICE = "/dev/uinput"

#: type-B multitouch, as the kernel's headers name it
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0x00
BTN_TOUCH = 0x14A
BTN_TOOL_PEN = 0x140
BTN_TOOL_RUBBER = 0x141
BTN_STYLUS = 0x14B
ABS_MT_SLOT = 0x2F
ABS_MT_TRACKING_ID = 0x39
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_PRESSURE = 0x3A
#: a pen's own axes: one contact, with tilt and pressure
ABS_X, ABS_Y = 0x00, 0x01
ABS_PRESSURE = 0x18
ABS_TILT_X, ABS_TILT_Y = 0x1A, 0x1B
INPUT_PROP_DIRECT = 0x01

#: a fixed device space: libinput normalises it, so the screen's size is not
#: needed and a 4K panel gets no coarser a touch than a 720p one
MAXIMUM = 32767

#: the ten slots a type-B device offers
SLOTS = 10

NO_DEVICE = ("the uinput device is not there - the kernel module is not loaded "
             "(modprobe uinput)")
NO_PERMISSION = ("this user cannot write /dev/uinput - add them to the input group "
                 "(ShaniOS ships 80-uinput.rules)")


def available() -> "tuple[bool, str]":
    """(can this inject touch, why not) - without opening anything."""
    if not os.path.exists(DEVICE):
        return False, NO_DEVICE
    if not os.access(DEVICE, os.W_OK):
        return False, NO_PERMISSION
    return True, ""


def _iow(kind: str, number: int, size: int) -> int:
    """The `_IOW` of the kernel's headers: direction, size, type, number."""
    return (1 << 30) | (size << 16) | (ord(kind) << 8) | number


UI_SET_EVBIT = _iow("U", 100, 4)
UI_SET_KEYBIT = _iow("U", 101, 4)
UI_SET_ABSBIT = _iow("U", 103, 4)
UI_SET_PROPBIT = _iow("U", 110, 4)
UI_DEV_SETUP = _iow("U", 3, 92)          # struct uinput_setup: id, name[80], ff max
UI_ABS_SETUP = _iow("U", 4, 24)          # struct uinput_abs_setup: code, absinfo
UI_DEV_CREATE = (ord("U") << 8) | 1
UI_DEV_DESTROY = (ord("U") << 8) | 2

#: one `struct input_event`: timeval, type, code, value
EVENT = struct.Struct("llHHi")


class TouchUnavailable(RuntimeError):
    """The device could not be opened, with the reason a person can act on."""


class TouchScreen:
    """One virtual touchscreen, in a fixed 0..MAXIMUM space.

    `path` is `/dev/uinput` in life and any writable file in a test.
    """

    def __init__(self, name: str = "Shani Link Touch", path: str = DEVICE) -> None:
        self.name = name
        self.path = path
        self._file = None
        self._lock = threading.Lock()
        self._next_id = 0
        self.active: set = set()

    # -- the device ----------------------------------------------------------

    def open(self) -> None:
        """Create the device. Raises `TouchUnavailable` with a sentence."""
        ok, why = available() if self.path == DEVICE else (True, "")
        if not ok:
            raise TouchUnavailable(why)
        try:
            self._file = open(self.path, "wb", buffering=0)
        except OSError as exc:
            raise TouchUnavailable(f"cannot open {self.path} ({exc})") from None
        try:
            self._setup()
        except OSError:
            self.close()
            raise

    def _setup(self) -> None:
        fd = self._file.fileno()
        for bit in (EV_KEY, EV_ABS):
            fcntl.ioctl(fd, UI_SET_EVBIT, bit)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH)
        fcntl.ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        for axis in (ABS_MT_SLOT, ABS_MT_TRACKING_ID, ABS_MT_POSITION_X,
                     ABS_MT_POSITION_Y, ABS_MT_PRESSURE):
            fcntl.ioctl(fd, UI_SET_ABSBIT, axis)

        setup = (struct.pack("HHHH", 0x03, 0x5348, 0x4C4E, 1)      # bus USB, "SHN"
                 + self.name.encode()[:79].ljust(80, b"\x00")
                 + struct.pack("I", 0))
        fcntl.ioctl(fd, UI_DEV_SETUP, setup)
        for axis, maximum in ((ABS_MT_POSITION_X, MAXIMUM), (ABS_MT_POSITION_Y, MAXIMUM),
                              (ABS_MT_PRESSURE, 255)):
            absinfo = struct.pack("H", axis) + b"\x00\x00" + struct.pack(
                "iiiii", 0, maximum, 0, 0, 0)
            fcntl.ioctl(fd, UI_ABS_SETUP, absinfo)
        fcntl.ioctl(fd, UI_DEV_CREATE)

    def _emit(self, events: List[tuple]) -> None:
        if self._file is None:
            return
        with self._lock:
            for ev_type, code, value in events:
                self._file.write(EVENT.pack(0, 0, ev_type, code, value))
            self._file.write(EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def down(self, slot: int, x: int, y: int, pressure: int = 255) -> None:
        """A finger lands on `slot` at (x, y) in device space.

        The tracking id is new for every contact: two fingers down at once must
        not look like one finger that jumped.
        """
        if self._file is None:
            return
        self._next_id += 1
        self.active.add(slot)
        self._emit([(EV_ABS, ABS_MT_SLOT, slot),
                    (EV_ABS, ABS_MT_TRACKING_ID, self._next_id),
                    (EV_ABS, ABS_MT_POSITION_X, x),
                    (EV_ABS, ABS_MT_POSITION_Y, y),
                    (EV_ABS, ABS_MT_PRESSURE, pressure),
                    (EV_KEY, BTN_TOUCH, 1)])

    def move(self, slot: int, x: int, y: int, pressure: int = 255) -> None:
        """The finger on `slot` moves, with the same id it landed with."""
        if self._file is None or slot not in self.active:
            return
        self._emit([(EV_ABS, ABS_MT_SLOT, slot),
                    (EV_ABS, ABS_MT_POSITION_X, x),
                    (EV_ABS, ABS_MT_POSITION_Y, y),
                    (EV_ABS, ABS_MT_PRESSURE, pressure)])

    def up(self, slot: int) -> None:
        """The finger on `slot` lifts; the button goes up with the last one."""
        if self._file is None or slot not in self.active:
            return
        self.active.discard(slot)
        events = [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, -1)]
        if not self.active:
            events.append((EV_KEY, BTN_TOUCH, 0))
        self._emit(events)

    def close(self) -> None:
        """Destroy the device, or just let the file go in a test."""
        if self._file is None:
            return
        try:
            if self.path == DEVICE:
                fcntl.ioctl(self._file.fileno(), UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            self._file.close()
        finally:
            self._file = None
            self.active.clear()


class TouchSurface:
    """The layer between the phone's points and the device's slots.

    A finger is identified by the browser's pointer id, which is stable for the
    length of a touch and may be any number; the device has ten slots. Ids are
    mapped to slots in the order they appear, and a finger that lifts frees its
    slot.
    """

    def __init__(self, screen: TouchScreen, width: int = MAXIMUM,
                 height: int = MAXIMUM) -> None:
        self.screen = screen
        self.width = width
        self.height = height
        self._slots: Dict[int, int] = {}
        self._free = set(range(SLOTS))

    def _to_device(self, x: float, y: float) -> "tuple[int, int]":
        def clamp(value) -> float:
            try:
                value = float(value)
            except (TypeError, ValueError):
                return 0.0
            return max(0.0, min(1.0, value))

        return int(clamp(x) * (self.width - 1)), int(clamp(y) * (self.height - 1))

    def update(self, points: List[dict]) -> None:
        """Apply one frame of the phone's touches.

        `points` are `{"id": int, "x": 0..1, "y": 0..1, "pressure": 0..1}` -
        normalised, because the page is letterboxed and the desktop is not.
        """
        seen = set()
        for point in points or ():
            try:
                identity = int(point.get("id"))
                x, y = self._to_device(point.get("x", 0), point.get("y", 0))
            except (TypeError, ValueError):
                continue
            seen.add(identity)
            pressure = point.get("pressure", 1.0)
            try:
                value = max(1, min(255, int(float(pressure) * 255)))
            except (TypeError, ValueError):
                value = 255
            slot = self._slots.get(identity)
            if slot is None and self._free:
                slot = min(self._free)      # the lowest free slot, so it is predictable
                self._free.remove(slot)
                self._slots[identity] = slot
                self.screen.down(slot, x, y, value)
            elif slot is not None:
                self.screen.move(slot, x, y, value)
        for identity in [i for i in self._slots if i not in seen]:
            slot = self._slots.pop(identity)
            self._free.add(slot)
            self.screen.up(slot)

    def close(self) -> None:
        for slot in list(self.screen.active):
            self.screen.up(slot)
        self.screen.close()


# --- the stylus ----------------------------------------------------------------

class Pen:
    """A uinput pen: proximity, pressure, tilt, and the eraser end.

    A pen is its own device, not a slot on the touchscreen: libinput treats a
    tablet tool differently from a finger (it can hover, it has a barrel button,
    and an eraser is a different tool), and a drawing program behaves
    differently for each. The axes are `ABS_X/Y` rather than the multitouch
    ones, because a pen is one contact.
    """

    def __init__(self, path: str = DEVICE) -> None:
        self.path = path
        self._file = None
        self._lock = threading.Lock()
        self._down = False
        self._tool = BTN_TOOL_PEN

    def open(self) -> None:
        ok, why = available() if self.path == DEVICE else (True, "")
        if not ok:
            raise TouchUnavailable(why)
        try:
            self._file = open(self.path, "wb", buffering=0)
        except OSError as exc:
            raise TouchUnavailable(f"cannot open {self.path} ({exc})") from None
        try:
            fd = self._file.fileno()
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
            for key in (BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS):
                fcntl.ioctl(fd, UI_SET_KEYBIT, key)
            fcntl.ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
            for axis, low, high in ((ABS_X, 0, MAXIMUM), (ABS_Y, 0, MAXIMUM),
                                    (ABS_PRESSURE, 0, 255),
                                    (ABS_TILT_X, -6400, 6400),
                                    (ABS_TILT_Y, -6400, 6400)):
                fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
                fcntl.ioctl(fd, UI_ABS_SETUP, struct.pack("H", axis) + b"\x00\x00"
                            + struct.pack("iiiii", low, high, 0, 0, 0))
            setup = (struct.pack("HHHH", 0x03, 0x5348, 0x4C50, 1)
                     + b"Shani Link Pen".ljust(80, b"\x00") + struct.pack("I", 0))
            fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except OSError:
            self.close()
            raise

    def _emit(self, events: List[tuple]) -> None:
        if self._file is None:
            return
        with self._lock:
            for ev_type, code, value in events:
                self._file.write(EVENT.pack(0, 0, ev_type, code, value))
            self._file.write(EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def down(self, x: int, y: int, pressure: int = 255,
             tilt_x: int = 0, tilt_y: int = 0, eraser: bool = False) -> None:
        """The pen touches down at (x, y), in device space."""
        if self._file is None:
            return
        self._down = True
        self._tool = BTN_TOOL_RUBBER if eraser else BTN_TOOL_PEN
        self._emit([(EV_KEY, self._tool, 1),
                    (EV_KEY, BTN_TOUCH, 1),
                    (EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y),
                    (EV_ABS, ABS_PRESSURE, pressure),
                    (EV_ABS, ABS_TILT_X, tilt_x), (EV_ABS, ABS_TILT_Y, tilt_y)])

    def move(self, x: int, y: int, pressure: int = 255,
             tilt_x: int = 0, tilt_y: int = 0) -> None:
        if self._file is None or not self._down:
            return
        self._emit([(EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y),
                    (EV_ABS, ABS_PRESSURE, pressure),
                    (EV_ABS, ABS_TILT_X, tilt_x), (EV_ABS, ABS_TILT_Y, tilt_y)])

    def up(self) -> None:
        """The pen lifts: contact ends, then proximity, so the tool is gone."""
        if self._file is None or not self._down:
            return
        self._down = False
        self._emit([(EV_KEY, BTN_TOUCH, 0), (EV_KEY, self._tool, 0)])

    def close(self) -> None:
        if self._file is None:
            return
        try:
            if self.path == DEVICE:
                fcntl.ioctl(self._file.fileno(), UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            self._file.close()
        finally:
            self._file = None
            self._down = False


# --- the keyboard ---------------------------------------------------------------

#: the evdev codes this can type, by character (linux/input-event-codes.h)
KEYS = {
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "-": 12, "=": 13, "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22,
    "i": 23, "o": 24, "p": 25, "[": 26, "]": 27, "a": 30, "s": 31, "d": 32, "f": 33,
    "g": 34, "h": 35, "j": 36, "k": 37, "l": 38, ";": 39, "'": 40, "`": 41, "\\": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50, ",": 51, ".": 52,
    "/": 53, " ": 57,
}
SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
           "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
           ":": ";", '"': "'", "~": "`", "|": "\\", "<": ",", ">": ".", "?": "/"}
KEY_LEFTSHIFT = 42
KEY_ENTER, KEY_BACKSPACE, KEY_TAB, KEY_ESC = 28, 14, 15, 1


class PenSurface:
    """The pen's half of the layer: normalised points, a device's own axes.

    `update(None)` lifts. Tilt arrives in degrees from PointerEvent; the device
    is declared in hundredths of a degree, which is what libinput expects.
    """

    def __init__(self, pen: Pen, width: int = MAXIMUM, height: int = MAXIMUM) -> None:
        self.pen = pen
        self.width = width
        self.height = height
        self._down = False

    def _xy(self, x, y) -> "tuple[int, int]":
        def clamp(value) -> float:
            try:
                value = float(value)
            except (TypeError, ValueError):
                return 0.0
            return max(0.0, min(1.0, value))

        return int(clamp(x) * (self.width - 1)), int(clamp(y) * (self.height - 1))

    def update(self, point: "Optional[dict]") -> None:
        if point is None or point.get("lift"):
            if self._down:
                self.pen.up()
                self._down = False
            return
        x, y = self._xy(point.get("x", 0), point.get("y", 0))
        pressure = point.get("pressure", 1.0)
        try:
            pressure = max(0, min(255, int(float(pressure) * 255)))
        except (TypeError, ValueError):
            pressure = 255
        try:
            tilt_x = max(-6400, min(6400, int(float(point.get("tilt_x", 0)) * 100)))
            tilt_y = max(-6400, min(6400, int(float(point.get("tilt_y", 0)) * 100)))
        except (TypeError, ValueError):
            tilt_x = tilt_y = 0
        if not self._down:
            self.pen.down(x, y, pressure, tilt_x, tilt_y, eraser=bool(point.get("eraser")))
            self._down = True
        else:
            self.pen.move(x, y, pressure, tilt_x, tilt_y)


class Keyboard:
    """A uinput keyboard, for the text a person types on the phone.

    US layout only: what is not on it is skipped rather than guessed, and the
    page says so. A phone keyboard can produce any script; a keycode cannot.
    """

    def __init__(self, path: str = DEVICE) -> None:
        self.path = path
        self._file = None
        self._lock = threading.Lock()

    def open(self) -> None:
        ok, why = available() if self.path == DEVICE else (True, "")
        if not ok:
            raise TouchUnavailable(why)
        try:
            self._file = open(self.path, "wb", buffering=0)
        except OSError as exc:
            raise TouchUnavailable(f"cannot open {self.path} ({exc})") from None
        try:
            fd = self._file.fileno()
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            for code in sorted(set(list(KEYS.values()) + [KEY_LEFTSHIFT, KEY_ENTER,
                                                          KEY_BACKSPACE, KEY_TAB, KEY_ESC])):
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)
            setup = (struct.pack("HHHH", 0x03, 0x5348, 0x4B42, 1)
                     + b"Shani Link Keyboard".ljust(80, b"\x00") + struct.pack("I", 0))
            fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except OSError:
            self.close()
            raise

    def _emit(self, events: List[tuple]) -> None:
        if self._file is None:
            return
        with self._lock:
            for ev_type, code, value in events:
                self._file.write(EVENT.pack(0, 0, ev_type, code, value))
            self._file.write(EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def press(self, code: int, shift: bool = False) -> None:
        """One key: down, then up, with the shift held for the first if asked."""
        events = []
        if shift:
            events.append((EV_KEY, KEY_LEFTSHIFT, 1))
        events += [(EV_KEY, code, 1), (EV_KEY, code, 0)]
        if shift:
            events.append((EV_KEY, KEY_LEFTSHIFT, 0))
        self._emit(events)

    def type(self, text: str) -> int:
        """Type `text`; returns how many characters had no key (and were skipped)."""
        skipped = 0
        for char in text or "":
            if char in ("\n", "\r"):
                self.press(KEY_ENTER)
            elif char == "\t":
                self.press(KEY_TAB)
            elif char == "\b":
                self.press(KEY_BACKSPACE)
            elif char in SHIFTED:
                self.press(KEYS[SHIFTED[char]], shift=True)
            elif char in KEYS:
                self.press(KEYS[char])
            elif char.isupper() and char.lower() in KEYS:
                self.press(KEYS[char.lower()], shift=True)
            else:
                skipped += 1
        return skipped

    def backspace(self, count: int = 1) -> None:
        for _ in range(max(0, int(count))):
            self.press(KEY_BACKSPACE)

    def close(self) -> None:
        if self._file is None:
            return
        try:
            if self.path == DEVICE:
                fcntl.ioctl(self._file.fileno(), UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            self._file.close()
        finally:
            self._file = None