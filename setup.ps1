$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw 'Установите FFmpeg и добавьте его папку bin в PATH. Затем откройте PowerShell заново.'
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Для проверенной конфигурации установите Python 3.12 x64.' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
& .\.venv\Scripts\python.exe -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить пакет Reframe.' }
& .\.venv\Scripts\python.exe scripts\download_model.py
if ($LASTEXITCODE -ne 0) { throw 'Не удалось скачать или проверить модель.' }
Write-Output 'Готово. Запуск: .\run.ps1'
