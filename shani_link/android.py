"""An Android phone over adb (USB or Wi-Fi debugging): info, battery, screenshot,
apps, files both ways, open a link.

Moved from Shani Chronoa's `android_device` skill, which keeps only its consent
(`phone-control-enabled`) and calls `_run` here. The Android tab of the app
calls the functions directly: a person pressing a button is the asking.

A fixed set of read-mostly verbs, deliberately not taps and not `adb shell`: a
phone holds its owner's messages, photos and location. The phone itself is the
first consent - adb only reaches a device whose owner turned on USB debugging
and accepted this computer's key.

Every function answers with a sentence a person can read; nothing raises for an
ordinary failure (no adb, no phone, a locked screen, a timeout).

Two questions answer in values rather than sentences, because a program rather
than a person asks them: `ready_serial()` (which phone a cable can reach),
`ready_devices()` (which phones, and whether each is on a cable or on Wi-Fi) and
`display_size()` (how big its picture is) - all used by `screen_stream`.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

ACTIONS = ("devices", "info", "screenshot", "apps", "push", "pull", "open_url")
_TIMEOUT = 60
#: where a file sent to the phone lands unless told otherwise
PHONE_DOWNLOADS = "/sdcard/Download/"
_URL = re.compile(r"^https?://[^\s'\"]+$")
NO_ADB = "adb (android-tools) is not installed."


class PathProblem(ValueError):
    """A path that must not be used, with the reason to tell the person."""


def available() -> bool:
    return shutil.which("adb") is not None


def _adb(args, serial: str = "", binary: bool = False, timeout: int = _TIMEOUT):
    argv = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(argv, capture_output=True, timeout=timeout, check=False, text=not binary)


def _devices() -> "List[Tuple[str, str, str]]":
    """`(serial, state, model)` for every device `adb devices -l` lists."""
    proc = _adb(["devices", "-l"])
    out = []
    for line in (proc.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
            out.append((parts[0], parts[1], model.replace("_", " ")))
    return out


def _state_hint(state: str) -> str:
    return {"unauthorized": "the phone has not accepted this computer yet - unlock it and allow USB debugging",
            "offline": "the phone is connected but offline - reconnect the cable"}.get(state, f"the device is {state}")


def _pick(serial: str) -> "Tuple[str, str]":
    """(serial, problem): the one ready device, or why there is none."""
    devs = _devices()
    ready = [d for d in devs if d[1] == "device"]
    if serial:
        match = [d for d in devs if d[0] == serial]
        if not match:
            return "", f"no device {serial!r} is connected"
        return (serial, "") if match[0][1] == "device" else ("", _state_hint(match[0][1]))
    if not devs:
        return "", ("no Android device is connected - plug it in with USB debugging on "
                    "(Settings > Developer options), or pair it with 'adb pair'")
    if not ready:
        return "", _state_hint(devs[0][1])
    if len(ready) > 1:
        return "", "more than one device is connected: " + ", ".join(f"{d[0]} ({d[2]})" for d in ready) + \
            "; say which with serial"
    return ready[0][0], ""


def ready_serial() -> "Tuple[str, str]":
    """(serial, problem): the one phone `adb devices` says is ready, or why none is.

    The screen stream asks before it offers the cable: one phone, ready, is the
    whole condition under which `adb reverse` means anything. Same answer as the
    verbs get, with the state spelled out for a person.
    """
    return _pick("")


def ready_devices() -> "List[Tuple[str, str]]":
    """(serial, "usb"|"wifi") for every phone adb can reach right now.

    `adb connect` always makes a serial of `host:port`, so the colon is the
    difference between a cable and a wireless phone. The verbs still want one
    named phone (`ready_serial`); this is for a caller with a preference.
    """
    return [(serial, "wifi" if ":" in serial else "usb")
            for serial, state, _model in _devices() if state == "device"]


#: `stay_on_while_plugged_in` is a bitmask: what each bit is for.
STAY_AWAKE_BITS = {"AC": 1, "USB": 2, "Wireless": 4}


def powered_by(serial: str) -> "Tuple[str, int]":
    """(what the phone is charging from, its stay-awake bit), or ("", 0).

    The setting is a bitmask, so telling a phone on a Qi pad to "stay on while
    plugged in over USB" is a setting that does nothing - the phone dims anyway.
    """
    battery = _shell(serial, "dumpsys", "battery")
    for name, bit in STAY_AWAKE_BITS.items():
        if re.search(rf"{name} powered:\s*true", battery):
            return name, bit
    return "", 0


def _shell(serial: str, *cmd: str) -> str:
    return (_adb(["shell", *cmd], serial).stdout or "").strip()


def notifications(serial: str, limit: int = 20) -> str:
    """The phone's notifications, as they are: app, title, text.

    `dumpsys notification --noredact`, read only - nothing here posts, dismisses
    or replies to anything. The format is checked against Android 16:
    `NotificationRecord(... pkg=<package> ...)` followed by
    `android.title=String (<title>)` and `android.text=String (<text>)`.
    """
    out = _shell(serial, "dumpsys", "notification", "--noredact")
    records = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("NotificationRecord("):
            package = re.search(r"pkg=([\w.]+)", line)
            records.append({"pkg": package.group(1) if package else "",
                            "title": "", "text": ""})
        elif records:
            for key, field in (("android.title", "title"), ("android.text", "text")):
                if line.startswith(key + "=") and not records[-1][field]:
                    value = line.split("=", 1)[1].strip()
                    wrapped = re.match(r"(?:String|SpannableString)\s*\((.*)\)$", value)
                    records[-1][field] = wrapped.group(1) if wrapped else value
    lines = [f"- {r['pkg'] or 'an app'}: "
             + (" — ".join(x for x in (r["title"], r["text"])
                           if x and x not in ("null", "String (null)")) or "(no text)")
             for r in records
             if (r["title"] and r["title"] != "null") or (r["text"] and r["text"] != "null")]
    if not lines:
        return "No notifications are showing on the phone."
    return f"{len(lines)} notification(s):\n" + "\n".join(lines[:limit])


def telephony(serial: str) -> str:
    """The line: operator, registration, radio and signal, as one sentence.

    Read from `dumpsys telephony.registry`, checked against Android 16: the
    state is `mVoiceRegState=0(IN_SERVICE)`, the operator
    `mOperatorAlphaLong=`, the radio `getRilVoiceRadioTechnology=20(NR_SA)`,
    and the signal `ssRsrp = -96`. The first block is SIM slot 0; a second
    phone with no SIM says POWER_OFF, which is the phone's answer, not a fault.
    """
    out = _shell(serial, "dumpsys", "telephony.registry")
    voice = re.search(r"mVoiceRegState=\d+\((\w+)\)", out)
    operator = re.search(r"mOperatorAlphaLong=([^,\n}]+)", out)
    radio = re.search(r"getRilVoiceRadioTechnology=\d+\((\w+)\)", out)
    rsrp = re.search(r"ssRsrp\s*=\s*(-?\d+)", out)
    if not (voice or operator):
        return "The phone did not say anything about its line."
    parts = [f"on {operator.group(1).strip()}" if operator else "operator unknown"]
    parts.append("registered" if voice and voice.group(1) == "IN_SERVICE"
                 else f"not registered ({voice.group(1).lower()})" if voice else "registration unknown")
    if radio:
        parts.append({"NR_SA": "5G", "LTE": "4G", "UMTS": "3G", "GSM": "2G"}.get(
            radio.group(1), radio.group(1)))
    if rsrp:
        parts.append(f"signal {rsrp.group(1)} dBm")
    return "; ".join(parts) + "."


def wifi(serial: str) -> str:
    """The phone's Wi-Fi: network, signal, link speed and band, as a sentence."""
    out = _shell(serial, "dumpsys", "wifi")
    ssid = re.search(r'SSID:\s*"([^"]+)"', out) or re.search(r"SSID:\s*([^,\n]+)", out)
    rssi = re.search(r"RSSI:\s*(-?\d+)", out)
    speed = re.search(r"Link speed:\s*(\d+)\s*Mbps", out)
    freq = re.search(r"Frequency:\s*(\d+)\s*MHz", out)
    if not (ssid or rssi):
        return "The phone did not say anything about its Wi-Fi."
    parts = [f"connected to {ssid.group(1)}" if ssid else "Wi-Fi is on"]
    if rssi:
        level = int(rssi.group(1))
        parts.append(f"signal {level} dBm "
                     f"({'weak' if level < -75 else 'fair' if level < -65 else 'good'})")
    if speed:
        parts.append(f"link speed {speed.group(1)} Mbit/s")
    if freq:
        parts.append("5 GHz" if int(freq.group(1)) > 4000 else "2.4 GHz")
    return "; ".join(parts) + "."


def thermal(serial: str) -> str:
    """The phone's own temperatures: CPU, GPU and skin."""
    out = _shell(serial, "dumpsys", "thermalservice")
    found = dict((name, value) for value, name in
                 re.findall(r"Temperature\{mValue=([\d.]+), mType=\d+, mName=(\w+)", out))
    if not found:
        return "The phone did not report any temperatures."
    shown = ", ".join(f"{name.lower()} {float(value):.1f} °C"
                     for name, value in found.items() if name.lower() in ("cpu", "gpu", "skin"))
    return (shown or "the phone reported temperatures it did not name") + "."


def display_size(serial: str) -> "Optional[Tuple[int, int]]":
    """The phone's own pixel size as it reports it, or None when it will not say.

    An override wins over the physical size: an override is what the person
    actually sees, and a mirrored desktop should match the picture, not the
    panel. Landscape is not decided here - that belongs to whoever encodes.
    """
    override = physical = None
    for line in _shell(serial, "wm", "size").splitlines():
        if "size:" not in line:
            continue
        try:
            width_str, height_str = line.split("size:", 1)[1].strip().lower().split("x", 1)
            width, height = int(width_str), int(height_str)
        except ValueError:
            continue
        if width <= 0 or height <= 0:
            continue
        if line.strip().startswith("Override"):
            override = (width, height)
        else:
            physical = (width, height)
    return override or physical


def human_size(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(value) < 1024 or unit == "GiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"  # pragma: no cover


def in_home(raw: str) -> Path:
    """`raw` expanded and resolved, refused when it lands outside the home folder.

    What `_run` (the assistant's way in) uses for every path it is handed; the
    app passes paths a person chose in a file dialog and does not need it.
    """
    if raw is None or not str(raw).strip():
        raise PathProblem("No path was given.")
    path = Path(str(raw).strip()).expanduser().resolve()
    home = Path.home().resolve()
    if not path.is_relative_to(home):
        raise PathProblem(f"Not touching {path}: it is outside your home directory ({home}).")
    return path


def default_screenshot() -> Path:
    return Path.home() / "Pictures" / f"phone-{time.strftime('%Y%m%d-%H%M%S')}.png"


# --- the verbs: each takes a ready device's serial and answers a sentence ----------------

def list_devices() -> str:
    devs = _devices()
    if not devs:
        return "No Android device is connected."
    return "Connected:\n" + "\n".join(f"- {s} {m or ''} ({st if st != 'device' else 'ready'})"
                                     for s, st, m in devs)


def info(serial: str) -> str:
    """Model, Android version, battery and free storage."""
    prop = lambda k: _shell(serial, "getprop", k)  # noqa: E731
    battery = _shell(serial, "dumpsys", "battery")
    level = re.search(r"level:\s*(\d+)", battery)
    charging = re.search(r"(AC|USB|Wireless) powered:\s*true", battery)
    df = _shell(serial, "df", "-h", "/sdcard").splitlines()
    free = df[-1].split()[3] if len(df) > 1 and len(df[-1].split()) > 3 else "?"
    return (f"{prop('ro.product.manufacturer')} {prop('ro.product.model')}, Android "
            f"{prop('ro.build.version.release')} (security patch {prop('ro.build.version.security_patch')}); "
            f"battery {level.group(1) + '%' if level else '?'}"
            f"{', charging over ' + charging.group(1) if charging else ''}; {free} free on internal storage.")


def screenshot(serial: str, target: Optional[Path] = None) -> str:
    """The phone's screen as a PNG, in ~/Pictures unless `target` says where."""
    target = Path(target) if target is not None else default_screenshot()
    if target.exists():
        return f"{target} already exists."
    proc = _adb(["exec-out", "screencap", "-p"], serial, binary=True)
    if proc.returncode != 0 or not (proc.stdout or b"").startswith(b"\x89PNG"):
        return "The phone did not return a screenshot (is it unlocked?)."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(proc.stdout)
    return f"Saved the phone's screen to {target} ({human_size(len(proc.stdout))})."


def apps(serial: str, query: str = "") -> str:
    """The apps the owner installed (not the system's), filtered by `query`."""
    query = (query or "").lower()
    pkgs = sorted(line.split(":", 1)[1] for line in _shell(serial, "pm", "list", "packages", "-3").splitlines()
                  if line.startswith("package:"))
    pkgs = [p for p in pkgs if query in p.lower()]
    if not pkgs:
        return "No matching apps."
    return (f"{len(pkgs)} installed app(s){' matching ' + repr(query) if query else ''}:\n"
            + "\n".join(f"- {p}" for p in pkgs[:80]))


def push(serial: str, source: Path, dest: str = PHONE_DOWNLOADS) -> str:
    """Copy a file from this computer to the phone's shared storage."""
    source = Path(source)
    if not source.is_file():
        return f"{source} is not a file."
    dest = (dest or PHONE_DOWNLOADS).strip()
    if not dest.startswith("/sdcard/"):
        return "Files go under /sdcard/ (the phone's shared storage)."
    proc = _adb(["push", str(source), dest], serial, timeout=600)
    return f"Copied {source.name} to the phone ({dest})." if proc.returncode == 0 else \
        f"adb push failed: {(proc.stderr or proc.stdout).strip()[:200]}"


def pull(serial: str, remote: str, target: Path) -> str:
    """Copy a file from the phone's shared storage to `target` here."""
    remote = (remote or "").strip()
    if not remote.startswith("/sdcard/"):
        return "Give a path on the phone's shared storage, starting /sdcard/."
    target = Path(target)
    if target.exists():
        return f"{target} already exists."
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = _adb(["pull", remote, str(target)], serial, timeout=600)
    return f"Copied {remote} to {target}." if proc.returncode == 0 and target.exists() else \
        f"adb pull failed: {(proc.stderr or proc.stdout).strip()[:200]}"


def open_url(serial: str, url: str) -> str:
    """Open an http(s) link on the phone, in whatever opens links there."""
    url = (url or "").strip()
    if not _URL.match(url):
        return "Give an http:// or https:// link."
    out = _shell(serial, "am", "start", "-a", "android.intent.action.VIEW", "-d", url)
    return f"Opened {url} on the phone." if "Error" not in out else f"The phone refused: {out[:200]}"


def do(verb, serial: str, *args) -> str:
    """Run one verb for the app: the timeout and a missing adb said in words."""
    if not available():
        return NO_ADB
    try:
        return verb(serial, *args)
    except subprocess.TimeoutExpired:
        return "The phone did not answer in time."
    except OSError as exc:
        return f"adb could not be run: {exc}"


def _run(arguments: dict) -> str:
    """The `android_device` tool's arguments, answered (no consent here: Chronoa's
    skill asks its own before calling this)."""
    action = (arguments.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return f"action must be one of {', '.join(ACTIONS)}."
    if not available():
        return NO_ADB
    try:
        if action == "devices":
            return list_devices()
        serial, problem = _pick((arguments.get("serial") or "").strip())
        if problem:
            return f"Cannot reach a phone: {problem}."
        if action == "info":
            return info(serial)
        if action == "screenshot":
            output = arguments.get("output")
            return screenshot(serial, in_home(output) if output else in_home(str(default_screenshot())))
        if action == "apps":
            return apps(serial, arguments.get("query") or "")
        if action == "push":
            return push(serial, in_home(arguments.get("path") or ""), arguments.get("to") or PHONE_DOWNLOADS)
        if action == "pull":
            remote = (arguments.get("path") or "").strip()
            if not remote.startswith("/sdcard/"):
                return "Give a path on the phone's shared storage, starting /sdcard/."
            return pull(serial, remote, in_home(arguments.get("to") or f"~/Downloads/{remote.rsplit('/', 1)[-1]}"))
        return open_url(serial, arguments.get("url") or "")
    except PathProblem as exc:
        return str(exc)
    except subprocess.TimeoutExpired:
        return "The phone did not answer in time."
