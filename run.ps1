$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$reframePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $reframePython)) {
    throw 'Сначала выполните setup.ps1, чтобы установить окружение Reframe.'
}
& $reframePython -m reframe
exit $LASTEXITCODE
