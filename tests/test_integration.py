import threading
from pathlib import Path

import av
import numpy as np
import pytest

from reframe.contracts import ExportRequest, Cancelled, ReframeError
from reframe.media import binary, command, probe, raw_probe, frames
from reframe.pipeline import run
from reframe.presets import load_presets

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("видео с пробелами")
    path = folder / "исходник.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=1.2",
             "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1.2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
    return folder, path


@pytest.mark.parametrize("preset", list(load_presets()))
@pytest.mark.parametrize("container", ["mp4", "mov"])
def test_all_exports_with_audio(media, preset, container):
    folder, source = media
    target = folder / (preset.replace(":", "x") + "." + container)
    run(ExportRequest(source, target, preset, container, cpu_only=True, fit_only=True), threading.Event())
    info = probe(target)
    assert abs(info.duration - 1.2) <= .04
    assert info.audio_index is not None
    with av.open(str(target)) as video:
        assert len(list(video.decode(video=0))) == 30


def test_cancellation_preserves_existing_result(media):
    folder, source = media
    target = folder / "protected.mp4"
    target.write_bytes(b"previous result")
    cancel = threading.Event()
    def stop(event):
        if event.stage == "Создание видео":
            cancel.set()
    with pytest.raises(Cancelled):
        run(ExportRequest(source, target, overwrite=True, cpu_only=True, fit_only=True), cancel, stop)
    assert target.read_bytes() == b"previous result"
    assert not list(folder.glob(".reframe-*"))


def test_no_audio_and_rotation(media):
    folder, source = media
    rotated = folder / "rotated.mov"
    command([binary("ffmpeg"), "-v", "error", "-y", "-display_rotation:v:0", "90", "-i", str(source), "-an", "-c:v", "copy", str(rotated)])
    info = probe(rotated)
    assert (info.width, info.height) == (180, 320)
    assert info.audio_index is None
    frame = next(frames(info))[2]
    assert frame.shape[:2] == (320, 180)
    output = folder / "rotated-out.mp4"
    run(ExportRequest(rotated, output, cpu_only=True, fit_only=True), threading.Event())
    assert probe(output).rotation == 0


def test_variable_frame_rate(media):
    folder, source = media
    vfr = folder / "vfr.mkv"
    command([binary("ffmpeg"), "-v", "error", "-y", "-i", str(source), "-an",
             "-vf", "select='if(lt(t,0.6),1,not(mod(n,2)))'", "-fps_mode", "vfr", "-c:v", "libx264", str(vfr)])
    output = folder / "vfr-out.mov"
    run(ExportRequest(vfr, output, container="mov", cpu_only=True, fit_only=True), threading.Event())
    original_times = [t for t, _, _ in frames(probe(vfr))]
    output_times = [t for t, _, _ in frames(probe(output))]
    assert len(output_times) == len(original_times)
    assert np.max(np.abs(np.array(original_times) - output_times)) < .001


def test_hdr_rejected(media):
    folder, source = media
    hdr = folder / "hdr.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-i", str(source), "-an", "-c:v", "libx264",
             "-x264-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc", str(hdr)])
    with pytest.raises(ReframeError, match="HDR"):
        probe(hdr)
