param(
    [ValidateNotNullOrEmpty()]
    [string]$EnvironmentPath = '.venv'
)

# Обновление Reframe: новая версия из GitHub, пакеты и модели. Ничего не удаляет и не отменяет локальные правки.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Stop-Update([string]$Message, [string]$Advice) {
    Write-Host ''
    Write-Host "Обновление не выполнено: $Message" -ForegroundColor Red
    if ($Advice) { Write-Host $Advice -ForegroundColor Yellow }
    Write-Host 'Видео, настройки и результаты не изменялись.' -ForegroundColor Yellow
    exit 1
}

# Тихая проверка Git: в Windows PowerShell 5.1 перенаправленный stderr при 'Stop' стал бы исключением.
function Test-Git {
    $ErrorActionPreference = 'Continue'
    & git @args 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Stop-Update 'не найден Git.' 'Установите Git (например, winget install Git.Git), откройте PowerShell заново и повторите.'
    }
    $reframeEnvironment = if ([System.IO.Path]::IsPathRooted($EnvironmentPath)) {
        [System.IO.Path]::GetFullPath($EnvironmentPath)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $EnvironmentPath))
    }
    $reframePython = Join-Path $reframeEnvironment 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $reframePython -PathType Leaf)) {
        Stop-Update "не найдено окружение $reframeEnvironment." 'Сначала выполните установку: powershell -ExecutionPolicy Bypass -File .\setup.ps1'
    }
    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and [System.IO.Path]::GetFullPath($_.Path).StartsWith($reframeEnvironment + '\', [System.StringComparison]::OrdinalIgnoreCase) } catch { $false }
    })
    if ($running.Count -gt 0) {
        Stop-Update 'Reframe сейчас запущен.' 'Дождитесь окончания обработки, закройте окно Reframe и повторите обновление.'
    }

    Write-Host '1/5. Проверка локальных изменений...'
    if (-not (Test-Git rev-parse --is-inside-work-tree)) {
        Stop-Update 'папка программы не является копией репозитория Git.' 'Скачайте программу заново через git clone или попросите помощи.'
    }
    # Неотслеживаемые файлы (видео, work, модели) не мешают: git сам откажется перезаписать их.
    $changes = @(& git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) { Stop-Update 'не удалось проверить состояние файлов программы.' 'Проверьте сообщения Git выше.' }
    if ($changes.Count -gt 0) {
        $changes | ForEach-Object { Write-Host "    $_" }
        Stop-Update 'файлы программы изменены вручную (список выше).' 'Скрипт не отменяет чужие правки. Сохраните их копию или попросите помощи, затем повторите обновление.'
    }
    if (-not (Test-Git rev-parse --abbrev-ref --symbolic-full-name '@{u}')) {
        Stop-Update 'у текущей ветки нет связанной ветки на GitHub.' 'Попросите помощи: нужно выбрать ветку для обновлений (git branch --set-upstream-to).'
    }
    $before = (& git rev-parse --short HEAD).Trim()

    Write-Host '2/5. Загрузка новой версии (git pull --ff-only)...'
    & git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        Stop-Update 'не удалось получить новую версию.' 'Проверьте интернет и доступ к GitHub. Если Git сообщает о расхождении веток, ничего не исправляйте сами — попросите помощи.'
    }
    $after = (& git rev-parse --short HEAD).Trim()
    if ($before -eq $after) {
        Write-Host "    Новых изменений нет (версия $after). Пакеты и модели всё равно будут проверены."
    } else {
        Write-Host "    Версия обновлена: $before -> $after."
    }

    Write-Host '3/5. Установка пакетов Python...'
    & $reframePython -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { Stop-Update 'не удалось установить пакеты Python.' 'Проверьте интернет и повторите обновление. Программа уже обновлена, повторный запуск безопасен.' }
    & $reframePython -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { Stop-Update 'не удалось установить пакет Reframe.' 'Повторите обновление. Если ошибка повторится, попросите помощи.' }
    & $reframePython -m pip check
    if ($LASTEXITCODE -ne 0) { Stop-Update 'обнаружен конфликт пакетов Python.' 'Попросите помощи и покажите сообщения выше.' }

    Write-Host '4/5. Проверка и загрузка моделей...'
    & $reframePython scripts\download_model.py
    if ($LASTEXITCODE -ne 0) { Stop-Update 'не удалось скачать или проверить модели.' 'Проверьте интернет и повторите обновление: без моделей формат 9:16 не запустится.' }

    Write-Host '5/5. Готово.'
    Write-Host ''
    Write-Host "Готово. Reframe обновлён (версия $after). Запуск: run.ps1" -ForegroundColor Green
    exit 0
} catch {
    Stop-Update $_.Exception.Message 'Повторите обновление. Если ошибка повторится, попросите помощи и покажите это сообщение.'
}
