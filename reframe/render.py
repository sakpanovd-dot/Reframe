from __future__ import annotations

from bisect import bisect_right
import cv2
import numpy as np


class Renderer:
    def __init__(self, plan):
        self.plan = plan
        self.times = [key.time for key in plan.keyframes]

    def _linked(self, a, b):
        keys = self.plan.keyframes
        return 0 <= a < len(keys) and 0 <= b < len(keys) and keys[a].scene == keys[b].scene \
            and keys[a].mode == keys[b].mode == "crop"

    def position(self, t):
        """Return (mode, x, y, crop width) at time t.

        Crop positions follow a Catmull-Rom spline through the keyframes of one
        shot, so the virtual camera keeps its speed between samples instead of
        stopping at each of them. Panel and graphic layouts are held, never slid.
        """
        p = self.plan
        i = max(0, bisect_right(self.times, t) - 1)
        key = p.keyframes[i]
        x, y = key.x, key.y
        if self._linked(i, i + 1):
            nxt = p.keyframes[i + 1]
            a = min(1, max(0, (t - key.time) / max(.001, nxt.time - key.time)))
            before = p.keyframes[i - 1] if self._linked(i - 1, i) else key
            after = p.keyframes[i + 2] if self._linked(i + 1, i + 2) else nxt

            def spline(p0, p1, p2, p3):
                value = .5 * (2 * p1 + (p2 - p0) * a + (2 * p0 - 5 * p1 + 4 * p2 - p3) * a * a
                              + (3 * p1 - p0 - 3 * p2 + p3) * a ** 3)
                # Keep overshoot within the segment so the crop never leaves the subject's envelope.
                return min(max(value, min(p1, p2)), max(p1, p2))
            x = spline(before.x, key.x, nxt.x, after.x)
            y = spline(before.y, key.y, nxt.y, after.y)
        width = key.w if key.mode == "panel" and key.w else p.crop_width
        return key.mode, round(x), round(y), width

    def frame(self, image, t):
        p = self.plan
        mode, x, y, width = self.position(t)
        size = (p.output_width, p.output_height)
        if mode in ("crop", "panel"):
            cw = max(2, round(width))
            ch = min(image.shape[0], max(2, round(cw * p.crop_height / p.crop_width)))
            x = min(max(0, x), image.shape[1] - cw)
            y = min(max(0, y), image.shape[0] - ch)
            crop = image[y:y + ch, x:x + cw]
            interpolation = cv2.INTER_AREA if cw >= size[0] else cv2.INTER_CUBIC
            return cv2.resize(crop, size, interpolation=interpolation)
        fit = min(size[0] / image.shape[1], size[1] / image.shape[0], 1.)
        fw, fh = max(1, round(image.shape[1] * fit)), max(1, round(image.shape[0] * fit))
        # When the foreground covers the output, the background is invisible.
        if (fw, fh) == size:
            return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        foreground = cv2.resize(image, (fw, fh), interpolation=cv2.INTER_AREA)
        x, y = (size[0] - fw) // 2, (size[1] - fh) // 2
        if mode == "graphic":
            # Title cards and packshots sit on flat colour: continue their own
            # edges so the vertical frame looks designed, not letterboxed.
            # Work at 1/8 size: the extended edges are smooth, and a full-size blur
            # costs ~30x a crop frame.
            k = 8
            small = cv2.resize(foreground, (max(1, fw // k), max(1, fh // k)), interpolation=cv2.INTER_AREA)
            sy, sx = y // k, x // k
            small = cv2.copyMakeBorder(small, sy, max(0, size[1] // k - small.shape[0] - sy),
                                       sx, max(0, size[0] // k - small.shape[1] - sx), cv2.BORDER_REPLICATE)
            small = cv2.GaussianBlur(small, (0, 0), max(1., size[0] / 40 / k))
            background = cv2.resize(small, size, interpolation=cv2.INTER_LINEAR)
            background[y:y + fh, x:x + fw] = foreground
            return background
        # Blur a small cover image, then enlarge; the foreground is never enlarged.
        scale = max(size[0] / image.shape[1], size[1] / image.shape[0])
        cover_w, cover_h = round(image.shape[1] * scale), round(image.shape[0] * scale)
        tiny = cv2.resize(image, (max(1, cover_w // 12), max(1, cover_h // 12)))
        tiny = cv2.GaussianBlur(tiny, (0, 0), 7)
        background = cv2.resize(tiny, (cover_w, cover_h))
        bx, by = (cover_w - size[0]) // 2, (cover_h - size[1]) // 2
        background = (background[by:by + size[1], bx:bx + size[0]].astype(np.float32) * .65).astype(np.uint8)
        background[y:y + fh, x:x + fw] = foreground
        return background
