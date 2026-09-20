param([string]$Python = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    # Directory mode avoids extracting Python and libraries to TEMP at every logon.
    & $Python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
        --name campus-auto-login --exclude-module pytest run.py
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
    $package = Join-Path $PSScriptRoot 'dist\campus-auto-login'
    foreach ($name in @('.env.example', 'configure.cmd', 'install-startup.cmd', 'remove-startup.cmd', 'manage-startup.ps1', 'WINDOWS-README.md')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $package -Force
    }
    Write-Host "Built $package. Distribute the whole folder, including _internal."
} finally {
    Pop-Location
}
