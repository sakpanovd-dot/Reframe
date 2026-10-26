from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from .contracts import AnalysisResult, Detection, ProgressEvent, Sample
from .media import frames, check_cancel


def iou(a, b) -> float:
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / max(union, 1)


class Tracker:
    def __init__(self):
        self.tracks = {}
        self.next_id = 0

    def update(self, objects: list[Detection], t: float) -> list[Detection]:
        self.tracks = {i: item for i, item in self.tracks.items() if t - item[1] <= .6}
        ids = list(self.tracks)
        assigned = {}
        if ids and objects:
            costs = np.full((len(ids), len(objects)), 10.)
            for row, track_id in enumerate(ids):
                old, old_time, velocity = self.tracks[track_id]
                predicted = np.array(old.box) + velocity * (t - old_time)
                for col, obj in enumerate(objects):
                    if old.category == obj.category:
                        costs[row, col] = 1 - iou(predicted, obj.box)
            rows, cols = linear_sum_assignment(costs)
            for row, col in zip(rows, cols):
                if costs[row, col] < .85:
                    assigned[col] = ids[row]
        tracked = []
        for index, obj in enumerate(objects):
            track_id = assigned.get(index)
            velocity = np.zeros(4)
            if track_id is None:
                track_id = self.next_id
                self.next_id += 1
            else:
                previous, old_time, old_velocity = self.tracks[track_id]
                velocity = .5 * old_velocity + .5 * (np.array(obj.box) - previous.box) / max(.001, t - old_time)
            obj = replace(obj, track_id=track_id)
            self.tracks[track_id] = obj, t, velocity
            tracked.append(obj)
        return tracked


def analyze(info, detector, cancel, emit) -> AnalysisResult:
    samples, tracker, scene = [], Tracker(), 0
    previous = None
    next_time = 0.0
    for t, _, image in frames(info, cancel, max_side=640):
        check_cancel(cancel)
        small = cv2.resize(image, (160, 90))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        histogram = cv2.calcHist([hsv], [0, 1], None, [32, 16], [0, 180, 0, 256])
        cv2.normalize(histogram, histogram)
        cut = False
        if previous is not None:
            diff = cv2.compareHist(previous[0], histogram, cv2.HISTCMP_BHATTACHARYYA)
            pixel_diff = np.mean(np.abs(small.astype(float) - previous[1].astype(float))) / 255
            if (diff > .65 and pixel_diff > .15) or pixel_diff > .42:
                scene += 1
                tracker = Tracker()
                cut = True
        previous = histogram, small
        if not cut and t + .001 < next_time:
            continue
        next_time = t + .2
        detected = detector.detect(image) if detector else []
        sx, sy = info.width / image.shape[1], info.height / image.shape[0]
        detected = [replace(o, box=(o.box[0] * sx, o.box[1] * sy, o.box[2] * sx, o.box[3] * sy)) for o in detected]
        objects = tracker.update(detected, t)
        samples.append(Sample(t, scene, objects))
        emit(ProgressEvent("Анализ видео", min(1, t / info.duration),
                           f"{detector.backend if detector else 'CPU'} · сцена {scene + 1}"))
    if not samples:
        from .contracts import ReframeError
        raise ReframeError("Не удалось декодировать кадры видео.")
    return AnalysisResult(info.width, info.height, info.duration, samples,
                          detector.backend if detector else "CPU")
