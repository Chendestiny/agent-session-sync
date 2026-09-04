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
#   1. Toolkit lands in %USERPROFILE%\.agents\skills\session-sync (old copy backed up as .bak-<timestamp>, newest 2 kept)
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

# Run a native command with stderr silenced. PS 5.1 under EAP=Stop turns any native stderr
# line (e.g. pip warnings) into a terminating NativeCommandError; scope EAP=Continue instead.
function Invoke-QuietNative([string]$Exe, [string[]]$ArgList) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @ArgList 2>&1 | Out-Null } catch { } finally { $ErrorActionPreference = $prev }
    return $LASTEXITCODE
}

$dest = Join-Path $HOME '.agents\skills\session-sync'
$zip = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N') + '.zip')
$tmpDir = Join-Path $env:TEMP ('ass-' + [guid]::NewGuid().ToString('N'))
$prefix = [string]$env:ASS_GH_PREFIX
$url = 'https://github.com/Chendestiny/agent-session-sync/archive/refs/heads/main.zip'

Write-Host '[1/6] Fetch agent-session-sync (main) ...'
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

Write-Host '[2/6] Place into skill directory ...'
if (-not (Test-Path (Split-Path $dest -Parent))) {
    New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
}
if (Test-Path $dest) {
    $bak = "$dest.bak-" + (Get-Date).ToString('yyyyMMdd-HHmmss')
    while (Test-Path -LiteralPath $bak) { Start-Sleep -Milliseconds 500; $bak = "$dest.bak-" + (Get-Date).ToString('yyyyMMdd-HHmmss') }   # same-second collision guard
    Move-Item -LiteralPath $dest -Destination $bak -Force
    Write-Host "      Old version backed up: $bak"
}
# Rolling backups: keep only the newest 2 (incl. the one just made), so repeated
# reinstalls never pile up dozens of .bak-* dirs in the skills folder.
$keepBak = 2
$baks = @(Get-ChildItem -LiteralPath (Split-Path $dest -Parent) -Filter 'session-sync.bak-*' -Directory | Sort-Object Name)
if ($baks.Count -gt $keepBak) {
    $baks | Select-Object -First ($baks.Count - $keepBak) | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
        Write-Host "      Removed old backup: $($_.Name)"
    }
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

Write-Host '[3/6] Python runtime ...'
# No system python (e.g. user never installed hermes or any py tooling)? Fall back to an
# embedded CPython under ~/.agents/py-runtime: no admin, no system changes, shims call it
# directly and it is appended to user PATH so bare `python` works in agent shells too.
$pyExe = $null
try { $pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source } catch {}
if ($pyExe) {
    if ((Invoke-QuietNative $pyExe @('--version')) -ne 0) { $pyExe = $null }   # WindowsApps store stub
}
if (-not $pyExe) {
    $rt = Join-Path $HOME '.agents\py-runtime'
    $embedded = Join-Path $rt 'python.exe'
    if (-not (Test-Path $embedded)) {
        Write-Host '      python not found - installing embedded CPython (~12 MB, no admin) ...'
        $pyver = '3.11.9'
        $pyzip = Join-Path $env:TEMP 'ass-py-embed.zip'
        $mirrors = @("https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip",
                     "https://registry.npmmirror.com/-/binary/python/$pyver/python-$pyver-embed-amd64.zip")
        $done = $false
        foreach ($u in $mirrors) {
            try { Invoke-WebRequest $u -OutFile $pyzip; $done = $true; break } catch {}
        }
        if (-not $done) { throw 'Failed to download embedded Python (python.org and npmmirror both failed)' }
        New-Item -ItemType Directory -Path $rt -Force | Out-Null
        Expand-Archive $pyzip -DestinationPath $rt -Force
        Remove-Item $pyzip -Force -ErrorAction SilentlyContinue
        $tag = $pyver.Substring(0, $pyver.LastIndexOf('.')).Replace('.', '')   # 3.11.9 -> 311
        $pth = Join-Path $rt "python$tag`._pth"
        if (Test-Path $pth) { (Get-Content $pth) -replace '^#\s*import site', 'import site' | Set-Content $pth }
        $gp = Join-Path $env:TEMP 'ass-get-pip.py'
        Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile $gp
        if ((Invoke-QuietNative $embedded @($gp, '--no-warn-script-location')) -ne 0) { throw 'get-pip failed inside embedded runtime' }
        if ((Invoke-QuietNative $embedded @('-m','pip','install','zstandard','--no-warn-script-location')) -ne 0) {
            if ((Invoke-QuietNative $embedded @('-m','pip','install','zstandard','--no-warn-script-location','-i','https://mirrors.aliyun.com/pypi/simple/')) -ne 0) {
                Write-Host '      [!] zstandard install into runtime failed; rerun installer or run: ass doctor'
            }
        }
        Remove-Item $gp -Force -ErrorAction SilentlyContinue
    }
    $pyExe = $embedded
    $userPath0 = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (@($userPath0 -split ';' | ForEach-Object { $_.TrimEnd('\') }) -notcontains $rt.TrimEnd('\')) {
        [Environment]::SetEnvironmentVariable('Path', ($userPath0.TrimEnd(';') + ';' + $rt), 'User')
        Write-Host "      Added $rt to user PATH (bare 'python' works in NEW terminals)"
    }
    Write-Host "      Using embedded runtime: $pyExe"
} else {
    Write-Host "      Using system python: $pyExe"
    # the interpreter we hardcode into shims must be self-sufficient (often an agent venv,
    # e.g. hermes bundles one - machines without hermes may have no python at all)
    if ((Invoke-QuietNative $pyExe @('-c','import zstandard')) -ne 0) {
        if ((Invoke-QuietNative $pyExe @('-m','pip','install','zstandard','--no-warn-script-location')) -eq 0) {
            Write-Host '      Installed zstandard into the detected python'
        } else {
            Write-Host '      [!] zstandard missing in detected python: run  ass doctor  later, or  pip install zstandard'
        }
    }
}

Write-Host '[4/6] Install global commands: session-sync / ass ...'
$binDir = Join-Path $HOME '.agents\bin'
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
$pyPosix = $pyExe.Replace('\', '/')
$cmdBody = "@echo off`r`n`"$pyExe`" `"%USERPROFILE%\.agents\skills\session-sync\sync.py`" %*`r`n"
$shBody = "#!/bin/sh`nexec `"$pyPosix`" `"`$HOME/.agents/skills/session-sync/sync.py`" `"$@`"`n"
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

Write-Host '[5/6] Bridge skill into per-agent skills dirs (junction -> single source) ...'
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

Write-Host '[6/6] Environment check ...'
try { & $pyExe --version | Write-Host } catch { Write-Host '  [!] python runtime broken, rerun installer' }
if ((Invoke-QuietNative $pyExe @('-c','import zstandard')) -ne 0) {
    Write-Host '  [!] zstandard missing: run  ass doctor  (auto-installs)'
}
Remove-Item $zip, $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host 'Install done!'
Write-Host ("  Location: {0}" -f $dest)
Write-Host '  Global:   ass web  /  session-sync web   (dashboard, any directory; new terminals)'
Write-Host '  Self-test: cd there and run  python sync.py selftest'
Write-Host '  Trigger: tell any agent "sync sessions to dsh"'
Write-Host '  Note: to-zcode direction is removed (one-way design); exit dsh before attach/prune'
