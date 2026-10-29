import json
from queue import Queue

import pytest

from reframe.contracts import AnalysisResult, Detection, ProgressEvent, Sample
from reframe.editorial import applies, make_editorial_plan
from reframe.presets import load_presets
from reframe.review import intervals, html_report


def plan(samples):
    return make_editorial_plan(AnalysisResult(640, 360, samples[-1].time+.2, samples), load_presets()['9:16'])


def test_primary_format_only():
    presets = load_presets()
    assert applies(1920, 1080, presets['9:16'])
    assert not applies(1920, 1080, presets['4:5'])
    assert not applies(1080, 1920, presets['9:16'])


def test_scene_lookahead_crops_immediately_and_keeps_moving_subject():
    samples = [Sample(i*.2, 0, [] if i == 0 else [Detection((50+i*8, 60, 100+i*8, 310), .9, 0, 1)]) for i in range(20)]
    result = plan(samples)
    assert all(k.mode == 'crop' for k in result.keyframes)
    assert result.keyframes[0].x < 150  # Starts at the subject, not image centre.
    for sample, key in zip(samples[1:], result.keyframes[1:]):
        box = sample.objects[0].box
        assert key.x <= box[0] and key.x+result.crop_width >= box[2]
    assert result.keyframes[0].reason == 'subject_lost'


def test_large_person_crops_with_explicit_review_flag():
    result = plan([Sample(i*.2, 0, [Detection((180, 0, 450, 360), .9, 0, 1)]) for i in range(10)])
    assert all(k.mode == 'crop' and 'partial_subject' in k.reason for k in result.keyframes)


@pytest.mark.parametrize('near', [True, False])
def test_two_subjects_group_if_they_fit_otherwise_flag(near):
    other = (310, 80, 355, 290) if near else (500, 80, 545, 290)
    samples = [Sample(i*.2, 0, [Detection((250, 80, 295, 290), .9, 0, 1), Detection(other, .9, 0, 2)]) for i in range(10)]
    result = plan(samples)
    assert all(k.mode == 'crop' for k in result.keyframes)
    if near:
        assert all(k.x <= 250 and k.x+result.crop_width >= 355 for k in result.keyframes)
    else:
        assert all('multiple_subjects' in k.reason for k in result.keyframes)


def test_uncertain_live_scene_is_cropped_at_best_guess_and_flagged():
    # A blurred background is unusable for the editor: crop where motion happens.
    result = plan([Sample(i*.2, 0, [], [(480, 100, 600, 300)]) for i in range(10)])
    assert all(k.mode == 'crop' for k in result.keyframes)
    assert all(k.x <= 480 and k.x + result.crop_width >= 600 for k in result.keyframes)
    review = intervals(result, 2)
    assert review == [dict(start=0., end=2., scene=0, mode='crop', reasons=['no_subject'])]


def test_title_card_is_fitted_as_graphic_but_live_flat_moment_is_not():
    samples = [Sample(i*.2, 0, [], flat=.95) for i in range(5)]
    samples += [Sample(1+i*.2, 0, [Detection((250, 80, 295, 290), .9, 0, 1)], flat=.3) for i in range(10)]
    samples[8] = Sample(samples[8].time, 0, [], flat=.9)  # single flat sample inside live footage
    result = plan(samples)
    assert [k.mode for k in result.keyframes[:5]] == ['graphic'] * 5
    assert all(k.mode == 'crop' for k in result.keyframes[5:])
    assert {r for e in intervals(result, 3) for r in e['reasons']} >= {'graphic'}


def test_split_screen_shows_one_panel_and_switches_after_hold():
    layout = [(.02, .32), (.35, .65), (.68, .98)]
    # Subject in the middle panel; a cut inside a panel every 1.2 s.
    samples = [Sample(i*.2, i // 6, [Detection((300, 80, 340, 300), .9, 0, i // 6)], panels=layout)
               for i in range(30)]
    result = plan(samples)
    assert all(k.mode == 'panel' and k.reason == 'split_screen' for k in result.keyframes)
    centers = [round((k.x + k.w/2) / 640, 2) for k in result.keyframes]
    assert centers[0] == .5  # starts on the panel with the subject
    assert all(k.w <= .3 * 640 for k in result.keyframes)
    changes = [k.time for k, prev, c, p in zip(result.keyframes[1:], result.keyframes, centers[1:], centers) if c != p]
    assert changes and all(b - a >= 2 - 1e-6 for a, b in zip([0] + changes, changes))
    assert len(set(centers)) >= 2


def test_external_motion_does_not_disable_crop_but_is_reported():
    result = plan([Sample(i*.2, 0, [Detection((250, 80, 295, 290), .9, 0, 1)], [(500, 0, 640, 360)]) for i in range(10)])
    assert all(k.mode == 'crop' and 'outside_action' in k.reason for k in result.keyframes)


def test_scene_cuts_have_independent_positions_and_review_intervals():
    samples = [Sample(i*.2, i//5, [Detection((20, 60, 70, 300) if i < 5 else (550, 60, 600, 300), .5, 0, 1)]) for i in range(10)]
    result = plan(samples)
    assert result.keyframes[4].x < 50
    assert result.keyframes[5].x > 400
    review = intervals(result, 2)
    assert len(review) == 2
    assert review[0]['end'] == review[1]['start'] == 1


def test_report_escapes_filename_and_explains_limits(tmp_path):
    text = html_report(tmp_path / '<script>.mp4', [])
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert 'Интервалов: 0' in text
    assert 'пока не реализовано' in text


def test_controller_receives_review_then_success(tmp_path):
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from reframe.app import Controller
    from reframe.contracts import VideoInfo
    app = QApplication.instance() or QApplication([])
    controller = Controller()
    try:
        info = VideoInfo(Path('C:/source.mp4'), 1920, 1080, 2., 25, 0, None)
        controller.items = [dict(id=0, path=info.path, name='source.mp4', info=info, status='processing', detail='',
                                 result='', reviewPath='', reviewCount=0, reviewError='', batch=True)]
        controller.current = 0
        controller.update(selected=0, busy=True)
        controller.events = Queue()
        controller.events.put(ProgressEvent('Review', 1, json.dumps(dict(path='C:/review.html',count=3,error='')),kind='review'))
        controller.events.put(ProgressEvent('Done', 1, 'C:/result.mp4', kind='done'))
        controller._drain_events()
        controller._finish_batch()
        assert controller.state['reviewCount'] == 3
        assert controller.state['reviewPath'] == 'C:/review.html'
        assert controller.state['stage'] == 'Готово'
        assert '3' in controller.state['detail']
    finally:
        controller.timer.stop()


def test_face_of_wide_subject_is_never_cut():
    # Leaning person: wide body box, face at its right edge (crop width is 198 of 640).
    body, face = (150, 40, 460, 360), Detection((400, 50, 440, 100), .9, -1)
    result = plan([Sample(i*.2, 0, [Detection(body, .9, 0, 1)], faces=[face]) for i in range(10)])
    for key in result.keyframes:
        assert key.x <= 400 and key.x + result.crop_width >= 440


def test_person_showing_face_is_preferred_over_back_view():
    back, front = (60, 60, 140, 340), (480, 60, 560, 340)
    samples = [Sample(i*.2, 0, [Detection(back, .9, 0, 1), Detection(front, .85, 0, 2)],
                      faces=[Detection((500, 70, 540, 120), .9, -1)]) for i in range(10)]
    result = plan(samples)
    assert all(k.x <= 480 and k.x + result.crop_width >= 560 for k in result.keyframes)


def test_scene_without_objects_is_framed_on_faces():
    face = Detection((520, 80, 560, 130), .9, -1)
    result = plan([Sample(i*.2, 0, [], [(0, 0, 200, 360)], faces=[face]) for i in range(10)])
    assert all(k.mode == 'crop' and k.x <= 520 and k.x + result.crop_width >= 560 for k in result.keyframes)
