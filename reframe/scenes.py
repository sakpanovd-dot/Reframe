"""Small-frame scene boundaries and conservative motion evidence."""
from collections import deque

import cv2
import numpy as np


def thumbnail(image):
    return cv2.resize(image, (160, 90))


def difference(a, b):
    a, b = a.astype(np.float32), b.astype(np.float32)
    raw = float(np.mean(np.abs(a - b)) / 255)
    # Exposure changes alone should not look like a new spatial composition.
    centered = float(np.mean(np.abs((a - a.mean(axis=(0, 1))) -
                                    (b - b.mean(axis=(0, 1))))) / 255)
    return raw, centered


def histogram(image):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 16], [0, 180, 0, 256])
    return cv2.normalize(hist, hist)


def background_continues(a, b):
    # Large foreground motion is not a cut when substantial parts of the
    # background remain unchanged. Ignore thin letterbox bands and corner logos.
    delta = np.mean(np.abs(a[10:80, 10:150].astype(np.float32) -
                           b[10:80, 10:150].astype(np.float32)), axis=2)
    blocks = delta.reshape(7, 10, 14, 10).mean(axis=(1, 3))
    return np.mean(blocks < 5) >= .35


def translated_difference(a, b):
    """Return residual after a reliable global translation, otherwise None."""
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if min(float(ga.std()), float(gb.std())) < 3:
        return None
    shift, response = cv2.phaseCorrelate(ga, gb)
    dx, dy = map(lambda v: int(round(v)), shift)
    if response < .2 or abs(dx) > 40 or abs(dy) > 22:
        return None
    h, w = ga.shape
    aa = a[max(0, -dy):min(h, h - dy), max(0, -dx):min(w, w - dx)]
    bb = b[max(0, dy):min(h, h + dy), max(0, dx):min(w, w + dx)]
    return difference(aa, bb)[1]


class SceneDetector:
    def __init__(self):
        self.previous = None
        self.recent = deque(maxlen=12)
        self.flash_return = False

    def update(self, current, following=None):
        old = self.previous
        self.previous = current
        if old is None:
            return False
        raw, centered = difference(old, current)
        hist = cv2.compareHist(histogram(old), histogram(current), cv2.HISTCMP_BHATTACHARYYA)
        # Animation often holds each drawing for several frames. A median of
        # those near-zero differences would label every new drawing a cut.
        baseline = float(np.percentile(self.recent, 90)) if self.recent else 0.
        candidate = ((hist > .65 and raw > .15) or raw > .42 or
                     (raw > .12 and centered > .10 and hist > .12 and raw > 4 * max(.015, baseline)) or
                     (hist > .45 and raw > .16 and centered > .07))
        if self.flash_return:
            self.flash_return = False
            return False
        # One-frame lookahead avoids splitting a flash into two unrelated scenes.
        if candidate and following is not None and difference(old, following)[0] < .035:
            self.flash_return = True
            return False
        if candidate and centered < .025 and hist < .65 and raw < .25:
            candidate = False
        if candidate:
            if background_continues(old, current):
                candidate = False
        if candidate:
            residual = translated_difference(old, current)
            if residual is not None and residual < .035:
                candidate = False
        # Keep recent large changes too. Clearing this history at a candidate
        # cut makes held animation drawings repeatedly trigger new cuts.
        self.recent.append(raw)
        return candidate


def motion_boxes(previous, current):
    """Significant changed regions in normalized coordinates, not object labels.

    A camera pan may also produce motion: keeping the full frame is intentional
    when the semantic detector cannot explain activity outside its chosen crop.
    """
    if previous is None:
        return []
    a = cv2.GaussianBlur(previous, (5, 5), 0).astype(np.float32)
    b = cv2.GaussianBlur(current, (5, 5), 0).astype(np.float32)
    delta = b - a
    delta -= np.median(delta, axis=(0, 1))
    mask = (np.max(np.abs(delta), axis=2) > 30).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    h, w = mask.shape
    boxes = []
    for x, y, bw, bh, area in stats[1:count]:
        if area >= h * w * .008:
            boxes.append((x / w, y / h, (x + bw) / w, (y + bh) / h))
    return boxes
