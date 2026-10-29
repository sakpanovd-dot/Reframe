# Работа над Reframe из облака

Репозиторий: https://github.com/sakpanovd-dot/Reframe — приватный.

## Claude Code в облаке (с телефона или браузера)

1. Открыть Claude → раздел **Code** (claude.ai/code или приложение на телефоне), подключить GitHub и выбрать репозиторий `sakpanovd-dot/Reframe`.
2. Если есть выбор ветки — выбрать `claude/dev`. Ветка `main` устарела.
3. Начать сессию с фразы:
   > Переключись на ветку claude/dev. Прочитай CLAUDE.md и NEXT_TASKS.md. Подготовь окружение командой bash scripts/cloud_setup.sh. Затем возьми задачу 1 из NEXT_TASKS.md, сделай её в отдельной ветке и открой pull request в claude/dev. Отвечай по-русски.
4. Результат — pull request в `claude/dev`. Перед слиянием его проверяют на ноутбуке (особенно всё, что касается окна программы и Windows).

Видео в репозитории нет, поэтому в облаке проверяются только тесты и синтетические клипы. Для установки нужен доступ в интернет к PyPI, GitHub (модели) и репозиториям пакетов Linux; если сеть ограничена, скрипт остановится на соответствующем шаге.

## Создать окружение Codex Cloud

1. В Codex на вебе или в приложении выбрать **Work in → Cloud → Select environment → Create environment**.
2. Выбрать репозиторий `sakpanovd-dot/Reframe`. Если он не виден, предоставить интеграции GitHub доступ к этому репозиторию.
3. Попросить подготовить окружение по требованиям ниже. Проверить отчёт установки и тестов, затем нажать **Publish**.
4. Создать задачу в опубликованном окружении. Работать в отдельной ветке и возвращать изменения через pull request.

Официальная инструкция: https://learn.chatgpt.com/docs/environments/cloud-environments

## Требования к окружению

- Python 3.12 и pip, Git.
- FFmpeg/ffprobe с H.264/H.265, libx264/libx265. Для теста поворота требуется опция `-display_rotation` (современная сборка FFmpeg; предпочтительно 8+).
- Для Linux нужны системные библиотеки Qt/OpenCV: EGL/OpenGL, GLib, xkbcommon и необходимые зависимости Qt. Конкретные пакеты зависят от образа; обычно это `libegl1`, `libopengl0`, `libglib2.0-0`, `libxkbcommon0`.
- Сеть на этапе установки для PyPI и скачивания официальной ONNX-модели из GitHub Releases. Модель и .venv не находятся в Git.

Команды после установки системных зависимостей:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
python scripts/download_model.py
export QT_QPA_PLATFORM=offscreen
export QT_QUICK_BACKEND=software
python -m pytest -q
python scripts/smoke_ui.py
```

Эти команды описывают настройку; облачное окружение ещё не создано и Linux-проверки здесь не выполнялись. Не подменять проверку отсутствующих системных компонентов пропуском тестов без объяснения.

## Что доступно без ноутбука

Редактирование кода, рефакторинг, исправления, тесты алгоритма и экспорта, создание PR. Проверка Linux offscreen помогает найти ошибки QML, но не заменяет визуальную приёмку Windows-приложения. CUDA/NVENC проверяются только при наличии подходящей NVIDIA.

Локальные видео, настройки и история этого чата автоматически в облако не переносятся. Контекст проекта находится в README.md, TESTING.md, AGENTS.md, CLAUDE.md и NEXT_TASKS.md. Если нужны личные исходники для проверки, их нужно отдельно предоставить выбранной облачной задаче.

## Вернуться на ноутбук

После проверки и слияния PR, при отсутствии несохранённых изменений:

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m reframe
```

Если на ноутбуке есть локальные правки, сначала сохранить их отдельным коммитом/веткой; не применять reset --hard для синхронизации.
