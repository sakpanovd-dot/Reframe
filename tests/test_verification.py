import threading

import pytest

from reframe.contracts import ExportRequest, ReframeError, Cancelled
from reframe.export import verify
from reframe.media import binary, command, probe, raw_probe
from reframe.pipeline import run
from reframe.planner import make_plan
from reframe.presets import load_presets
from reframe.contracts import AnalysisResult, Sample


@pytest.fixture
def video(tmp_path):
    source = tmp_path / 'source.mp4'
    command([binary('ffmpeg'), '-v', 'error', '-n', '-f', 'lavfi', '-i',
             'testsrc2=s=320x180:r=25:d=2', '-f', 'lavfi', '-i', 'sine=duration=2',
             '-c:v', 'libx264', '-c:a', 'aac', '-movflags', '+faststart', str(source)])
    return source


def test_truncated_payload_with_valid_metadata_is_not_published(video, monkeypatch):
    from reframe import pipeline
    target = video.with_name('protected.mp4')
    target.write_bytes(b'previous result')
    mux = pipeline.mux_audio
    def corrupt(*args):
        mux(*args)
        path = args[2]
        data = path.read_bytes()
        path.write_bytes(data[:len(data) * 3 // 4])
        # The old metadata-only verification would accept this file.
        streams = raw_probe(path)['streams']
        assert float(streams[0]['duration']) == 2
        assert len(streams) == 2
    monkeypatch.setattr(pipeline, 'mux_audio', corrupt)
    with pytest.raises(ReframeError, match='полностью прочитать'):
        run(ExportRequest(video, target, preset='16:9', overwrite=True, cpu_only=True), threading.Event())
    assert target.read_bytes() == b'previous result'
    assert not list(video.parent.glob('.reframe-*'))


def test_cancellation_during_full_verification_is_not_wrapped(video, monkeypatch):
    import reframe.export as exporter
    info = probe(video)
    plan = make_plan(AnalysisResult(info.width, info.height, info.duration, [Sample(0, 0, [])]),
                     load_presets()['16:9'])
    def cancelled(*args):
        raise Cancelled()
    monkeypatch.setattr(exporter, 'command', cancelled)
    with pytest.raises(Cancelled):
        verify(info, plan, video, threading.Event())
