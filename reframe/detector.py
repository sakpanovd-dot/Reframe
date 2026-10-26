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

ROOT = Path(__file__).resolve().parent.parent


class Detector(Protocol):
    backend: str
    def detect(self, image: np.ndarray) -> list[Detection]: ...


def model_path() -> Path:
    return Path(os.environ.get("REFRAME_MODEL", str(ROOT / "models" / "yolox_s.onnx")))


class YoloXDetector:
    def __init__(self, cpu_only: bool = False):
        import onnxruntime as ort
        path = model_path()
        if not path.is_file():
            raise ReframeError("Модель не установлена. Выполните: python scripts/download_model.py")
        manifest = json.loads(Path(__file__).with_name("model.json").read_text())
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != manifest["sha256"]:
            raise ReframeError("Контрольная сумма модели не совпадает. Повторно скачайте официальную модель.")
        self.path = path
        self.options = ort.SessionOptions()
        self.options.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
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

    def detect(self, image: np.ndarray) -> list[Detection]:
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
