import pytest

from reframe.progress import RemainingTime, describe


def simulate(stages, step=.1):
    """Feed (stage, seconds) events at 10 Hz; return (time, true remaining, shown estimate) rows."""
    total = sum(seconds for _, seconds in stages)
    estimate, rows, now = RemainingTime(0.), [], 0.
    for stage, seconds in stages:
        start = now
        while now < start + seconds - 1e-9:
            estimate.update(stage, (now - start) / seconds if seconds else 1, now)
            rows.append((now, total - now, estimate.seconds(now)))
            now += step
    return rows


JOB = [("Проверка видео", .5), ("Анализ видео", 40), ("Планирование кадра", .5),
       ("Создание видео", 20), ("Сохранение звука", .3), ("Проверка результата", .7)]


def test_nothing_until_estimate_is_stable_then_close_and_continuous():
    rows = simulate(JOB)
    shown = [(t, truth, value) for t, truth, value in rows if value is not None]
    first = shown[0][0]
    assert first >= 3.5  # not before the analysis speed is measured
    assert first / (first + shown[0][2]) >= .1  # not before ~10% of the way
    # Once visible, the estimate stays visible until the video is done.
    assert all(value is not None for t, _, value in rows if t >= first)
    for t, truth, value in shown:
        assert abs(value - truth) <= max(5, .2 * truth), (t, truth, value)


@pytest.mark.parametrize("render", [8, 40])
def test_render_speed_corrects_the_analysis_guess(render):
    job = [("Анализ видео", 40), ("Планирование кадра", .5), ("Создание видео", render),
           ("Проверка результата", .5)]
    rows = simulate(job)
    rendering = [(truth, value) for t, truth, value in rows if t > 40.5 + 6 and truth > 1]
    for truth, value in rendering:
        assert abs(value - truth) <= max(3, .2 * truth)


def test_same_aspect_job_without_analysis():
    rows = simulate([("Проверка видео", .3), ("Подготовка кадра", .1), ("Создание видео", 30),
                     ("Сохранение звука", .2), ("Проверка результата", 1)])
    shown = [(truth, value) for t, truth, value in rows if value is not None]
    assert shown and all(abs(value - truth) <= max(3, .2 * truth) for truth, value in shown)


def test_unknown_or_cancelled_stages_show_nothing():
    estimate = RemainingTime(0.)
    estimate.update("Проверка видео", 0, 0.)
    assert estimate.seconds(10.) is None
    estimate.update("Повтор на CPU", 0, 11.)
    assert estimate.seconds(12.) is None


@pytest.mark.parametrize("seconds, text", [
    (None, ""), (10, "осталось меньше минуты"), (50, "осталось ≈ 1 мин"), (150, "осталось ≈ 2 мин"),
    (3599, "осталось ≈ 1 ч 00 мин"), (3600 + 7 * 60, "осталось ≈ 1 ч 07 мин"),
])
def test_describe(seconds, text):
    assert describe(seconds) == text
