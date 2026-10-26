from __future__ import annotations

from bisect import bisect_right
import cv2
import numpy as np


class Renderer:
    def __init__(self, plan):
        self.plan = plan
        self.times = [key.time for key in plan.keyframes]

    def position(self, t):
        p = self.plan
        i = max(0, bisect_right(self.times, t) - 1)
        key = p.keyframes[i]
        x, y = key.x, key.y
        if i + 1 < len(p.keyframes):
            nxt = p.keyframes[i + 1]
            if key.scene == nxt.scene and key.mode == nxt.mode:
                a = min(1, max(0, (t - key.time) / max(.001, nxt.time - key.time)))
                a = a * a * (3 - 2 * a)
                x, y = x + a * (nxt.x - x), y + a * (nxt.y - y)
        return key.mode, round(x), round(y)

    def frame(self, image, t):
        p = self.plan
        mode, x, y = self.position(t)
        size = (p.output_width, p.output_height)
        if mode == "crop":
            x = min(max(0, x), p.width - p.crop_width)
            y = min(max(0, y), p.height - p.crop_height)
            crop = image[y:y + p.crop_height, x:x + p.crop_width]
            return cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
        fit = min(size[0] / image.shape[1], size[1] / image.shape[0], 1.)
        fw, fh = max(1, round(image.shape[1] * fit)), max(1, round(image.shape[0] * fit))
        # When the foreground covers the output, the blurred background is invisible.
        if (fw, fh) == size:
            return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        # Blur a small cover image, then enlarge; the foreground is never enlarged.
        scale = max(size[0] / image.shape[1], size[1] / image.shape[0])
        cover_w, cover_h = round(image.shape[1] * scale), round(image.shape[0] * scale)
        tiny = cv2.resize(image, (max(1, cover_w // 12), max(1, cover_h // 12)))
        tiny = cv2.GaussianBlur(tiny, (0, 0), 7)
        background = cv2.resize(tiny, (cover_w, cover_h))
        x, y = (cover_w - size[0]) // 2, (cover_h - size[1]) // 2
        background = (background[y:y + size[1], x:x + size[0]].astype(np.float32) * .65).astype(np.uint8)
        foreground = cv2.resize(image, (fw, fh), interpolation=cv2.INTER_AREA)
        x, y = (size[0] - fw) // 2, (size[1] - fh) // 2
        background[y:y + fh, x:x + fw] = foreground
        return background
