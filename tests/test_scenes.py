import cv2
import numpy as np

from reframe.scenes import SceneDetector, motion_boxes
from reframe.analysis import analyze
from reframe.contracts import AnalysisResult, Detection, Sample, VideoInfo
from reframe.planner import make_plan
from reframe.presets import load_presets


def texture(seed):
    rng = np.random.default_rng(seed)
    return cv2.resize(rng.integers(0, 256, (18, 32, 3), dtype=np.uint8), (160, 90))


def test_similar_palette_cut_resets_at_exact_frame(monkeypatch, tmp_path):
    a, b = texture(3), texture(7)
    images = [a] * 4 + [b] * 4
    monkeypatch.setattr('reframe.analysis.frames', lambda *a, **kw:
                        iter((i / 25, .04, image) for i, image in enumerate(images)))
    info = VideoInfo(tmp_path / 'test.mp4', 160, 90, .32, 25, 0, None)
    result = analyze(info, None, None, lambda e: None)
    assert [s.time for s in result.samples if s.scene == 1][0] == .16
    assert max(s.scene for s in result.samples) == 1
    assert not result.samples[1].motion_boxes  # A cut is not foreground motion.


def test_flash_and_exposure_do_not_split_scene():
    image = texture(5)
    flash = np.full_like(image, 255)
    detector = SceneDetector()
    assert not detector.update(image, flash)
    assert not detector.update(flash, image)
    assert not detector.update(image, image)
    brighter = np.clip(image.astype(np.int16) + 15, 0, 255).astype(np.uint8)
    assert not detector.update(brighter, brighter)


def test_camera_translation_does_not_split_scene():
    image = texture(10)
    detector = SceneDetector()
    for offset in range(0, 22, 3):
        moved = cv2.warpAffine(image, np.float32([[1, 0, offset], [0, 1, 0]]),
                               (160, 90), borderMode=cv2.BORDER_REFLECT)
        assert not detector.update(moved, moved)


def test_large_foreground_motion_with_continuing_background_is_not_a_cut():
    image = texture(19)
    detector = SceneDetector()
    for x in [0, 0, 25, 25, 50, 50, 75, 75, 40, 40]:
        moved = image.copy()
        moved[10:80, x:x + 65] = 0
        assert not detector.update(moved, moved)


def test_motion_evidence_detects_activity_and_ignores_exposure():
    a = np.full((90, 160, 3), 50, np.uint8)
    b = a.copy()
    b[30:75, 120:155] = 220
    boxes = motion_boxes(a, b)
    assert any(x1 < .8 and x2 > .9 for x1, y1, x2, y2 in boxes)
    assert not motion_boxes(a, a + 20)


def test_unexplained_activity_outside_static_detection_keeps_full_frame():
    samples = [Sample(i / 5, 0, [Detection((280, 100, 320, 220), .9, 14, 1)],
                      [(480, 100, 635, 330)]) for i in range(20)]
    plan = make_plan(AnalysisResult(640, 360, 4, samples), load_presets()['9:16'])
    assert all(k.mode == 'fit' for k in plan.keyframes)


def test_activity_inside_subject_crop_still_allows_tracking():
    samples = [Sample(i / 5, 0, [Detection((280, 100, 320, 220), .9, 14, 1)],
                      [(270, 90, 330, 230)]) for i in range(20)]
    plan = make_plan(AnalysisResult(640, 360, 4, samples), load_presets()['9:16'])
    assert sum(k.mode == 'crop' for k in plan.keyframes) >= 16


def test_subject_motion_trail_does_not_disable_following():
    samples = [Sample(i / 5, 0,
                      [Detection((80 + i * 16, 100, 120 + i * 16, 230), .95, 0, 1)],
                      [(40 + i * 16, 80, 180 + i * 16, 280)]) for i in range(20)]
    plan = make_plan(AnalysisResult(640, 360, 4, samples), load_presets()['9:16'])
    assert sum(k.mode == 'crop' for k in plan.keyframes) >= 16
    assert not any(k.reason == 'motion_outside_crop' for k in plan.keyframes)
