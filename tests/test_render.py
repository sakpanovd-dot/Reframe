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
