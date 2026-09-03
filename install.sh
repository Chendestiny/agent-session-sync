#!/usr/bin/env bash
# agent-session-sync 一键安装（跨 Agent 会话同步：codex/hermes/dsh/zcode/workbuddy -> dsh）
# 用法：
#   在线（Linux / macOS / WSL）：
#     curl -fsSL https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.sh | bash
#   离线（GitHub 访问慢/不可达）：浏览器下载 zip 解压，进入 agent-session-sync-main 目录执行：
#     bash install.sh
#   也可显式指定来源（zip 文件或已解压目录）：
#     bash install.sh /path/to/agent-session-sync-main.zip
#   加速镜像前缀（可选）：ASS_GH_PREFIX=https://ghfast.top/ （拼在 GitHub 下载地址前面）
# 效果：
#   1. 工具包落位 ~/.agents/skills/session-sync（旧版自动备份为 .bak-<时间戳>）
#   2. 注册为 skill，各 agent 可用「同步会话」一句话触发
#   3. 检查 python / zstandard 环境并给出提示
set -euo pipefail

DEST="$HOME/.agents/skills/session-sync"
REPO_ZIP='https://github.com/Chendestiny/agent-session-sync/archive/refs/heads/main.zip'
PREFIX="${ASS_GH_PREFIX:-}"
SRC_ARG="${1:-}"

step() { printf '[%s] %s\n' "$1" "$2"; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

# 找一个真正可用的 Python >= 3.10（不能只看 command -v：Windows 的 python3 假 stub 会静默失败）
PY=''
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        PY="$c"; break
    fi
done

# 脚本自身所在目录（curl | bash 管道执行时为空；旁边有 sync.py 则原地安装，不联网）
SCRIPT_DIR=''
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/sync.py" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

src_root=''

step '1/3' '获取 agent-session-sync (main) ...'
if [ -n "$SRC_ARG" ]; then
    if [ -f "$SRC_ARG" ]; then                       # 本地 zip
        [ -n "$PY" ] || die '需要 Python 3.10+（用于解压 zip）'
        "$PY" -m zipfile -e "$SRC_ARG" "$WORK/unzip/"
    elif [ -d "$SRC_ARG" ]; then                     # 本地目录
        src_root="$SRC_ARG"
    else
        die "来源不存在：$SRC_ARG"
    fi
elif [ -n "$SCRIPT_DIR" ]; then                      # 解压后原地安装
    src_root="$SCRIPT_DIR"
    echo "      使用本地目录：$src_root"
else                                                 # 在线下载
    [ -n "$PY" ] || die '需要 Python 3.10+（用于解压 zip）'
    dl_ok=0
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 -o "$WORK/ass.zip" "${PREFIX}${REPO_ZIP}" 2>/dev/null && dl_ok=1
        # 公司代理/SSL 拦截下 schannel 可能报吊销检查失败，降级重试（Linux curl 首次即成功，不会走到这）
        if [ "$dl_ok" -ne 1 ]; then
            curl -fsSL --retry 3 --ssl-no-revoke -o "$WORK/ass.zip" "${PREFIX}${REPO_ZIP}" 2>/dev/null && dl_ok=1
        fi
    fi
    if [ "$dl_ok" -ne 1 ] && command -v wget >/dev/null 2>&1; then
        wget -q -O "$WORK/ass.zip" "${PREFIX}${REPO_ZIP}" && dl_ok=1
    fi
    [ "$dl_ok" -eq 1 ] || die '下载失败：GitHub 访问慢可手动下载 zip 后执行 bash install.sh <zip路径>（或设 ASS_GH_PREFIX 加速前缀）'
    "$PY" -m zipfile -e "$WORK/ass.zip" "$WORK/unzip/"
fi
if [ -z "$src_root" ]; then
    # 在解压产物中定位包含 sync.py 的目录（兼容 zip 内层目录名变化）
    hit="$(find "$WORK/unzip" -name sync.py -type f | head -n 1 || true)"
    [ -n "$hit" ] || die '来源异常：未找到 sync.py'
    src_root="$(dirname "$hit")"
fi
[ -f "$src_root/sync.py" ] || die "来源异常：$src_root 下没有 sync.py"

step '2/3' '落位到 skill 目录 ...'
mkdir -p "$(dirname "$DEST")"
if [ -d "$DEST" ]; then
    bak="$DEST.bak-$(date +%Y%m%d-%H%M%S)"
    mv "$DEST" "$bak"
    echo "      旧版本已备份：$bak"
fi
mkdir -p "$DEST"
# 白名单复制（只拷 skill 包需要的文件，工作副本里的运行产物/私有数据不会带入）
for item in SKILL.md AGENTS.md README.md README_EN.md LICENSE \
            sync.py sync-finish.py agentsync docs tools examples scripts \
            install.ps1 install.sh; do
    [ -e "$src_root/$item" ] && cp -a "$src_root/$item" "$DEST/"
done

step '3/4' '安装全局命令 session-sync / ass ...'
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
for n in session-sync ass; do
cat > "$BIN_DIR/$n" <<'SHIM'
#!/bin/sh
exec python3 "$HOME/.agents/skills/session-sync/sync.py" "$@"
SHIM
chmod +x "$BIN_DIR/$n"
done
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "  [!] $BIN_DIR 不在 PATH：请加入 shell 配置（export PATH=\"\$HOME/.local/bin:\$PATH\"）" ;;
esac
echo "      任意目录可用：ass web（快捷）或 session-sync web（dashboard）等"

step '4/5' '桥接各 agent skills 目录（symlink -> 唯一源）...'
# 各家 skills 目录不统一（通用位 ~/.agents/skills 只有部分 agent 认）：检测到即桥接，改一处全家生效
for d in "$HOME/.workbuddy/skills" "$HOME/.workbuddy/.agent/skills" \
         "$HOME/.workbuddy-ai/skills" "$HOME/.workbuddy-ai/.agent/skills" \
         "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.hermes/skills" \
         "$HOME/.dsh/skills" "$HOME/.qoder/skills" \
         "$HOME/.config/opencode/skill" "$HOME/.config/opencode/skills"; do
    [ -d "$d" ] || continue
    link="$d/session-sync"
    if [ -L "$link" ]; then
        rm -f "$link"
    elif [ -e "$link" ]; then
        echo "  [!] 已存在实体目录，跳过（如需统一可删除后重装）：$link"
        continue
    fi
    ln -sfn "$DEST" "$link" && echo "      bridged: $link -> $DEST"
done

step '5/5' '环境检查 ...'
if [ -n "$PY" ]; then
    "$PY" --version
    "$PY" -c 'import zstandard' 2>/dev/null \
        || echo '  [!] 缺少 zstandard：请执行 pip install zstandard'
else
    echo '  [!] 未检测到可用的 Python 3.10+：macOS 可 brew install python3，Debian/Ubuntu 可 sudo apt install python3 python3-pip'
fi

echo ''
echo '安装完成！'
echo "  目录：$DEST"
echo '  全局：任意目录 session-sync web（dashboard）'
echo '  自检：cd 到该目录后执行 python sync.py selftest'
echo '  触发：对任意 agent 说「同步会话」或「把 hermes 会话同步到 dsh」'
echo '  注意：to-zcode 方向已移除（单向设计）；写入前 attach/prune 需退出 dsh'
