"""The graph scan the Bluetooth phone asks about (`audio._graph_nodes`).

Machines without a running PipeWire are ordinary: `pw-dump` is missing or
answers nothing usable, and that must read as "no graph", never as a crash.
"""

import subprocess

from shani_link import audio


def test_a_graph_that_will_not_parse_is_no_graph(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/pw-dump")
    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", "connection refused"),
    )
    assert audio.echo_cancel_active() is False


def test_a_machine_without_pw_dump_has_no_graph(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    assert audio.echo_cancel_active() is False
