from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ReframeError(Exception):
    """An actionable, user-facing error."""


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    duration: float
    fps: float
    video_index: int
    audio_index: int | None
    rotation: int = 0
    sar: float = 1.0
    start_time: float = 0.0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    confidence: float
    category: int
    track_id: int = -1


@dataclass
class Sample:
    time: float
    scene: int
    objects: list[Detection]


@dataclass
class AnalysisResult:
    width: int
    height: int
    duration: float
    samples: list[Sample]
    backend: str = "CPU"


@dataclass(frozen=True)
class Keyframe:
    time: float
    scene: int
    mode: str  # crop / fit
    x: float
    y: float


@dataclass
class ReframePlan:
    width: int
    height: int
    crop_width: int
    crop_height: int
    output_width: int
    output_height: int
    keyframes: list[Keyframe]


@dataclass(frozen=True)
class ExportRequest:
    source: Path
    destination: Path
    preset: str = "9:16"
    container: str = "mp4"
    overwrite: bool = False
    cpu_only: bool = False
    fit_only: bool = False  # diagnostic CLI; not exposed in the product UI
    diagnostics: Path | None = None


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    progress: float
    detail: str = ""
    elapsed: float = 0.0
    kind: str = "progress"


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    width: int
    height: int
    description: str = ""

    @property
    def ratio(self) -> float:
        return self.width / self.height
