import importlib
import sys
import traceback


def main():
    try:
        application = importlib.import_module('.app', package='reframe')
    except (ImportError, OSError):
        # Dependency/DLL failures happen before the GUI and its logger exist.
        traceback.print_exc()
        print(
            '\nНе удалось загрузить компоненты Reframe. '
            'Окно приложения не запущено.\n'
            'В Windows выполните setup.ps1 из папки проекта и повторите запуск run.ps1.\n'
            'Для проверенной конфигурации используйте Python 3.12 x64. '
            'Техническая причина указана выше.',
            file=sys.stderr,
        )
        return 1
    return application.main()

if __name__ == "__main__":
    raise SystemExit(main())
