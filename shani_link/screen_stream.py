"""Show this computer's screen on the phone, over adb or Bluetooth.

Two roads carry the same picture, and adb is tried first:

- **adb - a cable, or Wi-Fi debugging** - `adb reverse tcp:8090 tcp:8090`
  makes the phone's own loopback arrive at this computer, so the phone opens
  `http://127.0.0.1:8090` and the picture never touches a network: no
  tethering, no `bnep0`, no firewall exception, and it makes no difference
  whether the phone is on a cable or paired over Wi-Fi. Either way carries far
  more than Bluetooth's 61-72 KB/s, so the size comes from the phone itself
  (`adb shell wm size`), capped at 1920 on the long side and rounded to even.
  A cable wins when both are connected: it is faster, and it does not depend on
  the Wi-Fi staying up.
- **Bluetooth** - the phone shares its network over Bluetooth (tethering), this
  computer joins it as `bnep0`, and the page is served on that address only, so
  nothing is reachable from Wi-Fi. 640x360 at 8 fps -> VP8 at ~400 kbit/s: the
  link carried 61-72 KB/s (~500 kbit/s) both ways through the phone, 72 ms
  latency.

Both are captured the same way: the ScreenCast portal
(`org.freedesktop.portal.ScreenCast`) asks the person once and hands back a
PipeWire stream; GStreamer encodes it; a plain `<video>` (WebM) or `<img>`
(MJPEG) plays it, so the phone needs no app.

- **Recovery** - an encoder that stops is restarted once (`Recovery`). What
  cannot be restarted is the portal session: a new one asks the person again,
  which they did not ask for, so a session the person ended ends the stream and
  says so instead of re-prompting behind their back.
- **The phone stays awake** - adb sets `stay_on_while_plugged_in`'s bit for
  whatever the phone is actually charging from (1 AC, 2 USB, 4 wireless; asked
  of the phone, not assumed), for as long as the stream runs, and puts the
  original value back afterwards. A phone that dims after 30 seconds reads as
  the feature being broken. A phone on its battery is told so: the setting does
  nothing then, and only Android can change that.

The adb road was written without a phone to try it on; what is tested is
everything it consults - the tunnel, the size, the recovery - against a fake
`adb` in `tests/test_screen_stream.py`.

Measured problems on the way are in AGENTS.md: Ubuntu's udev renamed bnep0 to
enx<mac> and bluez dropped the link (fixed with a .link file, NamePolicy=keep
kernel); and the phone hangs up 25 ms after connecting unless its own
Bluetooth tethering is on.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from shani_link import android
from shani_link import touch

logger = logging.getLogger(__name__)

PORT = 8090
USB_HOST = "127.0.0.1"

#: the Bluetooth road, sized from a measurement of that link (see the module docstring)
BLUETOOTH_WIDTH, BLUETOOTH_HEIGHT = 640, 360
BLUETOOTH_FPS, BLUETOOTH_BITRATE = 8, 400_000

#: the cable road: the phone's own size, and enough bitrate to keep text crisp
USB_FPS, USB_BITRATE, USB_MAX_LONG_SIDE = 24, 4_000_000, 1920

#: a phone that will not say its size still gets a picture
FALLBACK_SIZE = (1280, 720)


def gstreamer_ready(codec: str = "webm") -> "tuple[bool, str]":
    """(can encode here, why not) - without starting anything.

    A skill asks this first so it can name the missing **package** rather than
    the portal's refusal, which says nothing anybody can act on. The same
    reason `files._PACKAGE_HINTS` exists.
    """
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError) as exc:
        return False, (f"the GStreamer bindings are not here ({exc}); "
                       f"gstreamer and gst-plugins-good provide them")
    Gst.init(None)
    needed = ("pipewiresrc", "vp8enc", "webmmux") if codec == "webm" \
        else ("pipewiresrc", "jpegenc")
    missing = [e for e in needed if Gst.ElementFactory.find(e) is None]
    if missing:
        return False, (f"gstreamer has no {', '.join(missing)}; "
                       f"gst-plugins-base, gst-plugins-good and "
                       f"gst-plugins-bad provide them")
    return True, ""


def bluetooth_address() -> Optional[str]:
    """This computer's IPv4 address on a Bluetooth network (bnep*), or None.

    `ip` answers from the kernel's own table and is on both images, so unlike
    `socket.connect` this sends nothing. The timeout is not decoration: this is
    the **only** `subprocess.run` in the shipped tree without one, and a hung
    child here would hang the caller indefinitely with nothing to recover it -
    the invariant AGENTS.md records was "zero subprocess.run without a
    timeout", and this broke it silently for as long as the module was dead.
    """
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True,
                             text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("ip addr show did not answer: %s", exc)
        return None
    m = re.search(r"^\d+:\s+(bnep\d+)\s+inet\s+([\d.]+)/", out, re.M)
    return m.group(2) if m else None


# --- the phone over adb: which one, what size, one tunnel ---------------------

def phone_serial() -> "tuple[str, str]":
    """(serial, problem): the phone to mirror to, or why there is none.

    A cable wins over a wireless phone: it is faster, and it does not depend on
    the Wi-Fi staying up. `adb devices` is the whole question - the phone is the
    first consent, and a device listed as `device` has already accepted this
    computer's key - so nothing here asks the person for anything. Two phones on
    cables is the one answer that still belongs to them.
    """
    if not android.available():
        return "", android.NO_ADB
    ready = android.ready_devices()
    if not ready:
        return "", android.ready_serial()[1]
    wired = [serial for serial, road in ready if road == "usb"]
    if len(wired) > 1:
        return "", "more than one phone is on a cable; unplug one"
    if wired:
        return wired[0], ""
    return ready[0][0], ""


def adb_ready() -> "tuple[bool, str]":
    """(can adb carry the picture, why not) - nothing started."""
    _serial, problem = phone_serial()
    return (False, problem) if problem else (True, "")


def choose() -> "tuple[str, str]":
    """("usb"|"wifi"|"bluetooth", where the phone opens it) or ("", why neither works).

    The phone over adb first, on whichever transport it is on; Bluetooth is not
    a filter of last resort - it is the road that works with the phone in
    another room - so it is still offered whenever adb cannot reach a phone.
    """
    serial, problem = phone_serial()
    if serial:
        return ("wifi" if ":" in serial else "usb"), f"{USB_HOST}:{PORT}"
    host = bluetooth_address()
    if host:
        return "bluetooth", f"{host}:{PORT}"
    return "", f"{problem}; and the phone is not sharing its network over Bluetooth"


def fit_to_phone(width: int, height: int, max_long_side: int = USB_MAX_LONG_SIDE,
                 landscape: bool = True) -> "tuple[int, int]":
    """The phone's size, turned the way it is held and capped for the encoder.

    Even dimensions because VP8 (like every H.264 encoder) wants them; turned
    because a phone that reports 1080x2400 is portrait and a desktop mirrored
    into it would be read sideways; capped because a 4K panel is more pixels
    than one software encoder should spend on a mirrored desktop over one cable.
    """
    if landscape and height > width:
        width, height = height, width
    longest = max(width, height)
    if longest > max_long_side:
        scale = max_long_side / longest
        width, height = int(width * scale), int(height * scale)
    return max(2, width - width % 2), max(2, height - height % 2)


def _adb(args, serial: str = "", timeout: int = 10):
    argv = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


@contextmanager
def adb_link(port: int = PORT, serial: str = ""):
    """Carry `port` over adb - a cable or Wi-Fi - and keep the phone awake.

    Yields `(ok, note)`; on failure `note` is why. Both the tunnel and the
    phone's own setting are undone on the way out, including on an exception: a
    `stay_on_while_plugged_in` left at "always" drains a battery for days after
    a demo, and a stale `adb reverse` makes the next run look like it works when
    the phone is showing a page from the last one.
    """
    def adb_call(args) -> "tuple[bool, str]":
        try:
            done = _adb(args, serial=serial)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        return done.returncode == 0, (done.stdout or done.stderr or "").strip()

    ok, out = adb_call(["reverse", f"tcp:{port}", f"tcp:{port}"])
    if not ok:
        yield False, f"adb could not open the tunnel ({out or 'no answer'})"
        return

    note = ""
    _source, bit = android.powered_by(serial)
    if not bit:
        note = "the phone is on its battery, so its screen may dim while it mirrors"
    original = None
    got, value = adb_call(["shell", "settings", "get", "global", "stay_on_while_plugged_in"])
    if bit and got and value.isdigit():
        original = int(value)
        adb_call(["shell", "settings", "put", "global", "stay_on_while_plugged_in",
                  str(original | bit)])

    try:
        yield True, note
    finally:
        if original is not None:
            adb_call(["shell", "settings", "put", "global", "stay_on_while_plugged_in",
                      str(original)])
        adb_call(["reverse", "--remove", f"tcp:{port}"])


# --- what to do when the picture stops ---------------------------------------

class Recovery:
    """The ladder when the encoder stops: restart it once, then stop and say why.

    Two restarts would be a guess, not a recovery - the same pipeline that failed
    twice is not going to work on the third try. `playing()` resets the count, so
    what "twice" means is twice in a row, not twice in an afternoon.

    A session the person closed is never restarted: a new session asks them
    again, and re-opening a share they just ended is the opposite of asking.
    """

    def __init__(self, restarts: int = 1) -> None:
        self.allowed = restarts
        self.used = 0

    def playing(self) -> None:
        """A frame arrived: the last restart worked."""
        self.used = 0

    def stopped(self, *, session_closed: bool = False) -> "tuple[bool, str]":
        """(restart the encoder?, the sentence for the log and the page)."""
        if session_closed:
            return False, ("the screen share was ended on this computer; "
                           "press Start to ask again")
        if self.used < self.allowed:
            self.used += 1
            return True, f"the encoder stopped; restarting it ({self.used} of {self.allowed})"
        return False, "the encoder stopped twice; press Start to ask again"


# --- the portal ------------------------------------------------------------

class ScreenCast:
    """One portal session: `start()` -> (pipewire fd, node id). Asks the person once."""

    PORTAL = ("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        self.session = ""
        self.closed = False

    def _request(self, method: str, args, timeout: int = 120) -> dict:
        """Call a portal method and wait for its Request's Response signal."""
        GLib = self.GLib
        token = "chronoa" + secrets.token_hex(4)
        path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        box, loop = {}, GLib.MainLoop()

        def on_response(_c, _s, _p, _i, _sig, params):
            code, results = params.unpack()
            box["code"], box["results"] = code, results
            loop.quit()
        sub = self.bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
                                        "Response", path, None, 0, on_response)
        try:
            options = dict(args[-1]) if args else {}
            options["handle_token"] = GLib.Variant("s", token)
            variant = GLib.Variant(self._sig(method), (*args[:-1], options))
            self.bus.call_sync(*self.PORTAL, self.IFACE, method, variant, None, 0, 10000, None)
            GLib.timeout_add_seconds(timeout, loop.quit)
            loop.run()
        finally:
            self.bus.signal_unsubscribe(sub)
        if box.get("code") != 0:
            raise RuntimeError(f"screen sharing was not allowed ({'cancelled' if box.get('code') == 1 else 'no answer' if 'code' not in box else 'failed'})")
        return box["results"]

    @staticmethod
    def _sig(method: str) -> str:
        return {"CreateSession": "(a{sv})", "SelectSources": "(oa{sv})", "Start": "(osa{sv})"}[method]

    def start(self, window: bool = False) -> "tuple[int, int]":
        """Open the portal and take its stream. `window=True` asks for a window
        instead of a monitor - the person picks which, and only that is shown."""
        GLib = self.GLib
        res = self._request("CreateSession", [{"session_handle_token": GLib.Variant("s", "chronoa_s")}])
        self.session = res["session_handle"]
        self._request("SelectSources", [self.session, {"types": GLib.Variant("u", 2 if window else 1),
                                                       "cursor_mode": GLib.Variant("u", 2),    # cursor drawn in
                                                       "multiple": GLib.Variant("b", False)}])
        res = self._request("Start", [self.session, "", {}], timeout=180)       # the person answers here
        node = res["streams"][0][0]
        # The session's own Closed signal, so a share ended from the desktop's
        # sharing indicator is a fact we can report rather than a picture that
        # quietly stops moving.
        self.bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Session",
                                  "Closed", self.session, None, 0, self._on_closed)
        fds_out = self.bus.call_with_unix_fd_list_sync(
            *self.PORTAL, self.IFACE, "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (self.session, {})),
            None, 0, 10000, None, None)
        result, fdlist = fds_out[0], fds_out[1]
        fd = fdlist.get(result.unpack()[0])
        return fd, int(node)

    def _on_closed(self, _c, _s, _p, _i, _signal, _params) -> None:
        self.closed = True
        logger.info("the screen share ended on this computer")

    def close(self) -> None:
        self.closed = True
        if self.session:
            try:
                self.bus.call_sync("org.freedesktop.portal.Desktop", self.session,
                                   "org.freedesktop.portal.Session", "Close", None, None, 0, 3000, None)
            except Exception:  # noqa: BLE001
                pass
            self.session = ""


# --- codecs ------------------------------------------------------------------

def default_sink() -> str:
    """The default audio sink's name, from PipeWire's own metadata (or "").

    `pw-metadata` is part of the pipewire package (a hard dependency), so this
    does not need `pactl` - which the image does not ship.
    """
    try:
        out = subprocess.run(["pw-metadata"], capture_output=True, text=True,
                             timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"key:'default\.audio\.sink'\s+value:'\{\"name\":\"([^\"]+)\"", out)
    return match.group(1) if match else ""


def audio_pipeline(sink: str) -> str:
    """Desktop audio as a live Opus stream in WebM, which a browser plays."""
    return (f"pipewiresrc target-object={sink} do-timestamp=true ! "
            f"audioconvert ! audioresample ! "
            f"opusenc bitrate=64000 ! webmmux streamable=true ! "
            f"appsink name=out emit-signals=true sync=false max-buffers=64 drop=true")


def microphone() -> "tuple[str, str]":
    """(the source to record from, the sink to play into), or ("", why not).

    The phone's microphone becomes a real microphone through the kernel's
    **snd-aloop** card - the same mechanism DroidCam's Linux client uses: the
    link plays the phone's PCM into the loopback's playback side and any
    application records from its capture side, which PipeWire shows as
    `alsa_input.platform-snd_aloop.N`. It is an in-tree module, so unlike
    `v4l2loopback` it needs no DKMS - only to be loaded. An image loads it with
    a high index (`options snd-aloop index=7`) so it cannot take over the
    default card; the index is read from the graph rather than assumed.

    Nothing here loads it: this is an unprivileged app, and loading a kernel
    module is the system's job.
    """
    from shani_link import audio
    index = None
    for props in audio.nodes():
        found = re.match(r"alsa_input\.platform-snd_aloop\.(\d+)$",
                         str(props.get("node.name", "")))
        if found:
            index = found.group(1)
            break
    if index is None:
        return "", ("the loopback sound card is not loaded - modprobe snd-aloop "
                    "(an image loads it from modules-load.d)")
    return (f"alsa_input.platform-snd_aloop.{index}",
            f"alsa_output.platform-snd_aloop.{index}")


def mic_pipeline(sink: str) -> str:
    """Raw PCM from the phone into the loopback's playback side.

    No container and no decoder: the page sends signed 16-bit mono at 48 kHz,
    which `appsrc` wraps as it arrives. A MediaRecorder WebM would need a
    parser and add a chunk of latency to every syllable.
    """
    return ("appsrc name=mic is-live=true format=time do-timestamp=true "
            "max-bytes=4194304 ! "
            "audio/x-raw,format=S16LE,rate=48000,channels=1 ! "
            "audioconvert ! audioresample ! "
            f"pipewiresink target-object={sink} sync=false")


def camera_pipeline(width: int = 1280, height: int = 720, fps: int = 15) -> str:
    """The phone's camera as a PipeWire **camera** - no kernel module.

    A `pipewiresink` in `mode=provide` with `media.class=Video/Source` and
    `media.role=Camera` is a camera node in the graph, which is what
    PipeWire-aware applications enumerate (Firefox with
    `media.webrtc.camera.allow-pipewire`, OBS 30+). Chrome and the V4L2 apps
    (Zoom, Discord) do not, and for those there is only `v4l2loopback`.

    JPEG in, because the browser produces it with `canvas.toBlob` and the
    encoder is already in gst-plugins-good; `jpegdec` is the cost, a few
    milliseconds a frame.
    """
    return ("appsrc name=cam is-live=true format=time do-timestamp=true "
            "max-bytes=4194304 ! image/jpeg ! jpegdec ! videoconvert ! "
            f"video/x-raw,format=YUY2,width={width},height={height},framerate={fps}/1 ! "
            "pipewiresink mode=provide "
            "stream-properties=\"properties,media.class=Video/Source,media.role=Camera\" "
            "client-name=\"Shani Link Camera\"")


class Codec:
    """One way of putting pictures on the wire: pipeline tail, page, framing."""

    name = ""
    content_type = ""

    def pipeline_tail(self, width: int, height: int, fps: int, bitrate: int) -> str:
        raise NotImplementedError

    def page(self) -> bytes:
        raise NotImplementedError

    def frame(self, data: bytes) -> bytes:
        raise NotImplementedError


class WebM(Codec):
    """VP8 in a streamable WebM: a plain `<video>`, and a browser that buffers it well."""

    name = "webm"
    content_type = "video/webm"

    def pipeline_tail(self, width: int, height: int, fps: int, bitrate: int) -> str:
        return (f"vp8enc deadline=1 cpu-used=8 target-bitrate={bitrate} "
                f"keyframe-max-dist={fps * 2} end-usage=cbr ! "
                f"webmmux streamable=true ! appsink name=out emit-signals=true "
                f"sync=false max-buffers=64 drop=true")

    def page(self) -> bytes:
        return PAGE_WEBM

    def frame(self, data: bytes) -> bytes:
        return data


class Mjpeg(Codec):
    """JPEG after JPEG: any browser at all, no WebCodecs, no JavaScript.

    Heavy on the wire, so it is not what the Bluetooth road would choose - but
    over a cable it costs nothing and it is the answer for a phone whose browser
    has no WebCodecs, or one being mirrored into a viewer that only takes an
    image.
    """

    name = "mjpeg"
    content_type = "multipart/x-mixed-replace; boundary=frame"

    def __init__(self, quality: int = 70) -> None:
        self.quality = quality

    def pipeline_tail(self, width: int, height: int, fps: int, bitrate: int) -> str:
        return (f"videoconvert ! jpegenc quality={self.quality} ! "
                f"appsink name=out emit-signals=true sync=false max-buffers=1 drop=true")

    def page(self) -> bytes:
        return PAGE_MJPEG

    def frame(self, data: bytes) -> bytes:
        return (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(data)).encode("ascii") + b"\r\n\r\n" + data + b"\r\n")


CODECS = {"webm": WebM, "mjpeg": Mjpeg}


def create_codec(name: str = "webm") -> Codec:
    try:
        return CODECS[name]()
    except KeyError:
        raise ValueError(f"unknown codec {name!r}; one of {', '.join(CODECS)}") from None


INPUT_CSS = """
#touch{position:fixed;inset:0;display:none;touch-action:none;z-index:5}
#type{position:fixed;left:0;right:0;bottom:0;display:none;gap:6px;padding:6px;
background:#000b;z-index:6}
#type input{flex:1;min-width:0;padding:8px 10px;border-radius:8px;border:1px solid #555;
background:#111;color:#eee;font:16px sans-serif}
#sound,#mic,#cam,#keys{position:fixed;top:8px;display:none;z-index:6;padding:8px 12px;
border-radius:8px;border:1px solid #666;background:#000b;color:#eee;font:14px sans-serif}
#sound{right:8px}#cam{right:96px}#mic{right:200px}#keys{right:296px}
"""

INPUT_JS = """
const layer = document.getElementById("touch");
const shown = document.querySelector("video,img");
const points = new Map();
let queued = 0;
let mic = null;
let cam = null;

function send(path, body) {
  fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
               body: JSON.stringify(body || {}), keepalive: true}).catch(() => {});
}
function where(event) {
  const box = shown.getBoundingClientRect();
  const vw = shown.videoWidth || shown.naturalWidth || box.width;
  const vh = shown.videoHeight || shown.naturalHeight || box.height;
  const scale = Math.min(box.width / vw, box.height / vh);   // object-fit: contain
  const w = vw * scale, h = vh * scale;
  const left = box.left + (box.width - w) / 2, top = box.top + (box.height - h) / 2;
  return {x: (event.clientX - left) / w, y: (event.clientY - top) / h};
}
function flush() {
  queued = 0;
  send("/touch", {points: [...points.values()]});
}
function queue() { if (!queued) queued = requestAnimationFrame(flush); }
function penFrame(event, lift) {
  const at = where(event);
  send("/pen", {
    x: at.x, y: at.y, pressure: event.pressure || 0,
    tilt_x: event.tiltX || 0, tilt_y: event.tiltY || 0,
    eraser: event.button === 5, lift: !!lift
  });
}
for (const name of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) {
  layer.addEventListener(name, (event) => {
    if (event.pointerType === "pen") {
      if (name === "pointerdown") { layer.setPointerCapture(event.pointerId); penFrame(event); }
      else if (name === "pointermove" && event.buttons) { penFrame(event); }
      else if (name === "pointerup" || name === "pointercancel") { penFrame(event, true); }
      event.preventDefault();
      return;
    }
    if (name === "pointerdown") {
      layer.setPointerCapture(event.pointerId);
      const at = where(event);
      points.set(event.pointerId, {id: event.pointerId, x: at.x, y: at.y,
                                   pressure: event.pressure || 1});
    } else if (name === "pointermove") {
      const held = points.get(event.pointerId);
      if (!held) return;
      const at = where(event);
      held.x = at.x; held.y = at.y; held.pressure = event.pressure || held.pressure;
    } else {
      points.delete(event.pointerId);
    }
    event.preventDefault();
    queue();
  }, {passive: false});
}

const kind = document.getElementById("kind");
let typed = "";
kind.addEventListener("input", () => {
  const value = kind.value;
  if (value.length > typed.length) send("/key", {text: value.slice(typed.length)});
  else if (value.length < typed.length) send("/key", {backspace: typed.length - value.length});
  typed = value;
});
kind.addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); send("/key", {text: "\\n"}); }
});

const keys = document.getElementById("keys");
keys.addEventListener("click", () => {
  const bar = document.getElementById("type");
  const showing = bar.style.display === "flex";
  bar.style.display = showing ? "none" : "flex";
  keys.textContent = showing ? "Type" : "Hide";
  if (!showing) kind.focus();
});

const sound = document.getElementById("sound");
sound.addEventListener("click", () => {
  document.getElementById("sound-el").play().catch(() => {});
});

const micButton = document.getElementById("mic");
micButton.addEventListener("click", async () => {
  if (mic) {
    try {
      mic.node.disconnect(); mic.source.disconnect();
      mic.stream.getTracks().forEach((track) => track.stop());
      mic.ctx.close();
    } catch (error) { /* already gone */ }
    mic = null;
    micButton.textContent = "Mic on";
    fetch("/mic", {method: "POST", body: new Uint8Array(0)}).catch(() => {});
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia(
      {audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
    const ctx = new AudioContext({sampleRate: 48000});
    const source = ctx.createMediaStreamSource(stream);
    const node = ctx.createScriptProcessor(4096, 1, 1);
    node.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const pcm = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        pcm[i] = Math.max(-1, Math.min(1, input[i])) * 32767;
      }
      fetch("/mic", {method: "POST", body: pcm.buffer}).catch(() => {});
    };
    const silent = ctx.createGain();
    silent.gain.value = 0;
    source.connect(node); node.connect(silent); silent.connect(ctx.destination);
    mic = {stream: stream, ctx: ctx, node: node, source: source};
    micButton.textContent = "Mic off";
  } catch (error) {
    micButton.textContent = "No microphone";
  }
});

const camButton = document.getElementById("cam");
camButton.addEventListener("click", async () => {
  if (cam) {
    clearInterval(cam.timer);
    cam.stream.getTracks().forEach((track) => track.stop());
    cam = null;
    camButton.textContent = "Camera on";
    fetch("/camera", {method: "POST", body: new Uint8Array(0)}).catch(() => {});
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia(
      {video: {facingMode: "environment", width: 1280, height: 720}, audio: false});
    const video = document.createElement("video");
    video.srcObject = stream; video.muted = true; video.playsInline = true;
    await video.play();
    const canvas = document.createElement("canvas");
    canvas.width = 1280; canvas.height = 720;
    const context = canvas.getContext("2d");
    const sendFrame = () => {
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        if (blob) fetch("/camera", {method: "POST", body: blob}).catch(() => {});
      }, "image/jpeg", 0.7);
    };
    const timer = setInterval(sendFrame, 66);
    cam = {stream: stream, video: video, timer: timer};
    camButton.textContent = "Camera off";
  } catch (error) {
    camButton.textContent = "No camera";
  }
});

fetch("/info").then((r) => r.json()).then((info) => {
  if (info.touch || info.pen) layer.style.display = "block";
  if (info.keyboard) document.getElementById("type").style.display = "flex";
  if (info.sound) sound.style.display = "block";
  if (info.mic) micButton.style.display = "block";
  if (info.camera) camButton.style.display = "block";
  if (info.keyboard) keys.style.display = "block";
}).catch(() => {});
"""


def _page(media: str, extra_css: str = "") -> bytes:
    """The viewer: the picture, the touch layer, the pen, the typing bar and the
    three devices the phone can lend the desktop."""
    return ("<!doctype html><html><head>"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Chronoa screen</title><style>"
            "body{margin:0;background:#000;color:#aaa;font:14px sans-serif}"
            "video,img{width:100vw;height:100vh;object-fit:contain;display:block}"
            + extra_css + INPUT_CSS + "</style></head><body>"
            + media
            + '<div id="touch"></div>'
            + '<div id="type"><input id="kind" placeholder="Type on this computer…" '
              'autocomplete="off" autocapitalize="off" spellcheck="false">'
              '<button tabindex="-1">·</button></div>'
            + '<button id="keys" type="button">Type</button>'
            + '<button id="sound" type="button">Sound on</button>'
            + '<button id="cam" type="button">Camera on</button>'
            + '<button id="mic" type="button">Mic on</button>'
            + '<audio id="sound-el" src="/audio" autoplay></audio>'
            + "<script>" + INPUT_JS + "</script></body></html>").encode()


PAGE_WEBM = _page('<video src="/stream" autoplay muted playsinline></video>',
                  "p{position:fixed;bottom:4px;left:8px;margin:0;z-index:4}")
PAGE_MJPEG = _page('<img src="/stream" alt="This computer screen">')

# --- encoding and serving ------------------------------------------------------

def pipeline_description(fd: int, node: int, width: int, height: int, fps: int,
                         bitrate: int, codec: Codec) -> str:
    return (f"pipewiresrc fd={fd} path={node} do-timestamp=true keepalive-time=1000 ! "
            f"videoconvert ! videoscale ! videorate ! "
            f"video/x-raw,width={width},height={height},framerate={fps}/1 ! "
            + codec.pipeline_tail(width, height, fps, bitrate))


def record_pipeline(fd: int, node: int, fps: int, bitrate: int, path: str) -> str:
    """The portal's stream into a file, at the source's own size.

    No caps: a window is not 1920x1080, and forcing a size would stretch it. The
    encoder takes what the portal gives and keeps the aspect. The muxer is the
    streamable one the live stream uses, so the file has no seek index until it
    is remuxed - the same bytes, the same players.
    """
    return (f"pipewiresrc fd={fd} path={node} do-timestamp=true keepalive-time=1000 ! "
            f"videoconvert ! "
            f"vp8enc deadline=1 cpu-used=8 target-bitrate={bitrate} "
            f"keyframe-max-dist={fps * 2} end-usage=cbr ! "
            f"webmmux streamable=true ! filesink location={path}")


def still_pipeline(fd: int, node: int) -> str:
    """The portal's stream as one PNG, taken on the first frame that arrives.

    A still is the same capture as the mirror and the recording, so it can be one
    window (`--window`): the person picks it, and the picture is exactly that
    window's, not a rectangle of the screen around it.
    """
    return (f"pipewiresrc fd={fd} path={node} do-timestamp=true keepalive-time=1000 ! "
            f"videoconvert ! pngenc ! "
            f"appsink name=shot emit-signals=true sync=false max-buffers=1 drop=true")


class Stream:
    """Everything one stream needs: the portal's node, the size, the codec.

    `session_closed` is a callable rather than the portal object, so the server
    can be exercised without a desktop: what it needs to know is one boolean.
    """

    def __init__(self, fd: int, node: int, width: int = BLUETOOTH_WIDTH,
                 height: int = BLUETOOTH_HEIGHT, fps: int = BLUETOOTH_FPS,
                 bitrate: int = BLUETOOTH_BITRATE, codec: Optional[Codec] = None,
                 session_closed=None) -> None:
        self.fd, self.node = fd, node
        self.width, self.height = width, height
        self.fps, self.bitrate = fps, bitrate
        self.codec = codec or WebM()
        self.session_closed = session_closed or (lambda: False)


class Mic:
    """The upload half: raw PCM from the phone, pushed into the loopback.

    One pipeline for the whole stream, created on the first chunk - the phone
    sends 100 ms slices and a new pipeline per slice would never play.
    """

    def __init__(self, sink: str) -> None:
        self.sink = sink
        self._pipe = None
        self._src = None
        self._lock = threading.Lock()

    def push(self, data: bytes) -> bool:
        from gi.repository import Gst
        with self._lock:
            if self._pipe is None:
                self._pipe = Gst.parse_launch(mic_pipeline(self.sink))
                self._src = self._pipe.get_by_name("mic")
                self._pipe.set_state(Gst.State.PLAYING)
            self._src.emit("push-buffer", Gst.Buffer.new_wrapped(data))
        return True

    def close(self) -> None:
        from gi.repository import Gst
        with self._lock:
            if self._pipe is not None:
                self._pipe.set_state(Gst.State.NULL)
                self._pipe = self._src = None


class Camera:
    """The upload half of the camera: JPEG frames into a PipeWire camera node.

    One pipeline for the whole stream, created on the first frame, like `Mic`.
    """

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 15) -> None:
        self.width, self.height, self.fps = width, height, fps
        self._pipe = None
        self._src = None
        self._lock = threading.Lock()

    def push(self, jpeg: bytes) -> bool:
        from gi.repository import Gst
        with self._lock:
            if self._pipe is None:
                self._pipe = Gst.parse_launch(
                    camera_pipeline(self.width, self.height, self.fps))
                self._src = self._pipe.get_by_name("cam")
                self._pipe.set_state(Gst.State.PLAYING)
            self._src.emit("push-buffer", Gst.Buffer.new_wrapped(jpeg))
        return True

    def close(self) -> None:
        from gi.repository import Gst
        with self._lock:
            if self._pipe is not None:
                self._pipe.set_state(Gst.State.NULL)
                self._pipe = self._src = None


class StreamServer:
    """One viewer per connection, and an encoder that is restarted when it stops.

    Each viewer gets its own encoder from the shared portal stream: two people
    watching at once is rare, and one encoder that a slow viewer can stall is
    worse than two that cannot.
    """

    def __init__(self, stream: Stream, host: str, port: int = PORT, on_fatal=None,
                 touch_surface=None, pen_surface=None, keyboard=None,
                 sound_sink: str = "", mic_source: str = "",
                 camera: bool = True) -> None:
        self.stream, self.host, self.port = stream, host, port
        self.on_fatal = on_fatal
        self.touch_surface = touch_surface
        self.pen_surface = pen_surface
        self.keyboard = keyboard
        self.sound_sink = sound_sink
        self.mic_source = mic_source
        self.mic = None
        self.cam = None
        self.camera = camera
        self.httpd: Optional[ThreadingHTTPServer] = None

    def serve(self) -> None:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        owner = self
        if owner.mic_source:
            owner.mic = Mic(owner.mic_source)
        if owner.camera:
            owner.cam = Camera()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.info("screen stream: %s - %s", self.client_address[0], fmt % args)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    body = owner.stream.codec.page()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/info":
                    # The page asks before it listens: a stream started without
                    # touch must not swallow taps that were meant for a scroll.
                    body = json.dumps({"touch": owner.touch_surface is not None,
                                       "pen": owner.pen_surface is not None,
                                       "keyboard": owner.keyboard is not None,
                                       "sound": bool(owner.sound_sink),
                                       "mic": bool(owner.mic_source),
                                       "camera": bool(owner.camera)}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/audio":
                    if not owner.sound_sink:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/webm")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self._pump_audio()
                    return
                if self.path != "/stream":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", owner.stream.codec.content_type)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self._pump()

            def do_POST(self):
                """Touch, pen or keys: one small JSON frame from the phone."""
                if self.path == "/camera":
                    if owner.cam is None:
                        self.send_error(404)
                        return
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                        frame = self.rfile.read(length)
                    except OSError:
                        return
                    if not frame:                  # the person turned the camera off
                        owner.cam.close()
                    else:
                        try:
                            owner.cam.push(frame)
                        except Exception:  # noqa: BLE001 - the graph can refuse a frame
                            self.send_error(500)
                            return
                    self.send_response(204)
                    self.end_headers()
                    return
                if self.path == "/mic":
                    if owner.mic is None:
                        self.send_error(404)
                        return
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                        chunk = self.rfile.read(length)
                    except OSError:
                        return
                    if not chunk:                  # the person turned the microphone off
                        owner.mic.close()
                    else:
                        try:
                            owner.mic.push(chunk)
                        except Exception:  # noqa: BLE001 - the graph can refuse a buffer
                            self.send_error(500)
                            return
                    self.send_response(204)
                    self.end_headers()
                    return
                if self.path == "/touch" and owner.touch_surface is not None:
                    self._read_json(lambda payload: owner.touch_surface.update(
                        payload.get("points") or []))
                elif self.path == "/pen" and owner.pen_surface is not None:
                    self._read_json(owner.pen_surface.update)
                elif self.path == "/key" and owner.keyboard is not None:
                    self._read_json(self._type)
                else:
                    self.send_error(404)

            def _type(self, payload) -> None:
                text = payload.get("text")
                if text:
                    owner.keyboard.type(str(text))
                count = payload.get("backspace")
                if count:
                    owner.keyboard.backspace(int(count))

            def _read_json(self, apply) -> None:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, TypeError):
                    self.send_error(400)
                    return
                except OSError:
                    return                       # the phone hung up mid-frame
                try:
                    apply(payload)
                except OSError:
                    return                       # the device went away mid-frame
                self.send_response(204)
                self.end_headers()

            def _pump(self) -> None:
                """Encode until the viewer leaves, the encoder stops twice, or the share ends."""
                recovery = Recovery()
                while True:
                    pipe = Gst.parse_launch(pipeline_description(
                        os.dup(owner.stream.fd), owner.stream.node, owner.stream.width,
                        owner.stream.height, owner.stream.fps, owner.stream.bitrate,
                        owner.stream.codec))
                    sink = pipe.get_by_name("out")
                    stop = threading.Event()

                    def on_sample(appsink):
                        buf = appsink.emit("pull-sample").get_buffer()
                        ok, info = buf.map(Gst.MapFlags.READ)
                        if ok:
                            try:
                                self.wfile.write(owner.stream.codec.frame(bytes(info.data)))
                                recovery.playing()
                            except OSError:
                                stop.set()
                            finally:
                                buf.unmap(info)
                        return Gst.FlowReturn.OK

                    sink.connect("new-sample", on_sample)
                    pipe.set_state(Gst.State.PLAYING)
                    reason = None
                    while not stop.wait(0.5):
                        message = pipe.get_bus().pop_filtered(
                            Gst.MessageType.ERROR | Gst.MessageType.EOS)
                        if message is None:
                            continue
                        reason = (message.parse_error()[0].message
                                  if message.type == Gst.MessageType.ERROR else "EOS")
                        break
                    pipe.set_state(Gst.State.NULL)
                    if reason is None:
                        logger.info("screen stream: viewer %s left", self.client_address[0])
                        return
                    closed = bool(owner.stream.session_closed())
                    restart, said = recovery.stopped(session_closed=closed)
                    logger.warning("screen stream: %s (%s)", said, reason)
                    if restart:
                        continue
                    if closed and owner.on_fatal is not None:
                        owner.on_fatal(said)
                    return

            def _pump_audio(self) -> None:
                """Desktop audio until the listener leaves; one restart if it stops."""
                recovery = Recovery()
                while True:
                    pipe = Gst.parse_launch(audio_pipeline(owner.sound_sink))
                    sink = pipe.get_by_name("out")
                    stop = threading.Event()

                    def on_sample(appsink):
                        buf = appsink.emit("pull-sample").get_buffer()
                        ok, info = buf.map(Gst.MapFlags.READ)
                        if ok:
                            try:
                                self.wfile.write(bytes(info.data))
                                recovery.playing()
                            except OSError:
                                stop.set()
                            finally:
                                buf.unmap(info)
                        return Gst.FlowReturn.OK

                    sink.connect("new-sample", on_sample)
                    pipe.set_state(Gst.State.PLAYING)
                    reason = None
                    while not stop.wait(0.5):
                        message = pipe.get_bus().pop_filtered(
                            Gst.MessageType.ERROR | Gst.MessageType.EOS)
                        if message is None:
                            continue
                        reason = (message.parse_error()[0].message
                                  if message.type == Gst.MessageType.ERROR else "EOS")
                        break
                    pipe.set_state(Gst.State.NULL)
                    if reason is None:
                        return
                    restart, said = recovery.stopped(session_closed=False)
                    logger.warning("screen stream: audio %s (%s)", said, reason)
                    if not restart:
                        return

        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        logger.info("screen stream: http://%s:%d", self.host, self.port)
        self.httpd.serve_forever()

    def stop(self) -> None:
        if getattr(self, "mic", None) is not None:
            self.mic.close()
        if getattr(self, "cam", None) is not None:
            self.cam.close()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()


# --- starting one -------------------------------------------------------------

def pick_transport(requested: str) -> str:
    """Which road to take: the one asked for, or the best there is.

    `SHANI_LINK_SCREEN_TRANSPORT` is what the page settled on before it wrote
    the sentence the person just read. Honouring it is the difference between a
    stream that agrees with its own page and one that picks a different road
    because the Wi-Fi blinked between two probes.
    """
    if requested != "auto":
        return requested
    settled = os.environ.get("SHANI_LINK_SCREEN_TRANSPORT", "").strip()
    if settled in ("usb", "wifi", "bluetooth"):
        return settled
    return choose()[0] or "bluetooth"


def _arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show this computer's screen on the phone.")
    parser.add_argument("--transport", choices=("auto", "usb", "wifi", "bluetooth"),
                        default="auto",
                        help="how to carry the picture (default: adb, by cable or Wi-Fi, when a phone is there)")
    parser.add_argument("--codec", choices=tuple(CODECS), default="webm",
                        help="webm (a <video>) or mjpeg (an <img>, no JavaScript)")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="stop after this many seconds (default: run until Ctrl+C)")
    parser.add_argument("--window", action="store_true",
                        help="share one window (the person picks it) instead of a monitor")
    parser.add_argument("--record", metavar="FILE",
                        help="record the portal's stream to FILE (webm) and exit")
    parser.add_argument("--stills", metavar="FILE",
                        help="save one frame to FILE (png) and exit")
    parser.add_argument("--touch", action="store_true",
                        help="let fingers on the phone tap and drag on this computer "
                             "(also SHANI_LINK_SCREEN_TOUCH=1)")
    return parser.parse_args(argv)


def _input_surfaces(enabled: bool):
    """The virtual devices the phone can drive, or Nones with the reasons.

    Touch is off unless asked for: a stream that swallowed every tap would be
    worse than one that does not, and a palm on the phone is not a click.
    """
    if not enabled:
        return None, None, None
    ok, why = touch.available()
    if not ok:
        print(f"Touch is off: {why}", flush=True)
        return None, None, None
    surface = pen = board = None
    try:
        screen = touch.TouchScreen()
        screen.open()
        surface = touch.TouchSurface(screen)
    except touch.TouchUnavailable as exc:
        print(f"Touch is off: {exc}", flush=True)
    try:
        device = touch.Pen()
        device.open()
        pen = touch.PenSurface(device)
    except touch.TouchUnavailable as exc:
        print(f"The pen is off: {exc}", flush=True)
    try:
        board = touch.Keyboard()
        board.open()
    except touch.TouchUnavailable as exc:
        print(f"Typing is off: {exc}", flush=True)
    if surface is not None or pen is not None or board is not None:
        print("Touch and typing are on: the phone drives this computer", flush=True)
    return surface, pen, board


def _portal_problem(exc: BaseException) -> str:
    """What the portal's refusal means, in a sentence somebody can act on."""
    said = str(exc)
    if "ScreenCast" in said and ("No such interface" in said or "not provided" in said):
        return ("this desktop has no screen-share portal - install "
                "xdg-desktop-portal-gnome (or xdg-desktop-portal-gtk) and try again")
    return f"the portal refused ({said})"


def _run(host: str, width: int, height: int, fps: int, bitrate: int, codec: Codec,
         seconds: float = 0.0, touch_surface=None, pen_surface=None, keyboard=None,
         sound_sink: str = "", window: bool = False) -> None:
    """Open the portal, serve one stream, and stay until it ends."""
    cast = ScreenCast()
    try:
        fd, node = cast.start(window=window)                  # the person answers here
        stream = Stream(fd, node, width, height, fps, bitrate, codec,
                        session_closed=lambda: cast.closed)
        done = threading.Event()

        def fatal(said: str) -> None:
            print(f"Stopping: {said}", flush=True)
            done.set()

        server = StreamServer(stream, host, on_fatal=fatal, touch_surface=touch_surface,
                              pen_surface=pen_surface, keyboard=keyboard,
                              sound_sink=sound_sink)
        threading.Thread(target=server.serve, daemon=True, name="screen-stream").start()
        print(f"Open http://{host}:{PORT} on the phone", flush=True)
        timer = None
        try:
            if seconds > 0:
                timer = threading.Timer(seconds, done.set)
                timer.start()
            while not done.wait(1.0):
                if cast.closed:
                    print("The screen share ended on this computer.", flush=True)
                    break
        except KeyboardInterrupt:
            pass
        finally:
            if timer is not None:
                timer.cancel()
            server.stop()
    finally:
        cast.close()


def _record(path: str, window: bool, seconds: float) -> None:
    """Record the portal's stream to a file, and stop when the person does."""
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    cast = ScreenCast()
    try:
        fd, node = cast.start(window=window)                  # the person answers here
        pipe = Gst.parse_launch(record_pipeline(fd, node, BLUETOOTH_FPS, BLUETOOTH_BITRATE, path))
        pipe.set_state(Gst.State.PLAYING)
        print(f"Recording to {path}", flush=True)
        deadline = None if seconds <= 0 else threading.Timer(seconds, lambda: pipe.send_event(
            Gst.Event.new_eos()))
        if deadline is not None:
            deadline.start()
        try:
            while True:
                message = pipe.get_bus().timed_pop_filtered(500 * Gst.MSECOND,
                                                            Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message is not None:
                    if message.type == Gst.MessageType.ERROR:
                        print(f"Recording failed: {message.parse_error()[0].message}", flush=True)
                    break
                if cast.closed:
                    print("The screen share ended on this computer.", flush=True)
                    break
        except KeyboardInterrupt:
            pass
        finally:
            if deadline is not None:
                deadline.cancel()
            pipe.send_event(Gst.Event.new_eos())
            pipe.set_state(Gst.State.NULL)
    finally:
        cast.close()


def _stills(path: str, window: bool) -> None:
    """Save one frame as a PNG, and stop."""
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    Gst.init(None)
    cast = ScreenCast()
    try:
        fd, node = cast.start(window=window)                  # the person answers here
        pipe = Gst.parse_launch(still_pipeline(fd, node))
        sink = pipe.get_by_name("shot")
        loop = GLib.MainLoop()
        box = {}

        def on_sample(appsink):
            buf = appsink.emit("pull-sample").get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                try:
                    with open(path, "wb") as handle:
                        handle.write(bytes(info.data))
                except OSError as exc:
                    box["error"] = str(exc)
                finally:
                    buf.unmap(info)
            box["done"] = True
            loop.quit()
            return Gst.FlowReturn.OK

        def on_bus(_bus, message):
            if message.type == Gst.MessageType.ERROR:
                box["error"] = message.parse_error()[0].message
                loop.quit()
            return True

        sink.connect("new-sample", on_sample)
        pipe.get_bus().add_watch(GLib.PRIORITY_DEFAULT, on_bus)
        pipe.set_state(Gst.State.PLAYING)
        GLib.timeout_add_seconds(60, loop.quit)
        loop.run()
        pipe.set_state(Gst.State.NULL)
        if box.get("error"):
            print(f"Could not take the picture: {box['error']}", flush=True)
            sys.exit(1)
        if not box.get("done"):
            print("No picture arrived in a minute.", flush=True)
            sys.exit(1)
        print(f"Saved {path}", flush=True)
    finally:
        cast.close()


def _serve(args: argparse.Namespace, codec: Codec, surface, pen_surface, keyboard,
           sound_sink: str = "", window: bool = False) -> None:
    transport = pick_transport(args.transport)

    if transport in ("usb", "wifi"):
        serial, problem = phone_serial()
        if problem:
            sys.exit(f"No phone adb can reach: {problem}.")
        size = android.display_size(serial) or FALLBACK_SIZE
        width, height = fit_to_phone(*size)
        print(f"Serving at the phone's size: {width}x{height}", flush=True)
        with adb_link(PORT, serial) as (ok, note):
            if not ok:
                sys.exit(note)
            if note:
                print(note, flush=True)
            try:
                _run(USB_HOST, width, height, USB_FPS, USB_BITRATE, codec, args.seconds,
                     surface, pen_surface, keyboard, sound_sink, window)
            except Exception as exc:  # noqa: BLE001 - the portal is a desktop service
                print(f"Stopping: {_portal_problem(exc)}", flush=True)
                sys.exit(1)
        return

    host = bluetooth_address()
    if host is None:
        sys.exit("No Bluetooth network: turn on Bluetooth tethering on the phone and connect to it first.")
    try:
        _run(host, BLUETOOTH_WIDTH, BLUETOOTH_HEIGHT, BLUETOOTH_FPS, BLUETOOTH_BITRATE,
             codec, args.seconds, surface, pen_surface, keyboard, sound_sink, window)
    except Exception as exc:  # noqa: BLE001
        print(f"Stopping: {_portal_problem(exc)}", flush=True)
        sys.exit(1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _arguments()
    codec = create_codec(args.codec)
    ready, why = gstreamer_ready(args.codec)
    if not ready:
        sys.exit(why)

    if args.record:
        path = os.path.abspath(os.path.expanduser(args.record))
        if not path.endswith(".webm"):
            path += ".webm"
        _record(path, args.window, args.seconds)
        return

    if args.stills:
        path = os.path.abspath(os.path.expanduser(args.stills))
        if not path.endswith(".png"):
            path += ".png"
        _stills(path, args.window)
        return

    wanted = args.touch or os.environ.get("SHANI_LINK_SCREEN_TOUCH", "").lower() in ("1", "true", "yes")
    surface, pen_surface, keyboard = _input_surfaces(wanted)
    sound = default_sink()
    if sound:
        print(f"Sound is available: {sound}", flush=True)
    else:
        print("Sound is off: no default audio sink (pw-metadata)", flush=True)
    try:
        _serve(args, codec, surface, pen_surface, keyboard, sound, args.window)
    finally:
        for device in (surface, pen_surface, keyboard):
            if device is not None:
                device.close()


if __name__ == "__main__":
    main()
