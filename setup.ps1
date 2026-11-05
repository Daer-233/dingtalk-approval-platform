param([string]$Python = 'python')
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
if (-not (Test-Path -LiteralPath $sdkPython)) {
    & $Python -B -m venv (Join-Path $sdkRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
}
$lockedRequirements = Join-Path $sdkRoot 'requirements.lock.txt'
if (-not (Test-Path -LiteralPath $lockedRequirements)) { $lockedRequirements = Join-Path $sdkRoot 'requirements.txt' }
& $sdkPython -B -m pip install -r $lockedRequirements
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
