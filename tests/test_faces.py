"""Face and lip analysis on synthetic frames, without the YuNet model (regions are supplied)."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from reframe import analysis
from reframe.contracts import Detection
from reframe.scenes import face_patches, lip_activity

RNG = np.random.default_rng(7)
BACKGROUNDS = [RNG.integers(50, 110, (360, 640, 3)).astype(np.uint8),
               RNG.integers(160, 235, (360, 640, 3)).astype(np.uint8)]
SKIN = cv2.GaussianBlur(RNG.integers(120, 210, (120, 100, 3)).astype(np.uint8), (5, 5), 0)


def draw(faces, background=0, gain=1., offset=0.):
    """faces: (x, y, mouth opening 0..1, eyes open). Returns the frame and YuNet-like regions."""
    image = BACKGROUNDS[background].copy()
    regions = []
    for x, y, mouth, eyes_open in faces:
        image[y:y + 120, x:x + 100] = SKIN
        for ex in (x + 30, x + 70):
            if eyes_open:
                cv2.circle(image, (ex, y + 40), 7, (30, 25, 20), -1)
            else:
                cv2.line(image, (ex - 7, y + 40), (ex + 7, y + 40), (30, 25, 20), 2)
        cv2.ellipse(image, (x + 50, y + 90), (18, 2 + round(mouth * 10)), 0, 0, 360, (40, 20, 35), -1)
        regions.append((Detection((x, y, x + 100, y + 120), .9, -1),
                        (x + 26, y + 78, x + 74, y + 104), (x + 15, y + 28, x + 85, y + 52)))
    image = np.clip(image.astype(np.float32) * gain + offset, 0, 255).astype(np.uint8)
    return image, regions


def activity(before, after):
    (a, ra), (b, rb) = before, after
    return lip_activity(face_patches(a, *ra[0][1:]), face_patches(b, *rb[0][1:]))


def scaled(frame, k):
    image, regions = frame
    s = lambda box: tuple(v * k for v in box)
    return (cv2.resize(image, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR),
            [(Detection(s(face.box), .9, -1), s(mouth), s(eyes)) for face, mouth, eyes in regions])


CLOSED = draw([(200, 100, 0, True)])


@pytest.mark.parametrize("after", [
    draw([(200, 100, 0, True)]),
    draw([(260, 130, 0, True)]),  # head moves
    draw([(240, 90, 0, True)], gain=.7, offset=30),  # head moves, exposure changes
    draw([(200, 100, 0, False)]),  # blink
    scaled(CLOSED, 1.25), scaled(CLOSED, .8),  # head comes closer / moves away
], ids=["still", "move", "move-exposure", "blink", "closer", "further"])
def test_head_movement_without_mouth_movement_is_near_zero(after):
    assert activity(CLOSED, after) < .05


def test_mouth_change_is_measured_and_grows_with_opening():
    values = [activity(CLOSED, draw([(200, 100, opening, True)])) for opening in (.3, .6, 1)]
    assert values[0] > .2 and values == sorted(values)
    # Speech while the head moves or comes closer still counts.
    assert activity(CLOSED, draw([(250, 110, 1, True)])) > .5
    assert activity(CLOSED, scaled(draw([(200, 100, 1, True)]), 1.25)) > .5


def test_unmeasurable_regions_give_zero():
    image, _ = CLOSED
    tiny = face_patches(image, (10, 10, 12, 11), (10, 10, 60, 20))
    outside = face_patches(image, (700, 400, 760, 430), (700, 380, 760, 400))
    assert tiny[0] is None and outside == (None, None)
    normal = face_patches(image, (226, 178, 274, 204), (215, 128, 285, 152))
    assert lip_activity(tiny, normal) == lip_activity(normal, outside) == 0.
    # Without an eye band the full lip change counts.
    opened, regions = draw([(200, 100, 1, True)])
    assert lip_activity((normal[0], None), face_patches(opened, *regions[0][1:])) > .5


class Faces:
    """Stands in for YuNet: returns the regions drawn into each frame, in a given order."""
    def __init__(self, regions, swap=()):
        self.regions_by_frame, self.swap, self.calls = regions, set(swap), 0

    def regions(self, image):
        found = list(self.regions_by_frame[self.calls])
        if self.calls in self.swap:
            found.reverse()
        self.calls += 1
        return found


def run_analysis(monkeypatch, frames, swap=()):
    images = [image for image, _ in frames]
    monkeypatch.setattr(analysis, "frames", lambda info, cancel, max_side=None:
                        iter([(i / 5, i, image) for i, image in enumerate(images)]))
    info = SimpleNamespace(width=640, height=360, duration=len(images) / 5)
    faces = Faces([regions for _, regions in frames], swap)
    result = analysis.analyze(info, None, None, lambda event: None, faces)
    assert faces.calls == len(frames) == len(result.samples)  # every frame is one sample at 5 fps
    return result.samples


def test_analysis_tracks_speakers_between_samples_and_resets_on_cut(monkeypatch):
    frames = []
    # Shot 1: left person moves the head with a closed mouth, right person talks.
    for i in range(8):
        frames.append(draw([(60 + 6 * i, 100, 0, True), (440, 110, (0, 1)[i % 2], True)]))
    # Shot 2: new background; mouths differ from the last frame of shot 1.
    for i in range(4):
        frames.append(draw([(60, 100, (1, 0)[i % 2], True), (440, 110, 0, True)], background=1))
    # Faces come back from the detector in a different order on some frames.
    samples = run_analysis(monkeypatch, frames, swap={3, 5, 10})
    assert [s.scene for s in samples] == [0] * 8 + [1] * 4
    for index, sample in enumerate(samples):
        assert len(sample.faces) == len(sample.speaking) == 2
        by_side = dict(zip(("left", "right") if sample.faces[0].box[0] < 300 else ("right", "left"), sample.speaking))
        if index in (0, 8):
            # No earlier face in this shot: the first sample and the cut sample start fresh.
            assert by_side == {"left": 0., "right": 0.}
        elif index < 8:
            assert by_side["left"] < .05 and by_side["right"] > .5
        else:
            assert by_side["left"] > .5 and by_side["right"] < .05


def test_face_that_jumps_is_not_compared_with_another_place(monkeypatch):
    frames = [draw([(60, 100, 0, True)]), draw([(440, 110, 1, True)]), draw([(440, 110, 0, True)])]
    samples = run_analysis(monkeypatch, frames)
    assert [s.scene for s in samples] == [0, 0, 0]
    assert samples[1].speaking == [0.]  # no overlap with the earlier face
    assert samples[2].speaking[0] > .5
