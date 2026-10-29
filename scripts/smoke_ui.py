"""Load QML, render a screenshot, and exit; no desktop automation needed."""
import os
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication
from reframe.app import Controller

app = QApplication([])
controller = Controller()
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("controller", controller)
warnings = []
engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parent.parent / "reframe/qml/Main.qml")))
if not engine.rootObjects():
    print("QML load errors:", warnings, flush=True)
    raise SystemExit(1)
window = engine.rootObjects()[0]
if os.environ.get("REFRAME_UI_SMALL"):
    window.setWidth(980)
    window.setHeight(720)
    controller.update(error="Не удалось обработать видео. Проверьте свободное место и доступ к папке сохранения.")
if len(sys.argv) > 1:
    controller.loadSource(QUrl.fromLocalFile(str(Path(sys.argv[1]).resolve())).toString())

def capture():
    try:
        if os.environ.get("REFRAME_UI_REVIEW"):
            controller.update(error="", warning="", stage="Готово", result="C:/result.mp4",
                              reviewPath="C:/review.html", reviewCount=3,
                              detail="Видео сохранено. Отмечено интервалов для проверки: 3.")
            # Let Qt lay out the newly visible action buttons before grabbing.
            app.processEvents()
        Path("work").mkdir(exist_ok=True)
        filename = "work/ui-small.png" if os.environ.get("REFRAME_UI_SMALL") else ("work/ui-loaded.png" if len(sys.argv) > 1 else "work/ui.png")
        if os.environ.get("REFRAME_UI_REVIEW"):
            filename = "work/ui-review.png"
        window.grabWindow().save(filename)
        print("QML warnings:", warnings, flush=True)
    finally:
        app.exit(1 if warnings else 0)

QTimer.singleShot(1500, capture)
raise SystemExit(app.exec())
