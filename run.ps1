param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$LabArgs
)
$ErrorActionPreference = 'Stop'
$sdkRoot = $PSScriptRoot
if ([System.IO.Path]::GetPathRoot($sdkRoot) -eq 'C:\') { throw 'Move this project to a non-C drive.' }
$env:PIP_CACHE_DIR = Join-Path $sdkRoot '.cache\pip'
$env:TEMP = Join-Path $sdkRoot '.cache\tmp'
$env:TMP = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $sdkRoot '.cache\pycache'
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Path $env:PIP_CACHE_DIR,$env:TEMP,$env:PYTHONPYCACHEPREFIX -Force | Out-Null
$sdkPython = Join-Path $sdkRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $sdkPython)) { throw 'Run setup.ps1 first.' }
Push-Location $sdkRoot
try {
    if ($LabArgs.Count -gt 0 -and $LabArgs[0] -eq 'test') {
        & $sdkPython -B -m pytest -q -o cache_dir=.cache/pytest --basetemp=.cache/pytest-tmp tests
    } else {
        & $sdkPython -B sdk_lab.py @LabArgs
    }
    $labExit = $LASTEXITCODE
} finally { Pop-Location }
exit $labExit
