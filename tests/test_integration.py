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


@pytest.mark.parametrize("upscale, size", [(False, (90, 160)), (True, (1080, 1920))])
def test_upscale_export_size(media, upscale, size):
    folder, source = media
    target = folder / f"upscale-{upscale}.mp4"
    run(ExportRequest(source, target, cpu_only=True, fit_only=True, upscale=upscale), threading.Event())
    info = probe(target)
    assert (info.width, info.height) == size
    assert info.audio_index is not None and abs(info.duration - 1.2) <= .04


@pytest.mark.parametrize('outcome', ['success', 'report_denied', 'cancelled'])
def test_editorial_export_and_review_publication(media, monkeypatch, outcome):
    import json
    from reframe.contracts import AnalysisResult, Sample
    folder, source = media
    target = folder / f'editorial-{outcome}.mp4'
    diagnostic = target.with_suffix('.json')
    existing_report = folder / 'previous-review.html'
    existing_report.write_text('previous report', encoding='utf-8')
    monkeypatch.setattr('reframe.pipeline.YoloXDetector', lambda *a: None)
    monkeypatch.setattr('reframe.pipeline.analyze', lambda *a: AnalysisResult(320, 180, 1.2, [Sample(0, 0, [])]))
    original_open = Path.open
    if outcome == 'report_denied':
        def guarded_open(path, *args, **kwargs):
            if '.review-' in path.name:
                raise PermissionError('Report destination denied')
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', guarded_open)
    events, cancel = [], threading.Event()
    def emit(event):
        events.append(event)
        if outcome == 'cancelled' and event.stage == 'Проверка результата':
            cancel.set()
    request = ExportRequest(source, target, cpu_only=True, diagnostics=diagnostic)
    if outcome == 'cancelled':
        with pytest.raises(Cancelled):
            run(request, cancel, emit)
        assert not target.exists()
        assert not any(e.kind in ('review', 'done') for e in events)
        assert not list(folder.glob(target.stem + '.review-*.html'))
    else:
        run(request, cancel, emit)
        assert probe(target).audio_index is not None
        assert len(list(frames(probe(target)))) == 30
        assert [e.kind for e in events if e.kind != 'progress'] == ['review', 'done']
        detail = json.loads(events[-2].detail)
        assert detail['count'] == 1
        if outcome == 'success':
            report = Path(detail['path']).read_text(encoding='utf-8')
            assert '00:00.000 — 00:01.200' in report
            assert 'Главный объект не обнаружен' in report
            assert not detail['error']
        else:
            assert detail['path'] == '' and 'отчёт' in detail['error']
        data = json.loads(diagnostic.read_text(encoding='utf-8'))
        assert data['strategy'] == 'editorial' and len(data['review']) == 1
    assert existing_report.read_text(encoding='utf-8') == 'previous report'
    assert not list(folder.glob('.reframe-*'))


@pytest.mark.parametrize('preset', ['9:16', '16:9'])
def test_cancellation_preserves_existing_result(media, preset):
    folder, source = media
    target = folder / "protected.mp4"
    target.write_bytes(b"previous result")
    cancel = threading.Event()
    def stop(event):
        if event.stage == "Создание видео":
            cancel.set()
    with pytest.raises(Cancelled):
        run(ExportRequest(source, target, preset=preset, overwrite=True, cpu_only=True, fit_only=True), cancel, stop)
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


@pytest.mark.parametrize('preset', ['9:16', '16:9'])
def test_variable_frame_rate(media, preset):
    folder, source = media
    vfr = folder / "vfr.mkv"
    command([binary("ffmpeg"), "-v", "error", "-y", "-i", str(source), "-an",
             "-vf", "select='if(lt(t,0.6),1,not(mod(n,2)))'", "-fps_mode", "vfr", "-c:v", "libx264", str(vfr)])
    output = folder / ("vfr-out-" + preset.replace(':', 'x') + ".mov")
    run(ExportRequest(vfr, output, preset=preset, container="mov", cpu_only=True, fit_only=True), threading.Event())
    original_times = [t for t, _, _ in frames(probe(vfr))]
    output_times = [t for t, _, _ in frames(probe(output))]
    assert len(output_times) == len(original_times)
    assert np.max(np.abs(np.array(original_times) - output_times)) < .001


@pytest.mark.parametrize('container', ['mp4', 'mov'])
def test_matching_aspect_exports_without_model_or_analysis(media, monkeypatch, container):
    import json
    folder, source = media
    target = folder / ('no-analysis.' + container)
    diagnostic = folder / ('no-analysis-' + container + '.json')
    def unexpected(*args, **kwargs):
        raise AssertionError('Matching aspect must not load the model or analyze scenes')
    monkeypatch.setattr('reframe.pipeline.YoloXDetector', unexpected)
    monkeypatch.setattr('reframe.pipeline.analyze', unexpected)
    events = []
    run(ExportRequest(source, target, '16:9', container, cpu_only=True,
                      diagnostics=diagnostic), threading.Event(), events.append)
    info = probe(target)
    assert (info.width, info.height) == (320, 180)
    assert info.audio_index is not None
    assert abs(info.duration - 1.2) <= .04
    assert len(list(frames(info))) == 30
    data = json.loads(diagnostic.read_text(encoding='utf-8'))
    assert data['analysis']['backend'] == 'skipped_same_ratio'
    assert len(data['plan']['keyframes']) == 1
    assert data['plan']['keyframes'][0]['reason'] == 'same_ratio'
    assert events[-1].kind == 'done'


def test_different_aspect_still_requires_analysis(media, monkeypatch):
    folder, source = media
    target = folder / 'requires-analysis.mp4'
    def missing(*args):
        raise ReframeError('Model required')
    monkeypatch.setattr('reframe.pipeline.YoloXDetector', missing)
    with pytest.raises(ReframeError, match='Model required'):
        run(ExportRequest(source, target, '9:16', cpu_only=True), threading.Event())
    assert not target.exists()
    assert not list(folder.glob('.reframe-*'))


def test_hdr_rejected(media):
    folder, source = media
    hdr = folder / "hdr.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-i", str(source), "-an", "-c:v", "libx264",
             "-x264-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc", str(hdr)])
    with pytest.raises(ReframeError, match="HDR"):
        probe(hdr)
