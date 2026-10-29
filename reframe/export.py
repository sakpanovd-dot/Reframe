from __future__ import annotations

import logging
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from .contracts import ProgressEvent, ReframeError
from .media import binary, command, frames, raw_probe, check_cancel
from .render import Renderer


@dataclass(frozen=True)
class Exporter:
    id: str
    extension: str
    muxer: str
    video_codec: str = "h264"
    audio_codec: str = "aac"


EXPORTERS = {e.id: e for e in (Exporter("mp4", ".mp4", "mp4"), Exporter("mov", ".mov", "mov"))}


def encoder_options(codec):
    return ({"preset": "p4", "cq": "21", "rc": "vbr", "b": "0", "bf": "0"}
            if codec == "h264_nvenc" else {"preset": "veryfast", "crf": "20", "bf": "0"})


def choose_encoder(work: Path, cpu_only: bool) -> str:
    if cpu_only:
        return "libx264"
    test = work / "encoder-test.mp4"
    try:
        with av.open(str(test), "w") as out:
            stream = out.add_stream("h264_nvenc", rate=30)
            stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
            stream.options = encoder_options("h264_nvenc")
            frame = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), np.uint8), format="bgr24")
            for packet in stream.encode(frame):
                out.mux(packet)
            for packet in stream.encode():
                out.mux(packet)
        return "h264_nvenc"
    except Exception:
        logging.info("NVENC unavailable; using libx264", exc_info=True)
        return "libx264"
    finally:
        test.unlink(missing_ok=True)


def encode_video(info, plan, path, codec, cancel, emit):
    renderer = Renderer(plan)
    time_base = Fraction(1, 90000)
    count, last_pts = 0, -1
    durations = {}
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream(codec, rate=Fraction(info.fps).limit_denominator(100000))
        stream.width, stream.height = plan.output_width, plan.output_height
        stream.pix_fmt = "yuv420p"
        stream.codec_context.thread_count = 2
        stream.time_base = time_base
        stream.codec_context.time_base = time_base
        stream.options = encoder_options(codec)

        def mux(packet):
            if packet.pts in durations:
                packet.duration = durations.pop(packet.pts)
            output.mux(packet)

        previous = None
        for t, source_duration, image in frames(info, cancel):
            # One-frame lookahead gives exact VFR frame durations, including discontinuities.
            if previous is not None:
                old_t, _, old_image = previous
                duration = t - old_t
                frame = av.VideoFrame.from_ndarray(renderer.frame(old_image, old_t), format="bgr24")
                frame.pts = max(last_pts + 1, round(old_t / time_base))
                frame.time_base = time_base
                durations[frame.pts] = max(1, round(duration / time_base))
                last_pts = frame.pts
                for packet in stream.encode(frame):
                    mux(packet)
                count += 1
            previous = t, source_duration, image
            if count % 10 == 0:
                emit(ProgressEvent("Создание видео", min(1, t / info.duration), codec))
        if previous is None:
            raise ReframeError("В исходнике не удалось прочитать ни одного кадра.")
        t, duration, image = previous
        # The stream duration is authoritative for the final sample.
        duration = max(1 / info.fps, info.duration - t) if info.duration > t else duration
        frame = av.VideoFrame.from_ndarray(renderer.frame(image, t), format="bgr24")
        frame.pts = max(last_pts + 1, round(t / time_base))
        frame.time_base = time_base
        durations[frame.pts] = max(1, round(duration / time_base))
        for packet in stream.encode(frame):
            mux(packet)
        for packet in stream.encode():
            mux(packet)


def mux_audio(info, video_path, output_path, exporter, cancel):
    args = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path)]
    if info.audio_index is not None:
        # copyts preserves the audio/video start offset; normalize against the video origin.
        args += ["-copyts", "-itsoffset", str(-info.start_time), "-i", str(info.path),
                 "-map", "0:v:0", "-map", f"1:{info.audio_index}",
                 "-c:v", "copy", "-c:a", exporter.audio_codec, "-b:a", "192k",
                 "-af", "aresample=async=1:first_pts=0,apad"]
    else:
        args += ["-map", "0:v:0", "-c:v", "copy"]
    args += ["-t", str(info.duration), "-map_metadata", "-1", "-metadata:s:v:0", "rotate=0",
             "-movflags", "+faststart", "-f", exporter.muxer, str(output_path)]
    command(args, cancel)


def verify(info, plan, path, cancel):
    data = raw_probe(path, cancel)
    videos = [s for s in data["streams"] if s["codec_type"] == "video"]
    if not videos:
        raise ReframeError("Проверка результата: видеодорожка отсутствует.")
    video = videos[0]
    if (video["width"], video["height"]) != (plan.output_width, plan.output_height):
        raise ReframeError("Проверка результата: неверные размеры кадра.")
    if abs(float(video.get("duration", 0)) - info.duration) > 1 / info.fps + .005:
        raise ReframeError("Проверка результата: длительность отличается от исходника.")
    audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
    if info.audio_index is not None:
        if not audio or abs(float(audio[0].get("start_time", 0)) - float(video.get("start_time", 0))) > .1:
            raise ReframeError("Проверка результата: звуковая дорожка отсутствует или смещена.")
        if abs(float(audio[0].get("duration", 0)) - info.duration) > .1:
            raise ReframeError("Проверка результата: длительность звука отличается от видео.")
    check_cancel(cancel)
    # Valid container metadata does not prove that all encoded packets are readable.
    # Decode every selected stream before the pipeline publishes the temporary file.
    try:
        command([binary("ffmpeg"), "-hide_banner", "-v", "error", "-xerror",
                 "-err_detect", "explode", "-threads", "2", "-i", str(path),
                 "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"], cancel)
    except ReframeError as error:
        raise ReframeError("Проверка результата: не удалось полностью прочитать готовое видео или звук. "
                           "Результат не сохранён; попробуйте повторить обработку.") from error
    check_cancel(cancel)
