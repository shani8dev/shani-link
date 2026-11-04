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


def _shell(serial: str, *cmd: str) -> str:
    return (_adb(["shell", *cmd], serial).stdout or "").strip()


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
