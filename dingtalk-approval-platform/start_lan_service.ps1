param(
    [switch]$SkipPortCheck
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$backendPort = 8011
$frontendPort = 5174
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$requirementsPath = Join-Path $projectRoot 'requirements.txt'
$requirementsStamp = Join-Path $projectRoot '.venv\.requirements.sha256'
$frontendRoot = Join-Path $projectRoot 'frontend'
$packagePath = Join-Path $frontendRoot 'package.json'
$packageStamp = Join-Path $frontendRoot 'node_modules\.package-json.sha256'
$logRoot = Join-Path $projectRoot 'data\logs'
$apiOutputLog = Join-Path $logRoot 'api-output.log'
$apiErrorLog = Join-Path $logRoot 'api-error.log'
$stopScript = Join-Path $projectRoot 'stop_lan_service.ps1'

Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Test-PortInUse([int]$Port) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1)
}

function Get-Sha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '')
        }
        finally {
            $sha256.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Install-BackendDependencies {
    $requirementsHash = Get-Sha256 $requirementsPath
    $installedHash = if (Test-Path -LiteralPath $requirementsStamp) {
        (Get-Content -LiteralPath $requirementsStamp -Raw).Trim()
    } else { '' }
    # Do not import the backend here. On some Windows installations, importing
    # Office-related packages can take long enough to make the launcher appear stuck.
    # The requirements hash is the source of truth for whether dependencies changed.
    $dependenciesReady = (Test-Path -LiteralPath $pythonPath) -and
        $installedHash -eq $requirementsHash

    if (-not $dependenciesReady) {
        Write-Host '[依赖] 正在同步后端依赖...' -ForegroundColor Cyan
        & $pythonPath -m pip install -r $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw '后端依赖安装失败。' }
        Set-Content -LiteralPath $requirementsStamp -Value $requirementsHash -Encoding ascii
    }
}

function Install-FrontendDependencies {
    $packageHash = Get-Sha256 $packagePath
    $installedHash = if (Test-Path -LiteralPath $packageStamp) {
        (Get-Content -LiteralPath $packageStamp -Raw).Trim()
    } else { '' }

    if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules')) -or
        $installedHash -ne $packageHash) {
        Write-Host '[依赖] 正在同步前端依赖...' -ForegroundColor Cyan
        Push-Location -LiteralPath $frontendRoot
        try {
            & npm install
            if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
            Set-Content -LiteralPath $packageStamp -Value $packageHash -Encoding ascii
        }
        finally {
            Pop-Location
        }
    }
}

try {
    if (-not $SkipPortCheck) {
        $occupiedPorts = @($backendPort, $frontendPort) |
            Where-Object { Test-PortInUse $_ }
        if ($occupiedPorts.Count -gt 0) {
            $portText = $occupiedPorts -join '、'
            throw "端口 $portText 已被占用。请运行重启局域网服务.bat以重新加载最新配置。"
        }
    }

    if (-not (Test-Path -LiteralPath $pythonPath)) {
        Write-Host '[首次运行] 正在创建 Python 独立环境...' -ForegroundColor Cyan
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw '未找到 Python，请先安装 Python 3.10 或更高版本。' }
        & $pythonCommand.Source -m venv (Join-Path $projectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Python 独立环境创建失败。' }
    }

    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCommand) { throw '未找到 npm，请先安装 Node.js 20 或更高版本。' }

    Install-BackendDependencies
    Install-FrontendDependencies

    $envFile = Join-Path $projectRoot '.env'
    if (Test-Path -LiteralPath $envFile) {
        Write-Host '[配置] 将从项目根目录重新读取 .env。' -ForegroundColor DarkCyan
    } else {
        Write-Host '[配置] 未找到 .env，钉钉审批接入将保持未配置状态。' -ForegroundColor Yellow
    }

    Remove-Item -LiteralPath $apiOutputLog, $apiErrorLog -Force -ErrorAction SilentlyContinue
    $runApiScript = Join-Path $projectRoot 'run_api.ps1'
    $startParameters = @{
        FilePath = 'powershell.exe'
        ArgumentList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runApiScript)
        WorkingDirectory = $projectRoot
        WindowStyle = 'Hidden'
        PassThru = $true
        RedirectStandardOutput = $apiOutputLog
        RedirectStandardError = $apiErrorLog
    }
    $apiProcess = Start-Process @startParameters

    Write-Host '[启动] 正在等待后端接口就绪...' -ForegroundColor Cyan
    $apiReady = $false
    for ($attempt = 1; $attempt -le 90; $attempt++) {
        if ($apiProcess.HasExited) { break }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$backendPort/api/health" -TimeoutSec 2
            if ($health.status -eq 'ok') {
                $apiReady = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }

    if (-not $apiReady) {
        if (Test-Path -LiteralPath $apiErrorLog) {
            Get-Content -LiteralPath $apiErrorLog -Tail 20 |
                Write-Host -ForegroundColor Red
        }
        throw '后端接口未能正常启动。'
    }

    $localAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -ExpandProperty IPAddress -Unique)

    Write-Host ''
    Write-Host '==============================================================' -ForegroundColor DarkGray
    Write-Host '  企业业务平台 - 局域网服务已启动' -ForegroundColor Green
    Write-Host "  本机访问：http://127.0.0.1:$frontendPort"
    foreach ($address in $localAddresses) {
        Write-Host ("  局域网访问：http://{0}:{1}" -f $address, $frontendPort)
    }
    Write-Host '  此窗口保持打开；按 Ctrl+C 后会关闭前后端服务。'
    Write-Host '==============================================================' -ForegroundColor DarkGray
    Write-Host ''

    Push-Location -LiteralPath $frontendRoot
    try {
        & npm run dev -- --host 0.0.0.0 --port $frontendPort --strictPort
        if ($LASTEXITCODE -ne 0) { throw '前端服务异常退出。' }
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Host ''
    Write-Host ("启动失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
finally {
    if ($apiProcess -and ((Test-PortInUse $backendPort) -or (Test-PortInUse $frontendPort))) {
        & $stopScript -Quiet
    }
}

exit 0
