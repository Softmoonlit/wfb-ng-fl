# scripts/sync_to_server.ps1
# Windows PowerShell: 一键向 Server 开发板传输项目代码与 UFTP 离线源码包

[CmdletBinding()]
param(
    [Alias("s")]
    [string]$Server,

    [Alias("u")]
    [string]$User,

    [Alias("c")]
    [string]$ConfigFile = ".\cluster_nodes.conf",

    [Alias("r")]
    [string]$RemoteDir = "projects/wfb-ng-fl",

    [Alias("z")]
    [string]$UftpZip,

    [Alias("n")]
    [switch]$DryRun,

    [Alias("h")]
    [switch]$Help
)

$ErrorActionPreference = "Stop"

function Show-Usage {
    Write-Host "WFB-FL Windows 电脑端向 Server 开发板一键增量同步工具" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "用法:" -ForegroundColor Yellow
    Write-Host "  powershell -File .\scripts\sync_to_server.ps1 [选项]"
    Write-Host ""
    Write-Host "选项:" -ForegroundColor Yellow
    Write-Host "  -Server, -s <IP/别名>     指定 Server 开发板局域网 IP 或 SSH 别名 (如 192.168.1.100 或 vm0)"
    Write-Host "  -User, -u <用户名>        指定 SSH 登录用户名 (默认优先读取配置，缺省为 ubuntu)"
    Write-Host "  -ConfigFile, -c <路径>    指定集群全局配置文件路径 (默认: .\cluster_nodes.conf)"
    Write-Host "  -RemoteDir, -r <路径>     指定 Server 端存放项目的目标路径 (默认: projects/wfb-ng-fl)"
    Write-Host "  -UftpZip, -z <路径>       指定本地 uftp_src-5.0.3.zip 离线源码包路径 (默认自动寻源)"
    Write-Host "  -DryRun, -n               演练模式 (仅打印即将执行的步骤与命令，不产生实际网络传输)"
    Write-Host "  -Help, -h                 显示本帮助信息并退出"
    Write-Host ""
    Write-Host "说明:" -ForegroundColor Yellow
    Write-Host "  本脚本运行于 Windows PowerShell，通过 SSH/SCP 及 Windows 原生 tar 管道进行流式传输："
    Write-Host "  1. 优先使用 Windows 10/11 自带的 tar.exe 管道流传输，自动排除 .git/ 与编译垃圾，秒级解压；"
    Write-Host "  2. 自动定位上级目录中的 uftp_src-5.0.3.zip 离线包并同步到 Server 端上级目录；"
    Write-Host "  3. 同步完成后，Server 端目录结构与本地 100% 对齐，可直接开始一键集群部署与演示。"
}

if ($Help) {
    Show-Usage
    exit 0
}

# 1. 尝试从配置文件中解析未显式指定的 SERVER_HOST 与 SERVER_USER
if (Test-Path $ConfigFile) {
    Get-Content $ConfigFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^SERVER_HOST\s*=\s*(.+)$') {
            if (-not $Server) {
                $Server = $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
        if ($line -match '^SERVER_USER\s*=\s*(.+)$') {
            if (-not $User) {
                $User = $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
}

if (-not $User) {
    $User = "ubuntu"
}

if (-not $Server) {
    Write-Host "[FAIL] 未指定 Server 主机 IP 或 SSH 别名！" -ForegroundColor Red
    Write-Host "       请通过参数传入: .\scripts\sync_to_server.ps1 -Server <IP/别名>" -ForegroundColor Red
    Write-Host "       或者在 $ConfigFile 中配置 SERVER_HOST=<IP>" -ForegroundColor Red
    exit 1
}

# 2. 格式化 SSH 连接目标 Target
if ($Server.Contains("@")) {
    $Target = $Server
} elseif ($PSBoundParameters.ContainsKey('User')) {
    $Target = "${User}@${Server}"
} elseif ($Server -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') {
    $Target = "${User}@${Server}"
} else {
    # 针对 SSH Config 中的别名 (如 vm0)，直接保留别名以使用别名内指定的 User 与 IdentityFile
    $Target = $Server
}

Write-Host "[INFO] 连接目标 Server: $Target (远端目标目录: $RemoteDir)" -ForegroundColor Cyan

# 3. 自动定位本地 UFTP 源码包
$ResolvedUftpZip = ""
$searchPaths = @(
    $UftpZip,
    (Join-Path $PSScriptRoot "..\..\uftp_src-5.0.3.zip"),
    (Join-Path $PSScriptRoot "..\uftp_src-5.0.3.zip"),
    "..\uftp_src-5.0.3.zip",
    ".\uftp_src-5.0.3.zip",
    (Join-Path $env:USERPROFILE "uftp_src-5.0.3.zip")
)

foreach ($path in $searchPaths) {
    if (-not [string]::IsNullOrEmpty($path) -and (Test-Path $path)) {
        $ResolvedUftpZip = (Resolve-Path $path).ProviderPath
        break
    }
}

if ($ResolvedUftpZip) {
    Write-Host "[PASS] 定位到本地 UFTP 源码包: $ResolvedUftpZip" -ForegroundColor Green
} else {
    Write-Host "[WARN] 未在本地找到 uftp_src-5.0.3.zip，仅同步代码库" -ForegroundColor Yellow
}

# 4. 计算远端父目录 (例如 projects/wfb-ng-fl -> projects)
$remoteParent = $RemoteDir.Replace('\', '/')
if ($remoteParent.Contains('/')) {
    $remoteParent = $remoteParent.Substring(0, $remoteParent.LastIndexOf('/'))
} else {
    $remoteParent = "projects"
}

# 5. 检查本地是否存在 tar 命令
$hasTar = $null -ne (Get-Command "tar" -ErrorAction SilentlyContinue)

if ($DryRun) {
    Write-Host "[INFO] [DRY-RUN] 创建远端目录: ssh $Target `"mkdir -p '$RemoteDir' '$remoteParent'`"" -ForegroundColor DarkGray
    if ($hasTar) {
        Write-Host "[INFO] [DRY-RUN] 管道传输代码: tar --exclude=`".git`" --exclude=`"*.o`" -czf - . | ssh $Target `"tar -xzf - -C '$RemoteDir'`"" -ForegroundColor DarkGray
    } else {
        Write-Host "[INFO] [DRY-RUN] SCP 拷贝代码: scp -r . `"${Target}:${RemoteDir}`"" -ForegroundColor DarkGray
    }
    if ($ResolvedUftpZip) {
        Write-Host "[INFO] [DRY-RUN] 上传 UFTP 离线包: scp `"$ResolvedUftpZip`" `"${Target}:${remoteParent}/`"" -ForegroundColor DarkGray
    }
    Write-Host "[PASS] 演练完成 (DRY-RUN 模式未产生实际网络传输)" -ForegroundColor Green
    exit 0
}

# 6. 正式执行传输
try {
    Write-Host "[INFO] 正在创建远端父级目录..." -ForegroundColor Cyan
    & ssh $Target "mkdir -p '$RemoteDir' '$remoteParent'"
    if ($LASTEXITCODE -ne 0) {
        throw "无法通过 SSH 连接至 Server ($Target)，请检查网络或 SSH 密钥配置。"
    }

    if ($ResolvedUftpZip) {
        Write-Host "[INFO] 正在上传 UFTP 源码包至 ${Target}:${remoteParent}/ ..." -ForegroundColor Cyan
        & scp "$ResolvedUftpZip" "${Target}:${remoteParent}/"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] UFTP 源码包上传异常，退出码: $LASTEXITCODE" -ForegroundColor Yellow
        } else {
            Write-Host "[PASS] UFTP 源码包上传完成" -ForegroundColor Green
        }
    }

    Write-Host "[INFO] 正在传输项目代码 (排除 .git 与编译临时文件)..." -ForegroundColor Cyan
    if ($hasTar) {
        cmd.exe /c "tar --exclude=.git --exclude=*.o --exclude=*.so --exclude=logs -czf - . | ssh $Target `"tar -xzf - -C '$RemoteDir'`""
    } else {
        & scp -r . "${Target}:${RemoteDir}"
    }

    if ($LASTEXITCODE -ne 0) {
        throw "代码传输失败，退出码: $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "[PASS] 项目代码与 UFTP 源码包已成功同步至 Server 开发板 (${Target}:${RemoteDir})！" -ForegroundColor Green
    Write-Host ""
    Write-Host "后续操作指引 (在 PowerShell 中执行):" -ForegroundColor Yellow
    Write-Host "  1. 登录 Server 开发板:  ssh $Target" -ForegroundColor Cyan
    Write-Host "  2. 进入项目目录:        cd $RemoteDir" -ForegroundColor Cyan
    Write-Host "  3. 首次部署 Server 本机: sudo ./scripts/deploy_node.sh" -ForegroundColor Cyan
    Write-Host "  4. 建立集群免密互信:     ./scripts/setup_cluster_auth.sh" -ForegroundColor Cyan
    Write-Host "  5. 批量部署所有 Client:  ./scripts/deploy_cluster.sh" -ForegroundColor Cyan
    Write-Host "  6. 启动现场演示:        bash tests/real_hardware/run_fl_demo.sh all" -ForegroundColor Cyan
} catch {
    Write-Host "[FAIL] 传输过程中发生错误: $_" -ForegroundColor Red
    exit 1
}
