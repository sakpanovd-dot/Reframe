import numpy as np
import pytest

from reframe.contracts import Detection
from reframe.detector import YoloXDetector


def detector_with_stub(monkeypatch, results):
    detector = YoloXDetector.__new__(YoloXDetector)
    detector._previous_image = None
    detector._previous_objects = ()
    calls = []

    def detect(image):
        calls.append(image.copy())
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(detector, '_detect', detect)
    return detector, calls


def test_exact_repeat_owns_pixels_and_returns_independent_list(monkeypatch):
    obj = Detection((0, 0, 2, 2), .9, 0)
    detector, calls = detector_with_stub(monkeypatch, iter([[obj], []]))
    image = np.zeros((4, 4, 3), np.uint8)
    detector.detect(image).clear()
    assert detector.detect(image.copy()) == [obj]
    assert len(calls) == 1
    image[0, 0, 0] = 1
    assert detector.detect(image) == []
    assert len(calls) == 2
    assert detector.detect(image) == []
    assert len(calls) == 2


@pytest.mark.parametrize('changed', [
    np.zeros((2, 8, 3), np.uint8),
    np.zeros((4, 4, 3), np.float32),
])
def test_geometry_and_dtype_changes_require_inference(monkeypatch, changed):
    detector, calls = detector_with_stub(monkeypatch, iter([[], []]))
    detector.detect(np.zeros((4, 4, 3), np.uint8))
    detector.detect(changed)
    assert len(calls) == 2


def test_failed_inference_is_retried(monkeypatch):
    detector, calls = detector_with_stub(monkeypatch, iter([[], RuntimeError('failed'), []]))
    detector.detect(np.zeros((4, 4, 3), np.uint8))
    changed = np.ones((4, 4, 3), np.uint8)
    with pytest.raises(RuntimeError):
        detector.detect(changed)
    assert detector.detect(changed) == []
    assert len(calls) == 3


def test_only_last_image_is_retained(monkeypatch):
    detector, calls = detector_with_stub(monkeypatch, iter([[], [], []]))
    first = np.zeros((4, 4, 3), np.uint8)
    detector.detect(first)
    detector.detect(np.ones_like(first))
    detector.detect(first)
    assert len(calls) == 3
