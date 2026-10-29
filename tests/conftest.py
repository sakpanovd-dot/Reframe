import pytest


@pytest.fixture(autouse=True)
def quiet_notifications(monkeypatch):
    """Batch-finished tray notifications must not pop up on the developer's desktop."""
    from PySide6.QtWidgets import QSystemTrayIcon
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
