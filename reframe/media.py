from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import closing
from pathlib import Path

from .contracts import Cancelled, ReframeError, VideoInfo

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def binary(name: str) -> str:
    override = os.environ.get(f"REFRAME_{name.upper()}")
    found = override or shutil.which(name)
    if not found:
        raise ReframeError(f"Не найден {name}. Установите FFmpeg и добавьте его папку bin в PATH.")
    return found


def check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def command(args: list[str], cancel=None) -> str:
    """Drain both pipes with communicate, and always reap children on cancellation."""
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=CREATE_FLAGS)
    try:
        while True:
            check_cancel(cancel)
            try:
                out, err = process.communicate(timeout=0.15)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            import logging
            logging.error("Command failed: %s\n%s", args[0], err.decode("utf-8", "replace")[-6000:])
            raise ReframeError("Не удалось прочитать или обработать видео. Проверьте файл и свободное место.")
        return out.decode("utf-8", "replace")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def raw_probe(path: Path, cancel=None) -> dict:
    return json.loads(command([binary("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                               "-of", "json", str(path)], cancel))


def probe(path: Path, cancel=None) -> VideoInfo:
    path = path.resolve()
    if not path.is_file():
        raise ReframeError("Видео не найдено. Выберите существующий файл.")
    data = raw_probe(path, cancel)
    videos = [s for s in data["streams"] if s["codec_type"] == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ReframeError("В файле нет видеодорожки.")
    v = videos[0]
    if v.get("color_transfer") in {"smpte2084", "arib-std-b67"} or any(
        any(term in s.get("side_data_type", "").lower() for term in ("dovi", "mastering display", "hdr"))
        for s in v.get("side_data_list", [])
    ):
        raise ReframeError("HDR пока не поддерживается. Сначала преобразуйте видео в SDR.")
    rotation = int(float(v.get("tags", {}).get("rotate", 0)))
    for side in v.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(round(float(side["rotation"])))
    rotation %= 360
    if rotation not in (0, 90, 180, 270):
        raise ReframeError("Поворот видео на произвольный угол пока не поддерживается.")
    sar_text = v.get("sample_aspect_ratio", "1:1")
    try:
        a, b = map(int, sar_text.split(":"))
        sar = a / b if a and b else 1.0
    except (ValueError, ZeroDivisionError):
        sar = 1.0
    width, height = round(v["width"] * sar), v["height"]
    if rotation in (90, 270):
        width, height = height, width
    from fractions import Fraction
    try:
        fps = float(Fraction(v.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = 0
    if not fps:
        fps = 30.0
    duration = float(v.get("duration") or data.get("format", {}).get("duration") or 0)
    if duration <= 0 or min(width, height) < 16:
        raise ReframeError("Не удалось определить длительность или размеры видео.")
    audios = [s for s in data["streams"] if s["codec_type"] == "audio"]
    audios.sort(key=lambda s: -s.get("disposition", {}).get("default", 0))
    warnings = []
    if duration > 300 or min(width, height) > 1080 or max(width, height) > 1920:
        warnings.append("Видео выходит за целевой диапазон 5 минут / 1080p. Обработка может занять больше времени.")
    info = VideoInfo(path, width, height, duration, fps, v["index"],
                     audios[0]["index"] if audios else None, rotation, sar,
                     float(v.get("start_time", 0)), tuple(warnings))
    # ffprobe and PyAV may use different FFmpeg builds. Verify with the decoder
    # used by the processing pipeline instead of maintaining a codec whitelist.
    check_cancel(cancel)
    try:
        with closing(frames(info, cancel, max_side=640)) as decoder:
            next(decoder)
    except Cancelled:
        raise
    except StopIteration as error:
        raise ReframeError("В видеодорожке не удалось прочитать ни одного кадра.") from error
    except Exception as error:
        import logging
        codec = v.get("codec_name") or "неизвестный"
        logging.exception("Cannot decode first frame; codec=%s", codec)
        raise ReframeError(f"Не удалось декодировать видео ({codec}). Файл может быть повреждён "
                           "или для этого кодека нет подходящего декодера в установленной сборке.") from error
    return info


def frames(info: VideoInfo, cancel=None, max_side: int | None = None):
    import av
    import cv2
    with av.open(str(info.path)) as container:
        stream = next(s for s in container.streams.video if s.index == info.video_index)
        stream.codec_context.thread_count = 2
        stream.thread_type = "AUTO"
        previous = -1.0
        origin = None
        for frame in container.decode(stream):
            check_cancel(cancel)
            timestamp = float(frame.pts * frame.time_base) if frame.pts is not None else (
                previous + 1 / info.fps + (origin or 0))
            if origin is None:
                origin = timestamp
            t = timestamp - origin
            if t <= previous:
                continue
            previous = t
            scale = min(1., max_side / max(info.width, info.height)) if max_side else 1.
            if scale < 1:
                rgb = frame.reformat(width=max(2, round(frame.width * scale)),
                                     height=max(2, round(frame.height * scale)), format="bgr24").to_ndarray()
            else:
                rgb = frame.to_ndarray(format="bgr24")
            if info.sar != 1:
                rgb = cv2.resize(rgb, (round(rgb.shape[1] * info.sar), rgb.shape[0]))
            if info.rotation == 90:
                rgb = cv2.rotate(rgb, cv2.ROTATE_90_COUNTERCLOCKWISE)
            elif info.rotation == 270:
                rgb = cv2.rotate(rgb, cv2.ROTATE_90_CLOCKWISE)
            elif info.rotation == 180:
                rgb = cv2.rotate(rgb, cv2.ROTATE_180)
            duration = float(frame.duration * frame.time_base) if frame.duration else 1 / info.fps
            yield t, duration, rgb
