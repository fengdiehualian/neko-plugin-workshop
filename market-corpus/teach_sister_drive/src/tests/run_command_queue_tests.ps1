$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) {
    throw '.NET Framework C# compiler was not found.'
}

$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\mod\CommandQueue.cs')).Path
$tests = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'CommandQueueTests.cs')).Path
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$buildDirectory = Join-Path $temporaryRoot ('neko-command-queue-tests-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $buildDirectory
$exitCode = 1
try {
    $executable = Join-Path $buildDirectory 'CommandQueueTests.exe'
    & $compiler /nologo /target:exe "/out:$executable" $source $tests
    if ($LASTEXITCODE -ne 0) { throw 'Command queue regression harness compilation failed.' }
    & $executable
    $exitCode = $LASTEXITCODE
}
finally {
    $resolvedBuildDirectory = [IO.Path]::GetFullPath($buildDirectory)
    $temporaryPrefix = $temporaryRoot.TrimEnd('\') + '\'
    if (-not $resolvedBuildDirectory.StartsWith($temporaryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolvedBuildDirectory) -notlike 'neko-command-queue-tests-*') {
        throw 'Refusing cleanup outside the command queue test temporary directory.'
    }
    Remove-Item -LiteralPath $resolvedBuildDirectory -Recurse -Force
}
exit $exitCode
