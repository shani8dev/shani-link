"""PipeWire, as the Bluetooth phone calls need it: wpctl, the graph, and the
Audio section of `wpctl status`. Taken as they were from Shani Chronoa
(pipewire.py, skills/audio_output.py), where they were tested."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

_DUMP_TIMEOUT = 10
WPCTL_TIMEOUT = 10
_LINE = re.compile(r"^[\s│├└─]*?(\*)?\s*(\d+)\.\s+(.+?)(?:\s+\[vol:[^\]]*\])?\s*$")


def _graph_nodes() -> list[dict]:
    """Node property dicts from the live graph, or [] if unavailable."""
    if not shutil.which("pw-dump"):
        return []
    try:
        raw = subprocess.run(
            ["pw-dump"],
            capture_output=True,
            text=True,
            timeout=_DUMP_TIMEOUT,
            check=False,
        ).stdout
        objects = json.loads(raw or "[]")
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
        logger.debug("pw-dump did not yield a usable graph: %s", e)
        return []
    if not isinstance(objects, list):
        return []
    return [
        obj["info"]["props"]
        for obj in objects
        if isinstance(obj, dict)
        and str(obj.get("type", "")).endswith("Node")
        and isinstance(obj.get("info"), dict)
        and isinstance(obj["info"].get("props"), dict)
    ]


def run_wpctl(*args: str) -> "subprocess.CompletedProcess[str]":
    """Run `wpctl` and return the completed process.

    Deliberately does not check for the binary's presence: every caller already
    decides for itself what a missing wpctl means for its own answer (a mute
    request and a device listing fail differently), and collapsing that decision
    here would take the wording away from the code that needs to explain it.
    """
    return subprocess.run(
        ["wpctl", *args],
        capture_output=True, text=True, timeout=WPCTL_TIMEOUT, check=False,
    )


def echo_cancel_active() -> bool:
    """Whether an echo-cancelling node is present in the running graph.

    Reported rather than enabled. PipeWire's `module-echo-cancel` is a SPA hook
    attached to a source node by a `filter-chain.conf.d` fragment, so it cannot
    be switched on from here at runtime - only detected. Shani Chronoa's
    barge-in monitor is why that matters: with no AEC, continuous-VAD barge-in
    hears Chronoa's own playback and interrupts itself.
    """
    for props in _graph_nodes():
        haystack = " ".join(
            str(props.get(key, "")) for key in ("node.name", "factory.name", "node.group")
        ).lower()
        if "echo" in haystack and "cancel" in haystack:
            return True
    return False


def parse_status(stdout: str) -> "dict[str, list[dict]]":
    """{'Devices': [...], 'Sinks': [...], 'Sources': [...]} from `wpctl status`.

    `Devices` was added for `bluetooth_call`, which needs the *cards* - the
    Bluetooth ones carry the `Profile 0: hfp_hf` lines that decide where a
    call's voice goes, and no sink or source line has one. The section state
    machine below already walked past them.
    """
    out = {"Devices": [], "Sinks": [], "Sources": []}
    in_audio, section = False, None
    for raw in stdout.splitlines():
        if raw and not raw[0].isspace() and raw[0] not in "│├└":
            in_audio = raw.strip() == "Audio"
            section = None
            continue
        if not in_audio:
            continue
        header = raw.strip(" │├└─")
        if header.endswith(":"):
            name = header[:-1].strip()
            section = name if name in out else None
            continue
        if section is None:
            continue
        m = _LINE.match(raw)
        if m:
            out[section].append({"id": int(m.group(2)), "name": m.group(3).strip(),
                                 "default": bool(m.group(1))})
    return out
