$ErrorActionPreference='Stop'
$buildDirectory=Join-Path $env:TEMP ('neko-possession-tests-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildDirectory | Out-Null
$compiler='D:\VisualStudio\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe'
$modRoot=Join-Path $PSScriptRoot '..\mod'
$gameManaged='D:\SteamLibrary\steamapps\common\teach sister to drive Demo\teach_sister_to_drive_Data\Managed'
$sources=@('CarPilot.cs','TutorialPilot.cs','TutorialMicrophone.cs','SceneReport.cs','PassLevel.cs','ActionSequence.cs') | ForEach-Object {Join-Path $modRoot $_}
$executable=Join-Path $buildDirectory 'PossessionControlTests.exe'
& $compiler /nologo /warn:0 /main:PossessionControlTests "/out:$executable" "/r:$gameManaged\Newtonsoft.Json.dll" "/r:$gameManaged\netstandard.dll" @sources (Join-Path $PSScriptRoot 'GameControlTests.cs') (Join-Path $PSScriptRoot 'PossessionControlTests.cs')
if($LASTEXITCODE -ne 0){throw 'Possession sequence tests failed to compile'}
Copy-Item -LiteralPath (Join-Path $gameManaged 'Newtonsoft.Json.dll') -Destination $buildDirectory
& $executable
if($LASTEXITCODE -ne 0){throw 'Possession sequence tests failed'}
