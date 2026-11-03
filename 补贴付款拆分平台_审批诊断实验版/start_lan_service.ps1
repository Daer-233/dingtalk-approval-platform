param([switch]$NoBrowser)
& (Join-Path $PSScriptRoot 'launch_experiment.ps1') -Action start -NoBrowser:$NoBrowser
exit $LASTEXITCODE
