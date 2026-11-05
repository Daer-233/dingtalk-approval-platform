param([switch]$NoBrowser)
& (Join-Path $PSScriptRoot 'launch_experiment.ps1') -Action restart -NoBrowser:$NoBrowser
exit $LASTEXITCODE
