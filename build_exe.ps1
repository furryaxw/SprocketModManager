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

    # 版本元数据取自 modman.APP_VERSION，与 tag 校验用同一个来源。
    $AppVersion = (& $Python -c "from modman import APP_VERSION; print(APP_VERSION)").Trim()
    if (-not $AppVersion) { throw "Could not read APP_VERSION from modman.py" }
    $Numeric = @($AppVersion -split '[^0-9]+' | Where-Object { $_ } | Select-Object -First 4)
    while ($Numeric.Count -lt 4) { $Numeric += '0' }
    $VersionFile = Join-Path $ProjectRoot 'dist\file_version_info.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $VersionFile) | Out-Null
    # Windows 版本资源要求四段整数：取版本里的数字段（`0.6.0-fix2` → 0.6.0.2），完整 tag 放字符串字段。
    [IO.File]::WriteAllText($VersionFile, @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($Numeric -join ', ')),
    prodvers=($($Numeric -join ', ')),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'furryaxw'),
         StringStruct('FileDescription', 'SprocketModManager'),
         StringStruct('FileVersion', '$AppVersion'),
         StringStruct('InternalName', 'SprocketModManager'),
         StringStruct('OriginalFilename', 'SprocketModManager.exe'),
         StringStruct('ProductName', 'SprocketModManager'),
         StringStruct('ProductVersion', '$AppVersion')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@, (New-Object Text.UTF8Encoding($false)))

    & $Python -m PyInstaller `
        --version-file "$VersionFile" `
        --noconfirm `
        --clean `
        --noupx `
        --onefile `
        --windowed `
        --name SprocketModManager `
        --icon "resources\app-icon.ico" `
        --add-data "sprocket_mod_manager\presentation\client_ui;sprocket_mod_manager\presentation\client_ui" `
        --add-data "resources\app-icon.ico;resources" `
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
