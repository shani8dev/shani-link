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


def _serve(touch_surface, port=0, pen_surface=None, keyboard=None, sound_sink="",
           mic_source="", camera=True):
    stream = ss.Stream(0, 0)
    server = ss.StreamServer(stream, "127.0.0.1", port=port, touch_surface=touch_surface,
                             pen_surface=pen_surface, keyboard=keyboard,
                             sound_sink=sound_sink, mic_source=mic_source,
                             camera=camera)
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
                                                   "sound": False, "mic": False, "camera": True}


def test_the_page_asks_before_it_listens():
    streamer, port = _serve(None)
    base = f"http://127.0.0.1:{port}"
    try:
        status, body = _get(base + "/info")
        assert status == 200 and json.loads(body) == {"touch": False, "pen": False,
                                                       "keyboard": False, "sound": False,
                                                       "mic": False, "camera": True}
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
                                    "sound": True, "mic": False, "camera": True}
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


# --- the microphone: the phone as a desktop microphone -------------------------

def test_the_microphone_is_the_loopback_card_when_it_is_loaded(monkeypatch):
    from shani_link import audio
    monkeypatch.setattr(audio, "nodes", lambda: [
        {"node.name": "alsa_output.platform-snd_aloop.7"},
        {"node.name": "alsa_input.platform-snd_aloop.7"}])
    source, sink = ss.microphone()
    assert source == "alsa_input.platform-snd_aloop.7"
    assert sink == "alsa_output.platform-snd_aloop.7"


def test_without_the_loopback_the_reason_names_the_module(monkeypatch):
    from shani_link import audio
    monkeypatch.setattr(audio, "nodes", lambda: [{"node.name": "alsa_output.x"}])
    source, why = ss.microphone()
    assert source == "" and "snd-aloop" in why, why


def test_the_microphone_is_raw_pcm_into_the_loopback():
    line = ss.mic_pipeline("alsa_output.platform-snd_aloop.0")
    assert "appsrc" in line and "S16LE,rate=48000,channels=1" in line
    assert "pipewiresink target-object=alsa_output.platform-snd_aloop.0" in line


def test_the_page_offers_the_microphone_only_when_there_is_one(monkeypatch):
    made = []

    class FakeMic:
        def __init__(self, sink):
            self.sink = sink
            self.data = []
            made.append(self)

        def push(self, data):
            self.data.append(data)
            return True

        def close(self):
            pass

    monkeypatch.setattr(ss, "Mic", FakeMic)
    streamer, port = _serve(Recorder(), mic_source="alsa_input.platform-snd_aloop.0")
    base = f"http://127.0.0.1:{port}"
    try:
        _status, body = _get(base + "/info")
        assert json.loads(body)["mic"] is True
        assert _post(base + "/mic", b"\x00\x01") == 204
        assert made and made[0].data == [b"\x00\x01"]
    finally:
        streamer.stop()


def test_mic_without_a_loopback_is_404_and_the_page_has_the_button():
    streamer, port = _serve(Recorder())
    base = f"http://127.0.0.1:{port}"
    try:
        assert _post(base + "/mic", b"\x00\x01") == 404
        _status, body = _get(base + "/")
        assert 'id="mic"' in body.decode() and '"/mic"' in body.decode()
    finally:
        streamer.stop()


# --- the camera: the phone as a PipeWire camera --------------------------------

def test_the_camera_is_a_pipewire_video_source_not_a_kernel_module():
    line = ss.camera_pipeline(1280, 720, 15)
    assert "pipewiresink mode=provide" in line
    assert "media.class=Video/Source" in line and "media.role=Camera" in line
    assert "jpegdec" in line and "width=1280,height=720,framerate=15/1" in line


def test_the_page_offers_the_camera_and_takes_frames(monkeypatch):
    made = []

    class FakeCamera:
        def __init__(self):
            self.data = []
            made.append(self)

        def push(self, frame):
            self.data.append(frame)
            return True

        def close(self):
            pass

    monkeypatch.setattr(ss, "Camera", FakeCamera)
    streamer, port = _serve(Recorder())
    base = f"http://127.0.0.1:{port}"
    try:
        _status, body = _get(base + "/info")
        assert json.loads(body)["camera"] is True
        assert _post(base + "/camera", b"\xff\xd8jpeg") == 204
        assert made and made[0].data == [b"\xff\xd8jpeg"]
        _status, body = _get(base + "/")
        assert 'id="cam"' in body.decode() and '"/camera"' in body.decode()
    finally:
        streamer.stop()


def test_a_stream_without_a_camera_refuses_frames():
    streamer, port = _serve(Recorder(), camera=False)
    base = f"http://127.0.0.1:{port}"
    try:
        _status, body = _get(base + "/info")
        assert json.loads(body)["camera"] is False
        assert _post(base + "/camera", b"\xff\xd8") == 404
    finally:
        streamer.stop()


def test_an_empty_frame_is_how_the_phone_stops_that_device(monkeypatch):
    made = []

    class FakeMic:
        def __init__(self, sink):
            self.closed = 0
            made.append(self)

        def push(self, data):
            return True

        def close(self):
            self.closed += 1

    monkeypatch.setattr(ss, "Mic", FakeMic)
    streamer, port = _serve(Recorder(), mic_source="alsa_input.platform-snd_aloop.7")
    base = f"http://127.0.0.1:{port}"
    try:
        assert _post(base + "/mic", b"\x00\x01") == 204
        assert _post(base + "/mic", b"") == 204
        assert made and made[0].closed == 1, "an empty post must close the encoder"
    finally:
        streamer.stop()


# --- recording the same stream to a file ---------------------------------------

def test_the_record_pipeline_is_the_webm_tail_into_a_file():
    line = ss.record_pipeline(9, 42, 8, 400_000, "/tmp/x.webm")
    assert "pipewiresrc fd=9 path=42" in line
    assert "vp8enc" in line and "webmmux streamable=true" in line
    assert "filesink location=/tmp/x.webm" in line
    assert "appsink" not in line


def test_the_still_pipeline_is_one_png_from_the_portal():
    line = ss.still_pipeline(5, 43)
    assert "pipewiresrc fd=5 path=43" in line
    assert "pngenc" in line and 'appsink name=shot' in line
    assert "filesink" not in line and "vp8enc" not in line
