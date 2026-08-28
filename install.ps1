#Requires -Version 5.1
<#
agent-session-sync 一键安装（跨 Agent 会话同步：codex/hermes/dsh/zcode/workbuddy -> dsh）
用法（任意 agent 或终端）：
  在线：irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
  离线（GitHub 访问慢/不可达）：浏览器下载 zip 解压，进入 agent-session-sync-main 目录执行
        powershell -ExecutionPolicy Bypass -File .\install.ps1
        （脚本检测到旁边的 sync.py 就直接本地落位，不再联网）
  也可显式指定来源（zip 文件或已解压目录）：
        .\install.ps1 -Source C:\Downloads\agent-session-sync-main.zip
  加速镜像前缀（可选，拼在 GitHub 下载地址前面）：
        $env:ASS_GH_PREFIX = 'https://ghfast.top/'
效果：
  1. 工具包落位 %USERPROFILE%\.agents\skills\session-sync（旧版自动备份为 .bak-<时间戳>）
  2. 注册为 skill，各 agent 可用「同步会话」一句话触发
  3. 检查 python / zstandard 环境并给出提示
#>
param(
    # 可选：本地 zip 或已解压目录（离线安装）。留空则自动：脚本旁有 sync.py 就地安装，否则在线下载。
    [string]$Source
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dest = Join-Path $HOME '.agents\skills\session-sync'
$zip = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N') + '.zip')
$tmpDir = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N'))
$prefix = [string]$env:ASS_GH_PREFIX
$url = 'https://github.com/Chendestiny/agent-session-sync/archive/refs/heads/main.zip'

Write-Host '[1/3] 获取 agent-session-sync (main) ...'
$srcRoot = $null
if ($Source) {
    if (Test-Path $Source -PathType Leaf) {              # 本地 zip
        Expand-Archive -Path $Source -DestinationPath $tmpDir -Force
    } elseif (Test-Path $Source -PathType Container) {  # 本地目录
        $srcRoot = (Resolve-Path $Source).Path
        Write-Host "      使用本地目录：$srcRoot"
    } else {
        throw "来源不存在：$Source"
    }
} elseif ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'sync.py'))) {
    $srcRoot = $PSScriptRoot                             # 解压后原地安装，不联网
    Write-Host "      使用本地目录：$srcRoot"
} else {
    try {
        Invoke-WebRequest -Uri "$prefix$url" -OutFile $zip -UseBasicParsing
    } catch {
        throw "下载失败：GitHub 访问慢可手动下载 zip 后运行 .\install.ps1 -Source <zip路径>（或先 `$env:ASS_GH_PREFIX='https://ghfast.top/' 设加速前缀）"
    }
}
if (-not $srcRoot) {
    # 在解压产物中定位包含 sync.py 的目录（兼容 zip 内层目录名变化）
    $hit = Get-ChildItem -Path $tmpDir -Recurse -Filter sync.py -File | Select-Object -First 1
    if (-not $hit) { throw '来源异常：未找到 sync.py' }
    $srcRoot = $hit.DirectoryName
}

Write-Host '[2/3] 落位到 skill 目录 ...'
if (-not (Test-Path (Split-Path $dest -Parent))) {
    New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
}
if (Test-Path $dest) {
    $bak = "$dest.bak-" + (Get-Date).ToString('yyyyMMdd-HHmmss')
    Move-Item -LiteralPath $dest -Destination $bak -Force
    Write-Host "      旧版本已备份：$bak"
}
New-Item -ItemType Directory -Path $dest -Force | Out-Null
# 白名单复制（只拷 skill 包需要的文件，工作副本里的运行产物/私有数据不会带入）
$bundle = 'SKILL.md','AGENTS.md','README.md','README_EN.md','LICENSE',
          'sync.py','sync-finish.py','agentsync','docs','tools','examples','scripts',
          'install.ps1','install.sh'
foreach ($item in $bundle) {
    $p = Join-Path $srcRoot $item
    if (Test-Path -LiteralPath $p) {
        Copy-Item -LiteralPath $p -Destination $dest -Recurse -Force
    }
}

Write-Host '[3/3] 环境检查 ...'
try { python --version | Write-Host } catch { Write-Host '  [!] 未检测到 python，请安装 Python 3.10+' }
try {
    python -c "import zstandard" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host '  [!] 缺少 zstandard：请执行 pip install zstandard' }
} catch { Write-Host '  [!] 未检测到 python，请安装 Python 3.10+' }
Remove-Item $zip, $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '安装完成！'
Write-Host ("  目录：{0}" -f $dest)
Write-Host '  自检：cd 到该目录后执行 python sync.py selftest'
Write-Host '  触发：对任意 agent 说「同步会话」或「把 hermes 会话同步到 dsh」'
Write-Host '  注意：to-zcode 方向已移除（单向设计）；写入前 attach/prune 需退出 dsh'
