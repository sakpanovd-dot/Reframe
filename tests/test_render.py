import cv2
import numpy as np
import pytest

from reframe.contracts import Keyframe, ReframePlan
from reframe.render import Renderer


@pytest.mark.parametrize("output", [(320, 180), (160, 90), (180, 320)])
def test_fit_preserves_foreground_and_centers_it(output):
    image = np.random.default_rng(42).integers(0, 256, (180, 320, 3), dtype=np.uint8)
    width, height = output
    plan = ReframePlan(320, 180, 320, 180, width, height,
                       [Keyframe(0, 0, "fit", 0, 0)])
    actual = Renderer(plan).frame(image, 0)
    scale = min(width / 320, height / 180, 1.)
    fw, fh = round(320 * scale), round(180 * scale)
    x, y = (width - fw) // 2, (height - fh) // 2
    expected = cv2.resize(image, (fw, fh), interpolation=cv2.INTER_AREA)
    assert actual.shape == (height, width, 3)
    assert actual.dtype == np.uint8
    np.testing.assert_array_equal(actual[y:y + fh, x:x + fw], expected)
    np.testing.assert_array_equal(image, np.random.default_rng(42).integers(
        0, 256, (180, 320, 3), dtype=np.uint8))


def test_crop_camera_keeps_moving_through_keyframes():
    keys = [Keyframe(i * .2, 0, "crop", 100 + 50 * i, 0) for i in range(6)]
    renderer = Renderer(ReframePlan(640, 360, 198, 352, 198, 352, keys))
    xs = [renderer.position(t / 100)[1] for t in range(20, 81)]
    steps = np.diff(xs)
    assert steps.min() > 0  # no stop-and-go at every sample


def test_graphic_extends_flat_background_without_darkening():
    image = np.full((180, 320, 3), (120, 140, 40), np.uint8)
    image[80:100, 100:220] = 255
    plan = ReframePlan(320, 180, 100, 178, 90, 160, [Keyframe(0, 0, "graphic", 0, 0)])
    frame = Renderer(plan).frame(image, 0)
    assert frame.shape == (160, 90, 3)
    np.testing.assert_allclose(frame[5, 45], (120, 140, 40), atol=2)
    np.testing.assert_allclose(frame[-5, 45], (120, 140, 40), atol=2)


def test_panel_uses_its_own_crop_width():
    image = np.zeros((360, 640, 3), np.uint8)
    image[:, 220:420] = 200
    plan = ReframePlan(640, 360, 198, 352, 198, 352, [Keyframe(0, 0, "panel", 230, 2, "split_screen", 180)])
    frame = Renderer(plan).frame(image, 0)
    assert frame.shape == (352, 198, 3) and frame.min() == 200
