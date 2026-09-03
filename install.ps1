# agent-session-sync installer (cross-agent session sync: codex/hermes/dsh/zcode/workbuddy -> dsh)
# Usage (any agent or terminal):
#   Online:  irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
#   Offline (slow/blocked GitHub): download the repo zip, extract, then inside agent-session-sync-main run
#            powershell -ExecutionPolicy Bypass -File .\install.ps1
#            (sync.py next to the script -> local install, zero network)
#   Explicit source: .\install.ps1 -Source <path to repo zip OR extracted folder>
#   Mirror prefix (optional, prepended to the GitHub download URL):
#            $env:ASS_GH_PREFIX = 'https://ghfast.top/'
# Effects:
#   1. Toolkit lands in %USERPROFILE%\.agents\skills\session-sync (old copy backed up as .bak-<timestamp>)
#   2. Registers as a skill: say "sync sessions" (or Chinese) to any agent to trigger
#   3. Checks python / zstandard and prints hints
# NOTE: keep this file ASCII-only and BOM-less. It must survive `irm | iex` on both
#       PowerShell 5.1 and PowerShell 7, where a UTF-8 BOM glues onto the first token
#       and breaks parsing. Chinese docs live in README.md / SKILL.md / AGENTS.md.
param(
    # Optional: local zip or extracted folder (offline install). Empty = auto:
    # use the copy beside this script if sync.py is there, otherwise download.
    [string]$Source
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dest = Join-Path $HOME '.agents\skills\session-sync'
$zip = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N') + '.zip')
$tmpDir = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N'))
$prefix = [string]$env:ASS_GH_PREFIX
$url = 'https://github.com/Chendestiny/agent-session-sync/archive/refs/heads/main.zip'

Write-Host '[1/3] Fetch agent-session-sync (main) ...'
$srcRoot = $null
if ($Source) {
    if (Test-Path $Source -PathType Leaf) {              # local zip
        Expand-Archive -Path $Source -DestinationPath $tmpDir -Force
    } elseif (Test-Path $Source -PathType Container) {  # local folder
        $srcRoot = (Resolve-Path $Source).Path
        Write-Host "      Using local folder: $srcRoot"
    } else {
        throw "Source not found: $Source"
    }
} elseif ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'sync.py'))) {
    $srcRoot = $PSScriptRoot                             # extracted-in-place install, no network
    Write-Host "      Using local folder: $srcRoot"
} else {
    $dlOk = $false
    try {
        Invoke-WebRequest -Uri "$prefix$url" -OutFile $zip -UseBasicParsing
        $dlOk = $true
    } catch { }
    if (-not $dlOk -and (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
        # corporate proxy / SSL interception fallback (Windows 10+ ships curl.exe)
        & curl.exe -fsSL --retry 3 --ssl-no-revoke -o "$zip" "$prefix$url"
        if ($LASTEXITCODE -eq 0) { $dlOk = $true }
    }
    if (-not $dlOk) {
        throw "Download failed. Download the repo zip manually and run: .\install.ps1 -Source <zip path> (or set `$env:ASS_GH_PREFIX to a mirror prefix first)"
    }
    Expand-Archive -Path $zip -DestinationPath $tmpDir -Force
}
if (-not $srcRoot) {
    # locate the folder containing sync.py inside the extracted zip (tolerates inner folder renames)
    $hit = Get-ChildItem -Path $tmpDir -Recurse -Filter sync.py -File | Select-Object -First 1
    if (-not $hit) { throw 'Unexpected content: sync.py not found' }
    $srcRoot = $hit.DirectoryName
}

Write-Host '[2/3] Place into skill directory ...'
if (-not (Test-Path (Split-Path $dest -Parent))) {
    New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
}
if (Test-Path $dest) {
    $bak = "$dest.bak-" + (Get-Date).ToString('yyyyMMdd-HHmmss')
    Move-Item -LiteralPath $dest -Destination $bak -Force
    Write-Host "      Old version backed up: $bak"
}
New-Item -ItemType Directory -Path $dest -Force | Out-Null
# allowlist copy (only what the skill bundle needs; worktree artifacts/private data never carried over)
$bundle = 'SKILL.md','AGENTS.md','README.md','README_EN.md','LICENSE',
          'sync.py','sync-finish.py','agentsync','docs','tools','examples','scripts',
          'install.ps1','install.sh'
foreach ($item in $bundle) {
    $p = Join-Path $srcRoot $item
    if (Test-Path -LiteralPath $p) {
        Copy-Item -LiteralPath $p -Destination $dest -Recurse -Force
    }
}

Write-Host '[3/4] Install global commands: session-sync / ass ...'
$binDir = Join-Path $HOME '.agents\bin'
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
$cmdBody = "@echo off`r`npython `"%USERPROFILE%\.agents\skills\session-sync\sync.py`" %*`r`n"
$shBody = "#!/bin/sh`nexec python `"`$HOME/.agents/skills/session-sync/sync.py`" `"$@`"`n"
foreach ($n in 'session-sync', 'ass') {
    [System.IO.File]::WriteAllText((Join-Path $binDir "$n.cmd"), $cmdBody)
    [System.IO.File]::WriteAllText((Join-Path $binDir $n), $shBody)
}
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$hit = @($userPath -split ';' | ForEach-Object { $_.TrimEnd('\') } | Where-Object { $_ -eq $binDir.TrimEnd('\') })
if ($hit.Count -eq 0) {
    $newPath = if ($userPath -and $userPath.Trim()) { $userPath.TrimEnd(';') + ';' + $binDir } else { $binDir }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Host "      Added $binDir to user PATH (takes effect in NEW terminals)"
}
Write-Host '      Run  ass web  (or session-sync web) from anywhere (dashboard);  ass status  etc. all work'

Write-Host '[4/5] Bridge skill into per-agent skills dirs (junction -> single source) ...'
# Each agent keeps its own skills dir and most do NOT read the common ~/.agents/skills.
# For every detected per-agent skills dir, create a junction pointing back to the single
# source, so one update is visible to all agents. Only existing dirs are bridged.
$bridgeCandidates = @(
    (Join-Path $HOME '.workbuddy\skills'),      (Join-Path $HOME '.workbuddy\.agent\skills'),
    (Join-Path $HOME '.workbuddy-ai\skills'),   (Join-Path $HOME '.workbuddy-ai\.agent\skills'),
    (Join-Path $HOME '.claude\skills'),
    (Join-Path $HOME '.codex\skills'),
    (Join-Path $HOME '.hermes\skills'),
    (Join-Path $HOME '.dsh\skills'),
    (Join-Path $HOME '.qoder\skills'),
    (Join-Path $HOME '.config\opencode\skill'),
    (Join-Path $HOME '.config\opencode\skills')
)
foreach ($d in $bridgeCandidates) {
    if (-not (Test-Path $d)) { continue }
    $link = Join-Path $d 'session-sync'
    if (Test-Path $link) {
        if (Get-Item $link -Force | Where-Object { $_.LinkType }) {
            # Delete the junction itself only: Remove-Item -Recurse on PS5.1 may
            # traverse INTO the target and wipe the single source.
            [System.IO.Directory]::Delete($link)
        } else {
            Write-Host "      [!] real directory exists, skipped (delete it and reinstall to bridge): $link"
            continue
        }
    }
    New-Item -ItemType Junction -Path $link -Target $dest | Out-Null
    Write-Host "      bridged: $link -> $dest"
}

Write-Host '[5/5] Environment check ...'
try { python --version | Write-Host } catch { Write-Host '  [!] python not found, please install Python 3.10+' }
try {
    python -c "import zstandard" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host '  [!] zstandard missing: run  pip install zstandard' }
} catch { Write-Host '  [!] python not found, please install Python 3.10+' }
Remove-Item $zip, $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host 'Install done!'
Write-Host ("  Location: {0}" -f $dest)
Write-Host '  Global:   ass web  /  session-sync web   (dashboard, any directory; new terminals)'
Write-Host '  Self-test: cd there and run  python sync.py selftest'
Write-Host '  Trigger: tell any agent "sync sessions to dsh"'
Write-Host '  Note: to-zcode direction is removed (one-way design); exit dsh before attach/prune'
