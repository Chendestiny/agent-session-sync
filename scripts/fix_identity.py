# -*- coding: utf-8 -*-
"""修复 hermes 批次的 projcache identity 失配（重导前 dsh 写的旧 identity → 刷新为当前 header）。
同时修 attach/title_backfill 的根因：force 重写后 projcache 旧 identity 必须刷新。"""
import os, sys, json, glob, shutil, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentsync.readers import _zstd_decode_all, _parse_jsonl
from agentsync import dshwrite

home = os.path.expanduser('~')
root = os.path.join(home, '.dsh', 'sessions')
sp = os.path.join(home, '.dsh', 'storages', 'session_projcache.json')

if dshwrite.dsh_process_running():
    print('dsh 在运行，中止（退出 dsh 后重跑）')
    sys.exit(1)

pc = json.load(open(sp, encoding='utf-8'))
fixed = 0
for p in glob.glob(os.path.join(root, '*', 'import-2026*', 'session.jsonl.zstd')):
    sid = os.path.basename(os.path.dirname(p))
    row = pc['tables']['sessions'].get(sid)
    if not row:
        continue
    lines = _parse_jsonl(_zstd_decode_all(open(p, 'rb').read()).decode('utf-8', 'replace'))
    h = next(o for o in lines if o.get('type') == 'session')
    ident = row.get('identity', {})
    if ident.get('createdAt') != h.get('createdAt') or ident.get('cwd') != h.get('cwd'):
        # 刷新 identity 为当前 header
        new_ident = {'createdAt': h.get('createdAt')}
        if h.get('cwd') is not None:
            new_ident['cwd'] = h['cwd']
        row['identity'] = new_ident
        fixed += 1

if fixed:
    shutil.copy2(sp, sp + '.agentsync-bak-' + time.strftime('%Y%m%d-%H%M%S'))
    open(sp + '.tmp', 'w', encoding='utf-8').write(json.dumps(pc, ensure_ascii=False, indent=1))
    os.replace(sp + '.tmp', sp)
print(f'identity 刷新: {fixed} 条')
