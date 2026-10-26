param(
    [string]$GamePath = 'D:\SteamLibrary\steamapps\common\teach sister to drive Demo'
)
$ErrorActionPreference = 'Stop'
$modRoot = $PSScriptRoot
$msbuildPath = 'D:\VisualStudio\BuildTools\MSBuild\Current\Bin\MSBuild.exe'
if (-not (Test-Path -LiteralPath $msbuildPath)) {
    $msbuildPath = (Get-Command MSBuild.exe -ErrorAction Stop).Source
}
$managed = Join-Path $GamePath 'teach_sister_to_drive_Data\Managed'
if (-not (Test-Path -LiteralPath (Join-Path $managed 'Assembly-CSharp.dll'))) {
    throw "Game assembly missing: $managed"
}
$bepInExCore = Join-Path $GamePath 'BepInEx\core'
foreach ($assembly in @('BepInEx.dll', '0Harmony.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $bepInExCore $assembly))) {
        throw "BepInEx assembly missing: $bepInExCore\$assembly"
    }
}
& $msbuildPath (Join-Path $modRoot 'NekoSisterDrive.csproj') /t:Build /p:Configuration=Release "/p:GameManaged=$managed" "/p:BepInExCore=$bepInExCore" /v:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw "Build failed ($LASTEXITCODE)" }
Get-FileHash -LiteralPath (Join-Path $modRoot 'bin\NekoSisterDrive.dll') -Algorithm SHA256
