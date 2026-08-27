# -*- coding: utf-8 -*-
"""把复活测试会话从 archivedSessionIds 移除（dsh 必须退出）。"""
import os, sys, json, shutil, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
home = os.path.expanduser('~')
sp = os.path.join(home, '.dsh', 'storages', 'workspace.json')

from agentsync.dshwrite import dsh_process_running

if dsh_process_running():
    print('dsh 在运行，中止（退出 dsh 后重跑）')
    sys.exit(1)

ws = json.load(open(sp, encoding='utf-8'))
g = ws['global']
arch = set(g.get('archivedSessionIds', []))
# 精确移除这 4 条（当前磁盘上存在的复活测试会话）
REMOVE = [
    'import-sess_9aad22da-8cc9-4dc5-b256-c526573c63eb',
    'import-sess_6786cad6-fdf4-492d-b1c8-49e4464eb890',
    'import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a',
    'import-a5b98a51-577a-4900-8eee-a2b69ae03cb9',
]
removed = [r for r in REMOVE if r in arch]
for r in removed:
    arch.discard(r)
g['archivedSessionIds'] = sorted(arch)
shutil.copy2(sp, sp + '.agentsync-bak-' + time.strftime('%Y%m%d-%H%M%S'))
open(sp + '.tmp', 'w', encoding='utf-8').write(json.dumps(ws, ensure_ascii=False, indent=1))
os.replace(sp + '.tmp', sp)
print(f'已从归档列表移除 {len(removed)} 条，剩余归档 {len(arch)} 条')
print('重启 dsh 后这 4 条应出现在对应分组（1 条嵌套路径的在未分组）')
