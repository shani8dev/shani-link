"""The touch end of the screen stream: what the phone posts, and what the page asks.

The server is the real one on a loopback port; the encoder is not opened, because
a POST never touches it - only `/stream` does.
"""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from shani_link import screen_stream as ss


class Recorder:
    """A stand-in for the uinput surface: it keeps what it was given."""

    def __init__(self):
        self.frames = []

    def update(self, points):
        self.frames.append(points)


def _serve(touch_surface, port=0, pen_surface=None, keyboard=None, sound_sink=""):
    stream = ss.Stream(0, 0)
    server = ss.StreamServer(stream, "127.0.0.1", port=port, touch_surface=touch_surface,
                             pen_surface=pen_surface, keyboard=keyboard,
                             sound_sink=sound_sink)
    threading.Thread(target=server.serve, daemon=True, name="screen-stream-test").start()
    deadline = time.monotonic() + 5
    while server.httpd is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.httpd is not None, "the server never listened"
    return server, server.httpd.server_address[1]


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def _post(url, body, content_type="application/json"):
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def server():
    recorder = Recorder()
    streamer, port = _serve(recorder)
    try:
        yield recorder, f"http://127.0.0.1:{port}"
    finally:
        streamer.stop()


def test_the_page_is_told_whether_touch_is_on(server):
    recorder, base = server
    status, body = _get(base + "/info")
    assert status == 200 and json.loads(body) == {"touch": True, "pen": False, "keyboard": False,
                                                   "sound": False}


def test_the_page_asks_before_it_listens():
    streamer, port = _serve(None)
    base = f"http://127.0.0.1:{port}"
    try:
        status, body = _get(base + "/info")
        assert status == 200 and json.loads(body) == {"touch": False, "pen": False,
                                                       "keyboard": False, "sound": False}
        assert _post(base + "/touch", b'{"points": []}') == 404
    finally:
        streamer.stop()


def test_a_frame_of_touches_reaches_the_surface(server):
    recorder, base = server
    points = [{"id": 1, "x": 0.25, "y": 0.5, "pressure": 0.8}]
    status = _post(base + "/touch", json.dumps({"points": points}).encode())
    assert status == 204
    assert recorder.frames == [points]


def test_a_lift_is_an_empty_frame_not_a_disconnect(server):
    recorder, base = server
    _post(base + "/touch", json.dumps({"points": [{"id": 1, "x": 0.1, "y": 0.1}]}).encode())
    _post(base + "/touch", json.dumps({"points": []}).encode())
    assert recorder.frames[-1] == []


def test_a_body_that_is_not_json_is_a_sentence_and_not_a_traceback(server):
    _recorder, base = server
    assert _post(base + "/touch", b"not json") == 400
    assert _post(base + "/touch", b'{"points": [{"id": "x"}]}') == 204   # the surface drops it


def test_the_page_carries_the_touch_layer(server):
    _recorder, base = server
    _status, body = _get(base + "/")
    text = body.decode()
    assert 'id="touch"' in text and '"/touch"' in text and '"/info"' in text
    assert "touch-action:none" in text

class PenRecorder:
    def __init__(self):
        self.frames = []

    def update(self, point):
        self.frames.append(point)


class KeyRecorder:
    def __init__(self):
        self.text = []
        self.backspaces = []

    def type(self, text):
        self.text.append(text)

    def backspace(self, count):
        self.backspaces.append(count)


def test_the_pen_endpoint_reaches_the_pen_surface():
    recorder = PenRecorder()
    streamer, port = _serve(Recorder(), pen_surface=recorder)
    base = f"http://127.0.0.1:{port}"
    try:
        assert _post(base + "/pen", json.dumps({"x": 0.5, "y": 0.5}).encode()) == 204
        assert recorder.frames == [{"x": 0.5, "y": 0.5}]
        assert _post(base + "/pen", json.dumps({"lift": True}).encode()) == 204
        assert recorder.frames[-1] == {"lift": True}
    finally:
        streamer.stop()


def test_the_typing_endpoint_reaches_the_keyboard():
    board = KeyRecorder()
    streamer, port = _serve(Recorder(), keyboard=board)
    base = f"http://127.0.0.1:{port}"
    try:
        assert _post(base + "/key", json.dumps({"text": "hello"}).encode()) == 204
        assert _post(base + "/key", json.dumps({"backspace": 2}).encode()) == 204
        assert board.text == ["hello"] and board.backspaces == [2]
    finally:
        streamer.stop()


def test_the_page_is_told_which_devices_are_on():
    recorder = Recorder()
    streamer, port = _serve(recorder, pen_surface=PenRecorder(), keyboard=KeyRecorder(),
                            sound_sink="test-sink")
    base = f"http://127.0.0.1:{port}"
    try:
        _status, body = _get(base + "/info")
        assert json.loads(body) == {"touch": True, "pen": True, "keyboard": True,
                                    "sound": True}
    finally:
        streamer.stop()


# --- the speaker: desktop audio to the phone -----------------------------------

def test_the_default_sink_comes_from_pipewire_not_pactl(monkeypatch):
    sample = ("update: id:0 key:'default.audio.sink' value:'{\"name\":\"alsa_output.x.Speaker__sink\"}' "
              "type:'Spa:String:JSON'\n")
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": sample})())
    assert ss.default_sink() == "alsa_output.x.Speaker__sink"


def test_no_pipewire_is_no_sink_not_a_traceback(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("no pw-metadata")
    monkeypatch.setattr(ss.subprocess, "run", boom)
    assert ss.default_sink() == ""


def test_the_audio_pipeline_is_opus_in_webm_from_that_sink():
    line = ss.audio_pipeline("alsa_output.x.Speaker__sink")
    assert "target-object=alsa_output.x.Speaker__sink" in line
    assert "opusenc" in line and "webmmux streamable=true" in line


def test_the_page_says_sound_is_available_and_serves_404_without_a_sink():
    streamer, port = _serve(Recorder())
    base = f"http://127.0.0.1:{port}"
    try:
        _status, body = _get(base + "/info")
        assert json.loads(body)["sound"] is False
        assert _get(base + "/audio") if False else True
        try:
            _get(base + "/audio")
            raise AssertionError("audio without a sink must not be served")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        streamer.stop()


def test_the_page_carries_the_sound_button():
    streamer, port = _serve(Recorder())
    base = f"http://127.0.0.1:{port}"
    try:
        _status, body = _get(base + "/")
        text = body.decode()
        assert 'id="sound"' in text and '"/audio"' in text
    finally:
        streamer.stop()
