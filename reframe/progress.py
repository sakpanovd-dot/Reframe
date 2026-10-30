"""Remaining time for the current video, estimated from stage progress events; no GUI code."""
from __future__ import annotations

ANALYSIS, PLANNING, RENDER = "Анализ видео", "Планирование кадра", "Создание видео"
AFTER_RENDER = {"Сохранение звука", "Проверка результата"}
# Fallback render/analysis time ratio when the output size is unknown.
RENDER_PER_ANALYSIS = .5
# Audio muxing and full verification after rendering, as a share of render time.
TAIL = .05
MIN_SHARE = .1  # show nothing before ~10% of the estimated total
MIN_STAGE = 3.  # seconds of a stage before its own speed is trusted
SMOOTHING = .05  # per update (10 per second): the shown value settles in ~2 s


def render_ratio(output_width: int, output_height: int) -> float:
    """Render time per analysis time; rendering cost follows the output pixel count.

    Laptop CPU, 30 s clips: 1080p -> 594x1056 0.34; 1080p -> 1080x1920 0.88;
    4K -> 1080x1920 0.98 (analysis always works on 640 px frames).
    """
    return min(1.5, max(.2, .45 * output_width * output_height / 1e6))


class RemainingTime:
    def __init__(self, started: float, render_per_analysis: float = RENDER_PER_ANALYSIS):
        self.render_per_analysis = render_per_analysis
        self.started = started
        self.stage, self.since, self.progress = None, started, 0.
        self.render_expected = None  # seconds, known once analysis speed is
        self.tail_expected = None  # seconds after rendering, known once render speed is
        self.tail_since = None
        self.smoothed, self.at = None, started

    def update(self, stage: str, progress: float, now: float) -> None:
        if stage != self.stage:
            self.stage, self.since = stage, now
        self.progress = progress

    def _raw(self, now):
        spent = now - self.since
        measured = spent >= MIN_STAGE and self.progress >= .02
        stage_total = spent / self.progress if measured else None
        if self.stage == ANALYSIS:
            if not measured:
                return None
            self.render_expected = stage_total * self.render_per_analysis
            return stage_total - spent + self.render_expected * (1 + TAIL)
        if self.stage == RENDER:
            if measured:
                self.tail_expected = stage_total * TAIL
                return (stage_total - spent) + self.tail_expected
            if self.render_expected is not None:
                return max(0., self.render_expected - spent) + self.render_expected * TAIL
            return None
        if self.stage == PLANNING and self.render_expected is not None:
            return self.render_expected * (1 + TAIL)
        if self.stage in AFTER_RENDER and self.tail_expected is not None:
            # Both tail stages share one budget, counted from the end of rendering.
            if self.tail_since is None:
                self.tail_since = self.since
            return max(0., self.tail_expected - (now - self.tail_since))
        return None

    def seconds(self, now: float) -> float | None:
        """Smoothed estimate, or None while it is not yet trustworthy."""
        raw = self._raw(now)
        if raw is None:
            self.smoothed = None
            return None
        if self.smoothed is None:
            self.smoothed = raw
        else:
            predicted = max(0., self.smoothed - (now - self.at))
            self.smoothed = predicted + SMOOTHING * (raw - predicted)
        self.at = now
        elapsed = now - self.started
        if elapsed / max(.001, elapsed + self.smoothed) < MIN_SHARE:
            return None
        return self.smoothed


def describe(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 45:
        return "осталось меньше минуты"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"осталось ≈ {minutes} мин"
    return f"осталось ≈ {minutes // 60} ч {minutes % 60:02d} мин"
