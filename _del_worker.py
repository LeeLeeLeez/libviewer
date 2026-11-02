# -*- coding: utf-8 -*-
"""_del_worker.py —— 删除页的外部进程（在装了 olefile 的 venv 里跑）。

GUI 进程是系统 Python 312（有 tkinter、**无 olefile**），所以一切触及
`lib_delete` 的动作都放到这里执行。好处：
  · GUI 不必 import olefile；
  · worker 崩了也不影响 GUI；
  · 输出可控、可复现（同样的命令可以手工重跑复核）。

用法
----
列库状态（解析各库正本路径 + 读名单，输出 JSON 到 stdout）：
    python _del_worker.py --mode list

预演 / 真删（stdout 混排人类可读日志 + 一行 `@{json}` 供 GUI 解析）：
    python _del_worker.py --mode sch --lib <path> --names A,B,C [--dry]
    python _del_worker.py --mode pcb --lib <path> --names X,Y   [--dry]
"""
import os
import sys
import json
import argparse

# 🔴🔴 强制 stdout/stderr 为 UTF-8，必须在任何 print 之前。
# 原因：本机 Windows 默认 locale = cp936(GBK)。若环境里没有
#   PYTHONUTF8=1 / PYTHONIOENCODING=utf-8（用户双击 .cmd 启动 GUI 就是这种），
#   worker 的 print 会按 GBK 编码；而 GUI 侧固定用 encoding='utf-8' 解码
#   → 中文全乱码（典型如 `ʫ��ʶ�ͨ`）。在源头就把编码钉死最稳。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lib_reader as LR          # noqa: E402   （不依赖 olefile）
import lib_delete as LD          # noqa: E402   （依赖 olefile）


def _why(info, path):
    """给「不可用」一个人类可读的原因。"""
    if info is None:
        return '找不到'
    return {'history': '只有 History 快照（非正本）',
            'backup': '只有 .bak 备份（非正本）',
            'dlp': '密文库，需先转明文',
            'other': '非 OLE 明文',
            'missing': '文件不存在'}.get(info.get('kind'), str(info.get('kind')))


def mode_list():
    """输出 {'cat|sch': {...}, 'cat|pcb': {...}} 的 JSON。"""
    base = LR.get_base()
    out = {}
    for cat, folder, stem in LR.CATEGORIES:
        srcs = LR.resolve_sources(folder, stem, base=base)
        for ext, key in (('.SchLib', 'sch'), ('.PcbLib', 'pcb')):
            info = srcs.get(key)
            item = {'ok': False, 'name': stem + ext, 'why': '找不到',
                    'path': None, 'count': 0, 'items': []}
            if info and info.get('kind') == 'current' and \
                    LD.kind(info['path']) == 'ole':
                try:
                    if key == 'sch':
                        rows = LR.read_schlib(info)
                    else:
                        rows = LR.read_pcblib(info)
                    names = [r['name'] for r in rows if r.get('name')]
                    item.update({'ok': True, 'why': '', 'path': info['path'],
                                 'count': len(names),
                                 'items': sorted(names)})
                except Exception as ex:
                    item['why'] = '读取失败：%r' % (ex,)
            else:
                if info and info.get('kind') == 'current':
                    item['why'] = _why({'kind': LD.kind(info['path'])},
                                       info['path'])
                else:
                    item['why'] = _why(info, None)
            out['%s|%s' % (cat, key)] = item
    return out


def _removed(mode):
    """删除子命令已于 2026-10-22 下线（「删除」tab 整体移除）。"""
    print('✘ 删除功能已于 2026-10-22 下线', file=sys.stderr)
    return 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', required=True, choices=['list'])
    ap.add_argument('--lib')
    ap.add_argument('--names')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    if a.mode == 'list':
        print(json.dumps(mode_list(), ensure_ascii=False))
        return 0
    return _removed(a.mode)


if __name__ == '__main__':
    sys.exit(main())
