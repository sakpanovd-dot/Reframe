from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .contracts import Detection, ReframeError
from .media import worker_threads

ROOT = Path(__file__).resolve().parent.parent


class Detector(Protocol):
    backend: str
    def detect(self, image: np.ndarray) -> list[Detection]: ...


def model_path() -> Path:
    return Path(os.environ.get("REFRAME_MODEL", str(ROOT / "models" / "yolox_s.onnx")))


def face_model_path() -> Path:
    return Path(os.environ.get("REFRAME_FACE_MODEL", str(ROOT / "models" / "face_detection_yunet_2023mar.onnx")))


def verify_model(path: Path, manifest_name: str) -> None:
    if not path.is_file():
        raise ReframeError("Модель не установлена. Выполните: python scripts/download_model.py")
    manifest = json.loads(Path(__file__).with_name(manifest_name).read_text())
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != manifest["sha256"]:
        raise ReframeError("Контрольная сумма модели не совпадает. Повторно скачайте официальную модель.")


class FaceDetector:
    """YuNet faces in the analysis image; boxes use the image's pixel coordinates.

    Faces anchor the crop on people: YOLOX gives a whole-body box, so a close-up
    or a person holding a phone can otherwise be centred on the torso or the hand.
    """
    SCORE = .75
    MIN_HEIGHT = .025  # of the frame height; smaller hits are mostly background texture

    def __init__(self):
        path = face_model_path()
        verify_model(path, "face_model.json")
        self.model = cv2.FaceDetectorYN.create(str(path), "", (320, 320), self.SCORE, .3, 50)

    def detect(self, image: np.ndarray) -> list[Detection]:
        return [face for face, _, _ in self.regions(image)]

    def regions(self, image: np.ndarray):
        """Faces with a mouth box and an eye-band box derived from YuNet landmarks.

        The two boxes let analysis tell lip movement (speech) apart from head
        movement, which changes both regions alike.
        """
        h, w = image.shape[:2]
        self.model.setInputSize((w, h))
        _, faces = self.model.detect(image)
        result = []
        for face in [] if faces is None else faces:
            score = float(face[14])
            x, y, fw, fh = float(face[0]), float(face[1]), float(face[2]), float(face[3])
            if score < self.SCORE or fh < h * self.MIN_HEIGHT:
                continue
            (rex, rey), (lex, ley), (nx, ny), (rmx, rmy), (lmx, lmy) = face[4:14].reshape(5, 2).astype(float)
            mouth_y = (rmy + lmy) / 2
            lip = max(2., mouth_y - ny)
            mouth = (min(rmx, lmx) - fw * .08, (ny + mouth_y) / 2, max(rmx, lmx) + fw * .08, mouth_y + lip * .8)
            eyes = (min(rex, lex) - fw * .1, min(rey, ley) - fh * .1, max(rex, lex) + fw * .1, max(rey, ley) + fh * .1)
            box = (max(0., x), max(0., y), min(float(w), x + fw), min(float(h), y + fh))
            result.append((Detection(box, score, -1), mouth, eyes))
        return result


class YoloXDetector:
    def __init__(self, cpu_only: bool = False):
        import onnxruntime as ort
        path = model_path()
        verify_model(path, "model.json")
        self.path = path
        self.options = ort.SessionOptions()
        self.options.intra_op_num_threads = min(8, worker_threads())
        self.options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        providers = ["CPUExecutionProvider"]
        if not cpu_only and "CUDAExecutionProvider" in ort.get_available_providers():
            if hasattr(ort, "preload_dlls"):
                try:
                    ort.preload_dlls()
                except Exception:
                    logging.exception("Optional CUDA DLL preload failed")
            providers.insert(0, "CUDAExecutionProvider")
        try:
            self.session = ort.InferenceSession(str(path), self.options, providers=providers)
        except Exception:
            logging.exception("CUDA session unavailable, using CPU")
            self.session = ort.InferenceSession(str(path), self.options, providers=["CPUExecutionProvider"])
        self.backend = "CUDA" if "CUDAExecutionProvider" in self.session.get_providers() else "CPU"
        self.input = self.session.get_inputs()[0].name
        self.size = int(self.session.get_inputs()[0].shape[-1])
        grids, strides = [], []
        for stride in (8, 16, 32):
            y, x = np.mgrid[:self.size // stride, :self.size // stride]
            grids.append(np.stack([x, y], axis=-1).reshape(-1, 2))
            strides.append(np.full((x.size, 1), stride))
        self.grid, self.strides = np.concatenate(grids), np.concatenate(strides)
        self._previous_image: np.ndarray | None = None
        self._previous_objects: tuple[Detection, ...] = ()

    def detect(self, image: np.ndarray) -> list[Detection]:
        # Only exact repeats are safe: approximate matches can hide a small
        # moving subject. Own the pixels because decoders may reuse buffers.
        if (self._previous_image is not None
                and image.dtype == self._previous_image.dtype
                and np.array_equal(image, self._previous_image)):
            return list(self._previous_objects)
        objects = self._detect(image)
        self._previous_image = image.copy()
        self._previous_objects = tuple(objects)
        return objects

    def _detect(self, image: np.ndarray) -> list[Detection]:
        h, w = image.shape[:2]
        scale = min(self.size / h, self.size / w)
        # Official YOLOX ONNX uses BGR, 0..255, top-left padding, no normalization.
        padded = np.full((self.size, self.size, 3), 114, np.uint8)
        resized = cv2.resize(image, (int(w * scale), int(h * scale)))
        padded[:resized.shape[0], :resized.shape[1]] = resized
        tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
        try:
            prediction = self.session.run(None, {self.input: tensor})[0][0]
        except Exception:
            if self.backend == "CPU":
                raise
            import onnxruntime as ort
            logging.exception("CUDA inference failed; restarting on CPU")
            self.session = ort.InferenceSession(str(self.path), self.options, providers=["CPUExecutionProvider"])
            self.backend = "CPU"
            prediction = self.session.run(None, {self.input: tensor})[0][0]
        xy = (prediction[:, :2] + self.grid) * self.strides
        wh = np.exp(np.clip(prediction[:, 2:4], -15, 15)) * self.strides
        boxes = np.concatenate((xy - wh / 2, xy + wh / 2), axis=1) / scale
        scores = prediction[:, 4:5] * prediction[:, 5:]
        categories = scores.argmax(axis=1)
        confidence = scores.max(axis=1)
        valid = np.where(confidence >= .35)[0]
        objects = []
        for category in np.unique(categories[valid]):
            ids = valid[categories[valid] == category]
            rects = boxes[ids].copy()
            rects[:, 2:] -= rects[:, :2]
            kept = cv2.dnn.NMSBoxes(rects.tolist(), confidence[ids].tolist(), .35, .45)
            for index in np.asarray(kept).flatten():
                i = ids[index]
                x1, y1, x2, y2 = boxes[i]
                box = (float(np.clip(x1, 0, w)), float(np.clip(y1, 0, h)),
                       float(np.clip(x2, 0, w)), float(np.clip(y2, 0, h)))
                if box[2] > box[0] and box[3] > box[1]:
                    objects.append(Detection(box, float(confidence[i]), int(category)))
        # A single vehicle may be classified as both car and truck. Do not let
        # duplicate, almost identical boxes create false scene ambiguity.
        from .analysis import iou
        unique = []
        for obj in sorted(objects, key=lambda o: -o.confidence):
            if not any(iou(obj.box, other.box) > .75 for other in unique):
                unique.append(obj)
        return unique
