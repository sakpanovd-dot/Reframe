from __future__ import annotations

from dataclasses import replace
from contextlib import closing, nullcontext

import numpy as np
from scipy.optimize import linear_sum_assignment

from .contracts import AnalysisResult, Detection, ProgressEvent, Sample
from .media import frames, check_cancel
from .scenes import SceneDetector, flatness, motion_boxes, panels, thumbnail


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


def _with_following(info, cancel):
    decoded = frames(info, cancel, max_side=640)
    with closing(decoded) if hasattr(decoded, "close") else nullcontext(decoded):
        current = next(decoded, None)
        if current is None:
            return
        for following in decoded:
            yield current, following
            current = following
        yield current, None


def analyze(info, detector, cancel, emit, faces=None) -> AnalysisResult:
    samples, tracker, scene = [], Tracker(), 0
    previous = None
    boundaries = SceneDetector()
    activity = []
    next_time = 0.0
    for (t, _, image), following in _with_following(info, cancel):
        check_cancel(cancel)
        small = thumbnail(image)
        cut = boundaries.update(small, thumbnail(following[2]) if following is not None else None)
        if cut:
            scene += 1
            tracker = Tracker()
            activity = []
        else:
            activity.extend(motion_boxes(previous, small))
        previous = small
        if not cut and t + .001 < next_time:
            continue
        next_time = t + .2
        detected = detector.detect(image) if detector else []
        sx, sy = info.width / image.shape[1], info.height / image.shape[0]
        detected = [replace(o, box=(o.box[0] * sx, o.box[1] * sy, o.box[2] * sx, o.box[3] * sy)) for o in detected]
        objects = tracker.update(detected, t)
        found = [replace(f, box=(f.box[0] * sx, f.box[1] * sy, f.box[2] * sx, f.box[3] * sy))
                 for f in faces.detect(image)] if faces else []
        motion = [(x1 * info.width, y1 * info.height, x2 * info.width, y2 * info.height)
                  for x1, y1, x2, y2 in activity]
        samples.append(Sample(t, scene, objects, motion, panels(image), flatness(image), found))
        activity = []
        emit(ProgressEvent("Анализ видео", min(1, t / info.duration),
                           f"{detector.backend if detector else 'CPU'} · сцена {scene + 1}"))
    if not samples:
        from .contracts import ReframeError
        raise ReframeError("Не удалось декодировать кадры видео.")
    return AnalysisResult(info.width, info.height, info.duration, samples,
                          detector.backend if detector else "CPU")
