$ErrorActionPreference = 'Stop'
$modRoot = Join-Path $PSScriptRoot '..\mod'
$buildRoot = Join-Path $env:TEMP ('neko-drive-control-tests-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildRoot | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$binary = Join-Path $buildRoot 'GameControlTests.exe'
$sources = @('CarPilot.cs', 'TutorialPilot.cs', 'TutorialMicrophone.cs', 'SceneReport.cs', 'PassLevel.cs') | ForEach-Object { Join-Path $modRoot $_ }
& $compiler /nologo /warn:0 "/out:$binary" $sources (Join-Path $PSScriptRoot 'GameControlTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Game control tests failed to compile' }
& $binary
if ($LASTEXITCODE -ne 0) { throw 'Game control contract tests failed' }
