param(
    [string]$UpstreamBase = 'https://www.lanlan.tech/text/v1',
    [int]$Port = 49321
)

$ErrorActionPreference = 'Stop'
$TaskPluginRoot = Split-Path -Parent $PSScriptRoot
$TaskPythonPath = Join-Path $TaskPluginRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $TaskPythonPath -PathType Leaf)) {
    throw 'Plugin development Python is missing.'
}
$TaskWorkspaceRoot = Split-Path -Parent $TaskPluginRoot
$TaskTraceName = 'reply-trace-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6) + '.jsonl'
$TaskTracePath = Join-Path $TaskWorkspaceRoot ('dist\test-builds\20261017-reply-diagnostics\' + $TaskTraceName)
& $TaskPythonPath (Join-Path $PSScriptRoot 'trace_reply_http.py') --upstream-base $UpstreamBase --port $Port --out $TaskTracePath
exit $LASTEXITCODE
