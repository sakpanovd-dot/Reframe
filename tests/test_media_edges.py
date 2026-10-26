import threading
from pathlib import Path
import av
import numpy as np
import pytest

from reframe.contracts import ExportRequest, ReframeError
from reframe.media import binary, command, probe, frames, raw_probe
from reframe.pipeline import run


def make_video(path, extra=(), codec="libx264"):
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=1",
             "-c:v", codec, *extra, str(path)])


@pytest.mark.integration
def test_anamorphic_pixels(tmp_path):
    source = tmp_path / "sar.mp4"
    make_video(source, ["-vf", "setsar=2/1"])
    info = probe(source)
    assert (info.width, info.height) == (640, 180)
    assert next(frames(info))[2].shape == (180, 640, 3)
    target = tmp_path / "square.mp4"
    run(ExportRequest(source, target, "1:1", cpu_only=True, fit_only=True), threading.Event())
    assert probe(target).sar == 1


@pytest.mark.integration
@pytest.mark.parametrize("container,codec", [("mkv", "libx265"), ("mov", "libx265"), ("avi", "libx264")])
def test_input_containers_and_hevc(tmp_path, container, codec):
    source = tmp_path / ("source." + container)
    make_video(source, codec=codec)
    target = tmp_path / "result.mp4"
    run(ExportRequest(source, target, cpu_only=True, fit_only=True), threading.Event())
    assert abs(probe(target).duration - probe(source).duration) <= .04


@pytest.mark.integration
@pytest.mark.parametrize("container,codec,extra", [
    ("mp4", "libaom-av1", ["-cpu-used", "8"]),
    ("webm", "libvpx-vp9", ["-deadline", "realtime", "-cpu-used", "8"]),
    ("avi", "mpeg4", []),
    ("mov", "prores_ks", ["-profile:v", "0"]),
])
def test_additional_decodable_codecs_are_accepted_and_exported(tmp_path, container, codec, extra):
    source = tmp_path / ("source." + container)
    make_video(source, extra=extra, codec=codec)
    source_info = probe(source)
    target = tmp_path / "result.mp4"
    run(ExportRequest(source, target, cpu_only=True, fit_only=True), threading.Event())
    assert abs(probe(target).duration - source_info.duration) <= 1 / source_info.fps
    assert raw_probe(target)["streams"][0]["codec_name"] == "h264"


def test_probe_rejects_real_decoder_failure(tmp_path, monkeypatch):
    source = tmp_path / "broken-codec.mp4"
    make_video(source)
    def broken_frames(*a, **kw):
        raise RuntimeError("Decoder unavailable")
        yield
    monkeypatch.setattr("reframe.media.frames", broken_frames)
    with pytest.raises(ReframeError, match="декодировать видео.*h264"):
        probe(source)


def test_probe_propagates_cancellation_during_decode(tmp_path, monkeypatch):
    from reframe.contracts import Cancelled
    source = tmp_path / "cancelled.mp4"
    make_video(source)
    def cancelled_frames(*a, **kw):
        raise Cancelled()
        yield
    monkeypatch.setattr("reframe.media.frames", cancelled_frames)
    with pytest.raises(Cancelled):
        probe(source)


@pytest.mark.integration
def test_audio_offset_is_preserved(tmp_path):
    source = tmp_path / "delayed.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=320x180:r=25:d=2",
             "-itsoffset", "0.5", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
             "-c:v", "libx264", "-c:a", "aac", str(source)])
    target = tmp_path / "result.mp4"
    run(ExportRequest(source, target, cpu_only=True, fit_only=True), threading.Event())
    def onset(path):
        # Decode actual audio content; stream start metadata alone is insufficient.
        with av.open(str(path)) as container:
            for frame in container.decode(audio=0):
                values = frame.to_ndarray()
                peak = np.max(np.abs(values), axis=0)
                indexes = np.flatnonzero(peak > .03)
                if len(indexes):
                    return float(frame.pts * frame.time_base) + indexes[0] / frame.sample_rate
        raise AssertionError("No tone decoded")
    assert abs(onset(source) - onset(target)) < .1


def test_invalid_file_and_source_unchanged(tmp_path):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"not a video")
    with pytest.raises(ReframeError):
        probe(source)
    assert source.read_bytes() == b"not a video"


def test_detect_scene_cut_on_exact_frame(monkeypatch):
    from reframe.analysis import analyze
    from reframe.contracts import VideoInfo
    fake = [(i / 25, .04, np.full((180, 320, 3), 0 if i < 7 else 255, np.uint8)) for i in range(20)]
    monkeypatch.setattr("reframe.analysis.frames", lambda *a, **kw: iter(fake))
    info = VideoInfo(Path("fake"), 320, 180, .8, 25, 0, None)
    result = analyze(info, None, threading.Event(), lambda e: None)
    assert next(s.time for s in result.samples if s.scene == 1) == 7 / 25
