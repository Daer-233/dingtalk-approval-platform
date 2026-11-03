param([switch]$NoBrowser)
& (Join-Path $PSScriptRoot 'launch_experiment.ps1') -Action stop -NoBrowser:$NoBrowser
exit $LASTEXITCODE
