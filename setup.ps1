param(
    [ValidateNotNullOrEmpty()]
    [string]$EnvironmentPath = '.venv'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
foreach ($reframeTool in @('ffmpeg', 'ffprobe')) {
    $reframeOverride = [Environment]::GetEnvironmentVariable('REFRAME_' + $reframeTool.ToUpperInvariant())
    $reframeExecutable = if ($reframeOverride) { $reframeOverride } else { $reframeTool }
    if (-not (Get-Command $reframeExecutable -ErrorAction SilentlyContinue)) {
        throw "Не найден $reframeTool. Установите FFmpeg, добавьте bin в PATH или задайте REFRAME_$($reframeTool.ToUpperInvariant())."
    }
    & $reframeExecutable -version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Не удалось запустить $reframeTool. Проверьте установленный FFmpeg." }
}
$reframeEnvironment = if ([System.IO.Path]::IsPathRooted($EnvironmentPath)) {
    [System.IO.Path]::GetFullPath($EnvironmentPath)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $EnvironmentPath))
}
$reframePython = Join-Path $reframeEnvironment 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $reframePython -PathType Leaf)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Установите Python 3.12 x64 вместе с Python Launcher (py).'
    }
    py -3.12 -m venv $reframeEnvironment
    if ($LASTEXITCODE -ne 0) { throw 'Для проверенной конфигурации установите Python 3.12 x64.' }
}
& $reframePython -c "import struct,sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Окружение требует Python 3.11–3.13 x64; рекомендуем Python 3.12 x64. Выберите другую папку окружения через -EnvironmentPath.' }
& $reframePython -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
& $reframePython -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить пакет Reframe.' }
& $reframePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Обнаружен конфликт зависимостей. Проверьте сообщения выше.' }
& $reframePython scripts\download_model.py
if ($LASTEXITCODE -ne 0) { throw 'Не удалось скачать или проверить модель.' }
Write-Output "Готово. Запуск: & '$reframePython' -m reframe"
