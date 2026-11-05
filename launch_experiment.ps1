param([ValidateSet('start','stop','restart')][string]$Action='start', [switch]$NoBrowser)
$ErrorActionPreference='Stop'
$arguments=@((Join-Path $PSScriptRoot 'scripts/service_control.py'),$Action)
if ($NoBrowser) { $arguments += '--no-browser' }
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -X utf8 @arguments
exit $LASTEXITCODE
