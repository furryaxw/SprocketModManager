[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { 'python' }

Push-Location $ProjectRoot
try {
    if (-not $SkipInstall) {
        & $Python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed with exit code $LASTEXITCODE" }
    }

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name SprocketModManager `
        --collect-all customtkinter `
        --collect-submodules dnfile `
        modman.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    $Output = Join-Path $ProjectRoot 'dist\SprocketModManager.exe'
    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Output
    Write-Host "Built $Output"
    Write-Host "SHA256 $($Hash.Hash)"
}
finally {
    Pop-Location
}
