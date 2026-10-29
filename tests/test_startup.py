from types import SimpleNamespace

import pytest

from reframe import __main__ as startup


@pytest.mark.parametrize('error', [ModuleNotFoundError('missing PySide6'), ImportError('DLL load failed'), OSError('broken library')])
def test_dependency_failure_has_recovery_instructions(monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(startup.importlib, 'import_module', fail)
    assert startup.main() == 1
    text = capsys.readouterr().err
    assert str(error) in text
    assert 'Не удалось загрузить компоненты Reframe' in text
    assert 'setup.ps1' in text
    assert 'run.ps1' in text


def test_launcher_preserves_application_exit_code(monkeypatch):
    monkeypatch.setattr(startup.importlib, 'import_module', lambda *a, **kw: SimpleNamespace(main=lambda: 7))
    assert startup.main() == 7
