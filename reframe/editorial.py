"""Offline scene composition for the primary 16:9 -> 9:16 editorial workflow.

Heuristics over detector tracks, not semantic understanding of dialogue or text.
Live footage is always cropped: a blurred background is unusable for the editor,
so uncertain framing is a best guess flagged in the review report instead.
"""
from collections import defaultdict
from itertools import groupby

import numpy as np
from scipy.ndimage import gaussian_filter1d

from .contracts import Keyframe, ReframePlan
from .planner import dimensions, excludes_activity, unexplained_activity

GRAPHIC_FLATNESS = .75  # title cards and packshots; dark live shots measured up to .57
PANEL_HOLD = 2.         # seconds on one split-screen panel before switching
PANEL_FORCE = 3.        # switch without waiting for a cut inside the panel


def applies(width, height, preset):
    return abs(width / height - 16 / 9) < .005 and preset.width * 16 == preset.height * 9


def is_graphic(sample):
    return sample.flat >= GRAPHIC_FLATNESS and not any(o.confidence >= .5 for o in sample.objects)


def fallback_center(samples, w, h):
    """Best guess for a scene without a sustained subject: detections, then motion."""
    boxes = [o.box for s in samples for o in s.objects] or [b for s in samples for b in s.motion_boxes]
    if not boxes:
        return np.array([w / 2, h / 2])
    weights = np.array([max(1., (b[2]-b[0]) * (b[3]-b[1])) for b in boxes])
    centers = np.array([[(b[0]+b[2])/2, (b[1]+b[3])/2] for b in boxes])
    return np.average(centers, axis=0, weights=weights)


def scene_keys(scene, samples, w, h, cw, ch):
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
    if len(entries) / len(samples) < .55:
        reason = 'no_subject' if not entries else 'unstable_subject'
        center = np.clip(fallback_center(samples, w, h), [cw/2, ch/2], [w-cw/2, h-ch/2])
        x, y = center - [cw/2, ch/2]
        return [Keyframe(s.time, scene, 'crop', float(x), float(y), reason) for s in samples]
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
    centers = []
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
        centers.append(center.copy())
    # The dead zone moves in steps; a gentle low-pass turns them into a camera move.
    centers = np.array(centers)
    if len(centers) > 2:
        centers = gaussian_filter1d(centers, 1.5, axis=0, mode='nearest')
    centers = np.clip(centers, [cw/2, ch/2], [w-cw/2, h-ch/2])
    keys = []
    for i, sample in enumerate(samples):
        x, y = centers[i] - [cw/2, ch/2]
        nearby = samples[max(0, i-2):i+3]
        if excludes_activity(unexplained_activity(nearby, selected, w, h), x, y, cw, ch, w*h):
            reasons[i].append('outside_action')
        if keys and abs(x-keys[-1].x) / max(.001, sample.time-samples[i-1].time) > cw:
            reasons[i].append('fast_pan')
        keys.append(Keyframe(sample.time, scene, 'crop', float(x), float(y), '|'.join(reasons[i]) or 'editorial_tracking'))
    return keys


def same_layout(a, b):
    return len(a) == len(b) and all(abs(p[0]-q[0]) < .02 and abs(p[1]-q[1]) < .02 for p, q in zip(a, b))


def panel_score(sample, panel, w):
    x1, x2 = panel[0] * w, panel[1] * w
    return sum(o.confidence * (o.box[2]-o.box[0]) * (o.box[3]-o.box[1]) for o in sample.objects
               if x1 <= (o.box[0]+o.box[2]) / 2 <= x2)


def panel_keys(samples, w, h, cw, ch):
    """Show one split-screen panel full frame; change panel at most every PANEL_HOLD s.

    Panels usually hold separate phone videos with their own cuts. Prefer to
    switch at such a cut once the hold time has passed, otherwise force it.
    """
    layout = samples[0].panels
    current, since = max(range(len(layout)), key=lambda i: panel_score(samples[0], layout[i], w)), samples[0].time
    keys = []
    for index, s in enumerate(samples):
        held = s.time - since
        cut = index and s.scene != samples[index-1].scene
        if held >= PANEL_FORCE - 1e-6 or (cut and held >= PANEL_HOLD - 1e-6):
            others = [i for i in range(len(layout)) if i != current]
            following = [i for i in others if i > current] + [i for i in others if i < current]
            current = max(following, key=lambda i: panel_score(s, layout[i], w) > 0)
            since = s.time
        a, b = layout[current]
        inset = (b - a) * w * .01
        width = min(float(cw), (b - a) * w - 2 * inset)
        height = width * ch / cw
        x = (a + b) / 2 * w - width / 2
        keys.append(Keyframe(s.time, s.scene, 'panel', float(x), float((h - height) / 2), 'split_screen', width))
    return keys


def make_editorial_plan(analysis, preset):
    w, h = analysis.width, analysis.height
    cw, ch, ow, oh = dimensions(w, h, preset)
    keys = []
    for scene, iterator in groupby(analysis.samples, lambda s: s.scene):
        samples = list(iterator)
        graphic = [is_graphic(s) for s in samples]
        # A single flat sample inside live footage must not flicker the layout.
        position = 0
        for value, run in groupby(graphic):
            length = len(list(run))
            if value and length < 3 and length < len(samples):
                graphic[position:position+length] = [False] * length
            position += length
        live = [s for s, g in zip(samples, graphic) if not g] or samples
        tracked = {k.time: k for k in scene_keys(scene, live, w, h, cw, ch)}
        for s, g in zip(samples, graphic):
            if g:
                keys.append(Keyframe(s.time, scene, 'graphic', 0., 0., 'graphic'))
            else:
                keys.append(tracked[s.time])
    # Split screens cross the per-panel cuts, so they are planned across scenes.
    result, index = [], 0
    samples = analysis.samples
    while index < len(samples):
        end = index
        if samples[index].panels:
            while end + 1 < len(samples) and same_layout(samples[end+1].panels, samples[index].panels):
                end += 1
        if samples[index].panels and samples[end].time - samples[index].time >= .6 - 1e-6:
            result.extend(panel_keys(samples[index:end+1], w, h, cw, ch))
        else:
            result.extend(keys[index:end+1])
        index = end + 1
    return ReframePlan(w, h, cw, ch, ow, oh, result)
