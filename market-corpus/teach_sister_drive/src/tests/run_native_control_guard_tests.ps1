param(
    [string]$GameDirectory = 'D:\SteamLibrary\steamapps\common\teach sister to drive Demo',
    [string]$CompilerPath = ''
)
$ErrorActionPreference = 'Stop'
if ($CompilerPath) {
    $compiler = (Resolve-Path -LiteralPath $CompilerPath).Path
} elseif (Test-Path -LiteralPath 'D:\VisualStudio\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe') {
    $compiler = 'D:\VisualStudio\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe'
} else {
    $compiler = Join-Path (Split-Path (Get-Command MSBuild.exe -ErrorAction Stop).Source) 'Roslyn\csc.exe'
}
$managed = Join-Path $GameDirectory 'teach_sister_to_drive_Data\Managed'
$harmonyDirectory = Join-Path $GameDirectory 'BepInEx\core'
$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\mod\NativeControlGuard.cs')).Path
$tests = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'NativeControlGuardTests.cs')).Path
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$buildDirectory = Join-Path $temporaryRoot ('neko-native-control-tests-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $buildDirectory
$exitCode = 1
try {
    $executable = Join-Path $buildDirectory 'NativeControlGuardTests.exe'
    $references = @(
        (Join-Path $managed 'Assembly-CSharp.dll'),
        (Join-Path $managed 'UnityEngine.CoreModule.dll'),
        (Join-Path $managed 'netstandard.dll'),
        (Join-Path $harmonyDirectory '0Harmony.dll')
    ) | ForEach-Object { '/r:' + $_ }
    & $compiler /nologo /target:exe "/out:$executable" @references $source $tests
    if ($LASTEXITCODE -ne 0) { throw 'Native control guard regression harness compilation failed.' }
    & $executable $managed $harmonyDirectory
    $exitCode = $LASTEXITCODE
}
finally {
    $resolvedBuildDirectory = [IO.Path]::GetFullPath($buildDirectory)
    $temporaryPrefix = $temporaryRoot.TrimEnd('\') + '\'
    if (-not $resolvedBuildDirectory.StartsWith($temporaryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolvedBuildDirectory) -notlike 'neko-native-control-tests-*') {
        throw 'Refusing cleanup outside the native control test temporary directory.'
    }
    Remove-Item -LiteralPath $resolvedBuildDirectory -Recurse -Force
}
exit $exitCode
