"""Offline scene composition for the primary 16:9 -> 9:16 editorial workflow.

Heuristics over detector tracks, not semantic understanding of dialogue or text.
"""
from collections import defaultdict
from itertools import groupby

import numpy as np

from .contracts import Keyframe, ReframePlan
from .planner import dimensions, excludes_activity, unexplained_activity


def applies(width, height, preset):
    return abs(width / height - 16 / 9) < .005 and preset.width * 16 == preset.height * 9


def make_editorial_plan(analysis, preset):
    w, h = analysis.width, analysis.height
    cw, ch, ow, oh = dimensions(w, h, preset)
    keys = []
    for scene, iterator in groupby(analysis.samples, lambda s: s.scene):
        samples = list(iterator)
        tracks = defaultdict(list)
        for sample in samples:
            for obj in sample.objects:
                if obj.confidence >= .35:
                    tracks[obj.track_id].append((sample.time, obj))
        scores = {}
        for track, entries in tracks.items():
            scores[track] = sum(
                .5 * obj.confidence + .3 * min(1., (obj.box[2]-obj.box[0]) * (obj.box[3]-obj.box[1]) / (w*h) * 4)
                + .2 * (1 - abs((obj.box[0]+obj.box[2])/2 - w/2) / (w/2))
                for _, obj in entries) / len(samples)
        selected = max(scores, key=scores.get) if scores else None
        entries = tracks.get(selected, [])
        # A sustained subject is required, but no warm-up is imposed on each scene.
        coverage = len(entries) / len(samples)
        if coverage < .55:
            reason = 'no_subject' if not entries else 'unstable_subject'
            keys.extend(Keyframe(s.time, scene, 'fit', (w-cw)/2, (h-ch)/2, reason) for s in samples)
            continue
        times = np.array([t for t, _ in entries])
        centers = np.array([[(o.box[0]+o.box[2])/2, (o.box[1]+o.box[3])/2] for _, o in entries])
        targets, reasons, boxes = [], [], []
        for s in samples:
            obj = next((o for o in s.objects if o.track_id == selected), None)
            flags = []
            target = np.array([np.interp(s.time, times, centers[:, axis]) for axis in (0, 1)])
            if obj is None:
                flags.append('subject_lost')
            elif obj.confidence < .55:
                flags.append('weak_detection')
            box = obj.box if obj else None
            rivals = [o for o in s.objects if o.track_id != selected
                      and scores.get(o.track_id, 0) >= scores[selected] * .75]
            if obj and rivals:
                group = [obj] + rivals
                union = (min(o.box[0] for o in group), min(o.box[1] for o in group),
                         max(o.box[2] for o in group), max(o.box[3] for o in group))
                if union[2]-union[0] <= cw * .9 and union[3]-union[1] <= ch:
                    box = union
                    target = np.array([(union[0]+union[2])/2, (union[1]+union[3])/2])
                else:
                    flags.append('multiple_subjects')
            if box and (box[2]-box[0] > cw * .9 or box[3]-box[1] > ch):
                flags.append('partial_subject')
            targets.append(target)
            reasons.append(flags)
            boxes.append(box)
        positions = []
        center = targets[0].copy()
        for i, sample in enumerate(samples):
            neighbors = [targets[j] for j in range(max(0, i-2), min(len(samples), i+3))
                         if abs(samples[j].time-sample.time) <= .4 + 1e-6]
            target = np.mean(neighbors, axis=0)
            if i == 0:
                center = target
            else:
                delta = target - center
                center = center + np.sign(delta) * np.maximum(0, np.abs(delta) - [cw*.1, ch*.06])
            box = boxes[i]
            if box:
                # Preserve a fitting box; for a large subject keep its centre visible
                # and explicitly flag the partial framing for editorial review.
                for axis, size in enumerate((cw, ch)):
                    lo, hi = box[axis], box[axis+2]
                    if hi-lo <= size * .9:
                        center[axis] = np.clip(center[axis], hi-size*.45, lo+size*.45)
                    else:
                        midpoint = (lo+hi)/2
                        center[axis] = np.clip(center[axis], midpoint-size*.3, midpoint+size*.3)
            center = np.clip(center, [cw/2, ch/2], [w-cw/2, h-ch/2])
            x, y = center - [cw/2, ch/2]
            nearby = samples[max(0, i-2):i+3]
            activity = unexplained_activity(nearby, selected, w, h)
            if excludes_activity(activity, x, y, cw, ch, w*h):
                reasons[i].append('outside_action')
            if positions and abs(x-positions[-1][0]) / max(.001, sample.time-samples[i-1].time) > cw:
                reasons[i].append('fast_pan')
            positions.append((float(x), float(y)))
        keys.extend(Keyframe(s.time, scene, 'crop', *position, '|'.join(flags) or 'editorial_tracking')
                    for s, position, flags in zip(samples, positions, reasons))
    return ReframePlan(w, h, cw, ch, ow, oh, keys)
