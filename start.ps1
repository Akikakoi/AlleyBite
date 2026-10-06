# AlleyBite 一键启动（Docker 全栈，对应开发文档 10.2）
#
# 用法：在仓库根目录执行
#   .\start.ps1
#
# 流程：检查 Docker → 准备 .env → 准备自签证书 → 构建并启动全部服务 → 等待就绪 → 打印访问入口
# 首次启动需构建镜像（pip/npm 下载依赖），可能耗时十几分钟；之后有层缓存会快很多。
# 若提示脚本被禁止运行，先执行：Set-ExecutionPolicy -Scope Process Bypass

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Note([string]$msg) { Write-Host "    $msg" }
function Write-Warn2([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

# 双击运行时（父进程为 explorer.exe）窗口会在脚本结束后立刻关闭，
# 导致报错一闪而过，因此这种情况结束时暂停等待回车。
$script:PauseAtEnd = $false
try {
    $self = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($self.ParentProcessId)" -ErrorAction Stop
    if ($parent.Name -eq 'explorer.exe') { $script:PauseAtEnd = $true }
} catch { }

$exitCode = 0
try {

    # --- 1. 前置条件 -----------------------------------------------------
    Write-Step "检查 Docker 环境"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "未找到 docker 命令，请先安装并启动 Docker Desktop。"
    }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 不可用，请确认 Docker Desktop 已启用 Compose v2。"
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 守护进程未运行，请先启动 Docker Desktop。"
    }

    # --- 2. 环境变量 -----------------------------------------------------
    Write-Step "准备环境变量 .env"
    if (Test-Path "$root\.env") {
        Write-Note ".env 已存在，保持不变。"
    } else {
        Copy-Item "$root\.env.example" "$root\.env"
        # 生成随机密钥并写回：容器内固定 DEBUG=false，弱默认值会让 API 拒绝启动。
        $rand1 = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
        $rand2 = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
        $envLines = Get-Content -Path "$root\.env" -Encoding UTF8 | ForEach-Object {
            if ($_ -match '^FEEDBACK_IP_SALT=') { "FEEDBACK_IP_SALT=$rand1" }
            elseif ($_ -match '^ADMIN_TOKEN_SECRET=') { "ADMIN_TOKEN_SECRET=$rand2" }
            else { $_ }
        }
        [System.IO.File]::WriteAllLines("$root\.env", $envLines)
        Write-Warn2 "已从 .env.example 生成 .env（并写入随机 FEEDBACK_IP_SALT / ADMIN_TOKEN_SECRET）。"
        Write-Warn2 "未填 LLM_API_KEY 时抽取走 mock（可离线跑通）；未填 AMAP_API_KEY 时跳过地图源。"
        Write-Warn2 "需接入真实模型/地图时，请编辑 .env 后重新执行本脚本。"
    }

    # --- 3. HTTPS 自签证书 -----------------------------------------------
    Write-Step "准备 HTTPS 自签证书"
    $certDir = Join-Path $root "deploy\certs"
    $crt = Join-Path $certDir "server.crt"
    $key = Join-Path $certDir "server.key"
    if ((Test-Path $crt) -and (Test-Path $key)) {
        Write-Note "证书已存在，保持不变。"
    } else {
        New-Item -ItemType Directory -Force -Path $certDir | Out-Null
        $certDirPath = (Resolve-Path $certDir).Path
        $subj = "/C=CN/ST=Local/L=Local/O=AlleyBite/CN=localhost"
        $san = "subjectAltName=DNS:localhost,DNS:alleybite.local,IP:127.0.0.1"
        if (Get-Command openssl -ErrorAction SilentlyContinue) {
            openssl req -x509 -nodes -newkey rsa:2048 -days 825 `
                -keyout "$certDirPath\server.key" -out "$certDirPath\server.crt" `
                -subj $subj -addext $san
        } else {
            Write-Note "本机无 openssl，改用 Docker 生成。"
            docker run --rm -v "${certDirPath}:/certs" alpine/openssl req -x509 -nodes -newkey rsa:2048 -days 825 `
                -keyout /certs/server.key -out /certs/server.crt `
                -subj $subj -addext $san
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $crt)) {
            throw "自签证书生成失败，请手动执行 deploy/certs/gen-self-signed.sh。"
        }
        Write-Note "已生成 server.crt / server.key（生产环境请替换为正式证书）。"
    }

    # --- 4. 构建并启动 ---------------------------------------------------
    Write-Step "构建并启动全部服务（首次较慢，请耐心等待）"
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up 失败，请查看上方输出。"
    }

    # --- 5. 等待就绪 -----------------------------------------------------
    Write-Step "等待服务就绪（最长 180 秒）"
    # 用 curl.exe（Windows 10+ 自带）探测，不用 Invoke-WebRequest：
    # PowerShell 5.1 的 .NET Framework 与 nginx 的 TLS 握手不兼容，即使显式启用
    # TLS1.2 也会失败并误报；-k 用于跳过自签证书校验。
    # 用 127.0.0.1 而非 localhost，避免 Windows 下 localhost 解析到 ::1 导致连接失败。
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        Write-Warn2 "未找到 curl.exe，跳过就绪探测，请直接查看下方容器状态。"
    } else {
        $deadline = (Get-Date).AddSeconds(180)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            $code = & curl.exe -k -s -o NUL -w "%{http_code}" --max-time 5 https://127.0.0.1/health
            if ("$code".Trim() -eq '200') { $ready = $true; break }
            Start-Sleep -Seconds 5
        }
        if ($ready) {
            Write-Note "健康检查通过。"
        } else {
            Write-Warn2 "180 秒内未等到 /health 返回 200，请执行 docker compose logs -f api 排查。"
            $exitCode = 1
        }
    }

    # --- 6. 状态汇总 -----------------------------------------------------
    Write-Step "容器状态"
    docker compose ps

} catch {
    Write-Host ""
    Write-Host "[错误] $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "启动完成" -ForegroundColor Green
} else {
    Write-Host "启动未完成，请按上方提示排查。" -ForegroundColor Red
}

Write-Host ""
Write-Host "访问入口："
Write-Host "  前端首页      https://127.0.0.1/                （自签证书，浏览器会提示不受信任，属预期）"
Write-Host "  健康检查      https://127.0.0.1/health"
Write-Host "  接口文档      https://127.0.0.1/docs"
Write-Host "  Grafana      https://127.0.0.1/grafana/        （仅回环与内网可访问）"
Write-Host "  告警管理台    https://127.0.0.1/alertmanager/"
Write-Host "  管理后台      https://127.0.0.1:8443/           （仅内网/白名单可访问）"
Write-Host "                首次使用需在 .env 设置 ADMIN_PASSWORD，或运行："
Write-Host "                .\backend\.venv\Scripts\python.exe backend\scripts\create_admin.py --username admin --password <口令>"
Write-Host ""
Write-Host "常用命令："
Write-Host "  查看日志      docker compose logs -f api"
Write-Host "  停止服务      docker compose down"
Write-Host "  重启服务      docker compose restart api"

if ($script:PauseAtEnd) {
    Write-Host ""
    Read-Host "按回车键关闭窗口"
}
exit $exitCode
