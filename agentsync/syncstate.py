"""每源「上次成功同步时间」状态（仅增量的基准）。

状态文件 .agentsync-state.json 放在 dsh sessions 根（与墓碑
.agentsync-deleted.json 同层）：repo 副本 / skill 副本 / 其他机器路径
只要指向同一个 dsh 根，就共享同一份增量基准。

仅记录 to-dsh --apply 成功跑完的时间；dry-run 不记。
"""
from __future__ import annotations

import json
import os
import time

# 时钟粒度/边界重叠冗余：各源时间戳精度不一（hermes 秒、codex 文件 mtime、
# zcode/workbuddy 毫秒），回看 15 分钟；重复执行幂等，重叠无副作用。
OVERLAP_MS = 15 * 60 * 1000


def state_path(dsh_root) -> str:
    return os.path.join(str(dsh_root), ".agentsync-state.json")


def load(dsh_root) -> dict:
    """{source: last_sync_ms}；无文件/损坏返回 {}。"""
    path = state_path(dsh_root)
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
        return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (OSError, ValueError):
        return {}


def mark(dsh_root, sources: list[str]) -> None:
    """把给定源的增量基准推进到当前时刻（其余源保留原值）。"""
    path = state_path(dsh_root)
    state = load(dsh_root)
    now = int(time.time() * 1000)
    for src in sources:
        state[src] = max(state.get(src, 0), now)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def cutoff_for(state: dict, source: str) -> int | None:
    """该源的增量下界（毫秒）；无记录（首次）返回 None = 不过滤。"""
    last = state.get(source, 0)
    return (last - OVERLAP_MS) if last else None


def apply_cutoff(sessions: list, cutoff_ms: int) -> list:
    """按最后活跃时间过滤：updated_at 缺失退 created_at。"""
    return [s for s in sessions if (getattr(s, "updated_at", 0) or s.created_at or 0) >= cutoff_ms]
