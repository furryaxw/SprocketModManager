# 重新生成 tests/fixtures/dll_metadata 下的托管 DLL 夹具。
#
# 仅在需要更新夹具时手动运行；测试本身**不**构建任何东西，CI 上没有 dotnet SDK 也能跑，
# 因为夹具 DLL 已经提交进仓库。
#
# 用法：pwsh -File tests/fixtures/dll_metadata/build_fixtures.ps1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = Join-Path $Here 'src'
$Output = Join-Path $Here 'dll'
$Staging = Join-Path ([System.IO.Path]::GetTempPath()) ("dll-metadata-fixtures-" + [guid]::NewGuid().ToString('N'))

$Dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $Dotnet) {
    Write-Host 'dotnet SDK not found: skipping fixture build (committed DLLs in dll/ are used as-is).'
    exit 0
}

try {
    New-Item -ItemType Directory -Force -Path $Output | Out-Null
    foreach ($Name in @('MelonLoaderStub', 'FixtureMod', 'FixturePlugin', 'FixtureLibrary')) {
        $Project = Join-Path $Src (Join-Path $Name "$Name.csproj")
        $ProjectOut = Join-Path $Staging $Name
        & $Dotnet.Source build $Project -c Release -o $ProjectOut --nologo -v quiet
        if ($LASTEXITCODE -ne 0) { throw "build failed for $Name (exit $LASTEXITCODE)" }
        Copy-Item -LiteralPath (Join-Path $ProjectOut "$Name.dll") -Destination (Join-Path $Output "$Name.dll") -Force
        Write-Host "wrote dll/$Name.dll"
    }
}
finally {
    if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
}
