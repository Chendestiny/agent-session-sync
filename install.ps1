#Requires -Version 5.1
<#
agent-session-sync 一键安装（跨 Agent 会话同步：codex/hermes/dsh/zcode/workbuddy -> dsh）
用法（任意 agent 或终端）：
  irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
效果：
  1. 工具包落位 %USERPROFILE%\.agents\skills\session-sync（旧版自动备份为 .bak-<时间戳>）
  2. 注册为 skill，各 agent 可用「同步会话」一句话触发
  3. 检查 python / zstandard 环境并给出提示
#>
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dest = Join-Path $HOME '.agents\skills\session-sync'
$zip = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N') + '.zip')
$tmpDir = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N'))
$url = 'https://codeload.github.com/Chendestiny/agent-session-sync/zip/refs/heads/main'

Write-Host '[1/4] 下载 agent-session-sync (main) ...'
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
Write-Host '[2/4] 解压 ...'
Expand-Archive -Path $zip -DestinationPath $tmpDir -Force
$srcRoot = Join-Path $tmpDir 'agent-session-sync-main'
if (-not (Test-Path (Join-Path $srcRoot 'sync.py'))) { throw "下载内容异常：未找到 sync.py" }

Write-Host '[3/4] 落位到 skill 目录 ...'
if (-not (Test-Path (Split-Path $dest -Parent))) {
    New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
}
if (Test-Path $dest) {
    $bak = "$dest.bak-" + (Get-Date).ToString('yyyyMMdd-HHmmss')
    Move-Item -LiteralPath $dest -Destination $bak -Force
    Write-Host "      旧版本已备份：$bak"
}
Move-Item -LiteralPath $srcRoot -Destination $dest -Force

Write-Host '[4/4] 环境检查 ...'
try { python --version | Write-Host } catch { Write-Host '  [!] 未检测到 python，请安装 Python 3.10+' }
python -c "import zstandard" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host '  [!] 缺少 zstandard：请执行 pip install zstandard' }
Remove-Item $zip, $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '安装完成！'
Write-Host ("  目录：{0}" -f $dest)
Write-Host '  自检：cd 到该目录后执行 python sync.py selftest'
Write-Host '  触发：对任意 agent 说「同步会话」或「把 hermes 会话同步到 dsh」'
Write-Host '  注意：to-zcode 方向已移除（单向设计）；写入前 attach/prune 需退出 dsh'