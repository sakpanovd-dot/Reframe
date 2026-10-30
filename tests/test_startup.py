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


def _powershell_brackets_balanced(text):
    # Rough syntax guard for a script that cannot run in Linux CI: brackets outside strings/comments.
    pairs, stack, quote, i = {')': '(', '}': '{', ']': '['}, [], None, 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == '`' and quote == '"':
                i += 1
            elif char == quote:
                quote = None
        elif char in '\'"':
            quote = char
        elif char == '#':
            i = text.find('\n', i)
            if i < 0:
                break
        elif char in '({[':
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
        i += 1
    return not stack and quote is None


def test_update_script_is_safe_and_ordered():
    from pathlib import Path
    raw = (Path(__file__).resolve().parent.parent / 'update.ps1').read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf'), 'Windows PowerShell 5.1 needs a BOM to read Russian text'
    text = raw.decode('utf-8-sig')
    assert _powershell_brackets_balanced(text)
    steps = ['git status --porcelain', 'git pull --ff-only', 'pip install -r requirements-lock.txt',
             'pip install --no-deps -e .', 'scripts\\download_model.py', 'Готово']
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions)
    lowered = text.lower()
    for forbidden in ('reset --hard', 'remove-item', 'git clean', 'git checkout', 'git stash', '--force', 'del '):
        assert forbidden not in lowered
