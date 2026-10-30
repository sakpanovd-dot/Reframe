import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from reframe.analysis import Tracker
from reframe.contracts import AnalysisResult, Detection, ExportRequest, Sample, ReframeError
from reframe.pipeline import validate_destination
from reframe.planner import dimensions, make_plan
from reframe.presets import load_presets
from reframe.render import Renderer


def scene(samples, width=640, height=360):
    return AnalysisResult(width, height, len(samples) / 5, samples)


def moving_samples(count=30, category=0):
    return [Sample(i / 5, 0, [Detection((80 + i * 10, 100, 120 + i * 10, 230), .95, category, 1)])
            for i in range(count)]


def test_tracking_retains_identity_after_short_occlusion():
    tracker = Tracker()
    first = tracker.update([Detection((10, 10, 50, 80), .9, 0)], 0)[0]
    second = tracker.update([Detection((15, 10, 55, 80), .9, 0)], .2)[0]
    tracker.update([], .4)
    third = tracker.update([Detection((25, 10, 65, 80), .9, 0)], .6)[0]
    assert first.track_id == second.track_id == third.track_id


def test_moving_subject_is_inside_crop_and_crop_really_used():
    samples = moving_samples()
    plan = make_plan(scene(samples), load_presets()["9:16"])
    renderer = Renderer(plan)
    crop_count = 0
    for sample in samples:
        mode, x, y, _ = renderer.position(sample.time)
        if mode == "crop":
            crop_count += 1
            x1, y1, x2, y2 = sample.objects[0].box
            assert x <= x1 and x2 <= x + plan.crop_width
            assert y <= y1 and y2 <= y + plan.crop_height
    assert crop_count >= len(samples) * .8


def test_ambiguous_people_use_full_frame():
    samples = [Sample(i / 5, 0, [Detection((50, 50, 120, 250), .95, 0, 1),
                                 Detection((520, 50, 590, 250), .95, 0, 2)]) for i in range(10)]
    plan = make_plan(scene(samples), load_presets()["9:16"])
    assert all(k.mode == "fit" for k in plan.keyframes)


def test_large_subject_and_unknown_scene_fit():
    samples = [Sample(0, 0, []), Sample(.2, 0, [Detection((0, 0, 640, 360), .99, 5, 0)])]
    assert all(k.mode == "fit" for k in make_plan(scene(samples), load_presets()["9:16"]).keyframes)


def test_same_ratio_preserves_content_without_detections():
    p = make_plan(scene([Sample(0, 0, [])]), load_presets()["16:9"])
    assert p.keyframes[0].mode == "crop"
    assert (p.crop_width, p.crop_height) == (640, 360)


def test_scene_cut_does_not_interpolate():
    from reframe.contracts import Keyframe, ReframePlan
    plan = ReframePlan(640, 360, 200, 360, 200, 360,
                       [Keyframe(0, 0, "crop", 0, 0), Keyframe(1, 1, "crop", 400, 0)])
    renderer = Renderer(plan)
    assert renderer.position(.99)[1] == 0
    assert renderer.position(1)[1] == 400


@pytest.mark.parametrize("preset", list(load_presets()))
def test_dimensions_no_upscale_exact_ratio(preset):
    p = load_presets()[preset]
    plan = make_plan(scene([Sample(0, 0, [])], 320, 240), p)
    assert plan.output_width <= plan.crop_width <= 320
    assert plan.output_height <= plan.crop_height <= 240
    assert plan.output_width * p.height == plan.output_height * p.width
    assert not plan.output_width % 2 and not plan.output_height % 2
    rendered = Renderer(plan).frame(np.full((240, 320, 3), 100, np.uint8), 0)
    assert rendered.shape == (plan.output_height, plan.output_width, 3)


@pytest.mark.parametrize("source, upscale, expected", [
    ((1920, 1080), False, (594, 1056, 594, 1056)),
    ((1920, 1080), True, (594, 1056, 1080, 1920)),
    ((3840, 2160), False, (1206, 2144, 1080, 1920)),
    ((3840, 2160), True, (1206, 2144, 1080, 1920)),
])
def test_dimensions_output_size_modes(source, upscale, expected):
    assert dimensions(*source, load_presets()["9:16"], upscale) == expected


@pytest.mark.parametrize("preset", list(load_presets()))
def test_upscale_renders_preset_size_and_enlarges_full_frame(preset):
    p = load_presets()[preset]
    plan = make_plan(scene([Sample(0, 0, [])], 320, 240), p, upscale=True)
    assert (plan.output_width, plan.output_height) == (p.width, p.height)
    image = np.full((240, 320, 3), 100, np.uint8)
    for mode in ("crop", "graphic", "fit"):
        plan.keyframes[0] = replace(plan.keyframes[0], mode=mode)
        rendered = Renderer(plan).frame(image, 0)
        assert rendered.shape == (p.height, p.width, 3)
    # Full frames are enlarged like crops, not left as a small picture on a dimmed background.
    fit = Renderer(plan).frame(np.full((240, 320, 3), 255, np.uint8), 0)
    bright = (fit.min(axis=2) > 250).mean()
    expected = min(p.width / 320, p.height / 240, p.width / plan.crop_width) ** 2 * 320 * 240 / (p.width * p.height)
    assert bright == pytest.approx(expected, abs=.02)


def test_new_preset_without_algorithm_changes(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text('[{"id":"2:3","title":"New","width":720,"height":1080}]')
    preset = load_presets(path)["2:3"]
    p = make_plan(scene(moving_samples()), preset)
    assert p.output_width * 3 == p.output_height * 2


def test_destination_protects_source_and_existing_output(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "output.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"existing")
    with pytest.raises(ReframeError, match="Исходное"):
        validate_destination(ExportRequest(source, source, overwrite=True))
    with pytest.raises(ReframeError, match="существует"):
        validate_destination(ExportRequest(source, output))
    assert source.read_bytes() == b"source" and output.read_bytes() == b"existing"


def test_failed_export_keeps_existing_file_and_cleans_temp(tmp_path, monkeypatch):
    from reframe.contracts import VideoInfo
    from reframe.pipeline import run
    source, target = tmp_path / "source.mp4", tmp_path / "result.mp4"
    source.write_bytes(b"original")
    target.write_bytes(b"previous result")
    monkeypatch.setattr("reframe.pipeline.probe", lambda *a: VideoInfo(source, 640, 360, 1, 25, 0, None))
    monkeypatch.setattr("reframe.pipeline.analyze", lambda *a: scene([Sample(0, 0, [])]))
    monkeypatch.setattr("reframe.pipeline.choose_encoder", lambda *a: "libx264")
    def fail(*args):
        raise OSError(28, "disk full")
    monkeypatch.setattr("reframe.pipeline.encode_video", fail)
    with pytest.raises(OSError):
        run(ExportRequest(source, target, overwrite=True, fit_only=True), threading.Event())
    assert source.read_bytes() == b"original"
    assert target.read_bytes() == b"previous result"
    assert not list(tmp_path.glob(".reframe-*"))


def test_nvenc_unavailable_falls_back(tmp_path, monkeypatch):
    from reframe.export import choose_encoder
    monkeypatch.setattr("reframe.export.av.open", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("No GPU")))
    assert choose_encoder(tmp_path, False) == "libx264"
