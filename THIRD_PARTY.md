# Third-party components

Reframe uses third-party packages installed separately through pip. The lock file records tested versions. Preserve their original license files and notices if distributing an environment or a future executable.

| Component | Upstream |
|---|---|
| PySide6 / Qt | https://doc.qt.io/qtforpython-6/licenses.html |
| PyAV | https://github.com/PyAV-Org/PyAV |
| FFmpeg | https://ffmpeg.org/legal.html |
| ONNX Runtime | https://github.com/microsoft/onnxruntime |
| YOLOX-s weights / preprocessing | https://github.com/Megvii-BaseDetection/YOLOX |
| YuNet face detector (2023mar) | https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet (MIT) |
| OpenCV | https://opencv.org/license/ |
| NumPy | https://numpy.org/doc/stable/license.html |
| SciPy | https://scipy.org |

YOLOX is distributed upstream under Apache-2.0. The model manifest identifies the official release and exact model checksum. FFmpeg builds can have different license conditions depending on included encoders. This repository uses the FFmpeg executable installed on the user's computer and PyAV's packaged libraries; it does not bundle an installer or claim clearance for a future commercial distribution.

Diagnostic photos are fetched separately and excluded from the package. Their source links appear in README.md; no claim of ownership over those photos is made.
