# -*- coding: utf-8 -*-
"""
build_cache.py —— 预解析各类别库，生成 lib_cache.json

为什么需要缓存：
  · IC.PcbLib 有 28 MB，每次解析要数秒；GUI 启动不该等。
  · 库文件在 AD 里改动后会变成 DLP 密文，解析结果应及时固化。

用法：
    python build_cache.py            # 重建全部
    python build_cache.py --only ic  # 只重建某类别
"""
import sys, os, json, time, datetime

# 🔴 强制 stdout 为 UTF-8（与 main._utf8_env() 双保险）。
#    本机默认 locale=cp936；若被以 GBK 环境启动，
#    这里的中文输出会乱码。reconfigure 在源头钉死。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 🔴🔴 打包(frozen) 后 HERE 必须是 **exe 所在目录** ——
#      onefile 下 `__file__` = sys._MEIPASS（每次运行都不同的临时解包目录），
#      lib_cache.json 会被写进那里 → **GUI 永远读不到新数据**
#      （2026-10-14 实测踩到：日志里出现 `写入 C:\...\Temp\_MEI0000xxxx\lib_cache.json`）。
if getattr(sys, 'frozen', False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_reader as LR

CACHE = os.path.join(HERE, 'lib_cache.json')


def build(cats=None):
    out = {'built_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'lib_base': LR.get_base(), 'categories': []}
    for cat, folder, stem in LR.CATEGORIES:
        if cats and folder not in cats:
            continue
        t0 = time.time()
        r = LR.load_category(cat, folder, stem)
        dt = time.time() - t0

        def srcinfo(s):
            if s is None:
                return None
            return {'kind': s['kind'],
                    'file': os.path.basename(s['path']),
                    'size': s['size'],
                    'mtime': datetime.datetime.fromtimestamp(
                        s['mtime']).strftime('%Y-%m-%d %H:%M')}

        entry = {
            'cat': cat, 'folder': folder, 'stem': stem,
            'sch_src': srcinfo(r['sch_src']),
            'pcb_src': srcinfo(r['pcb_src']),
            'sch': [{'name': x['name'], '料号': x['料号'], 'MPN': x['MPN'],
                     '厂商': x['厂商'], '封装': x['封装'], 'spec': x['spec'],
                     'Comment': x['Comment'], 'template': x['template'],
                     'cls': x.get('cls', 'normal'),
                     'models': x.get('models', [])}
                    for x in r['sch']],
            'pcb': [{'name': x['name']} for x in r['pcb']],
        }
        out['categories'].append(entry)
        print('%-9s 符号 %4d  封装 %3d  %.1fs  [sch:%s pcb:%s]' % (
            cat, len(entry['sch']), len(entry['pcb']), dt,
            (entry['sch_src'] or {}).get('kind', '-'),
            (entry['pcb_src'] or {}).get('kind', '-')))
    return out


def main():
    cats = None
    if '--only' in sys.argv:
        i = sys.argv.index('--only')
        if i + 1 < len(sys.argv):
            cats = [sys.argv[i + 1]]
    # 全量重建时保留已有类别，避免 --only 之外的丢失
    if cats and os.path.exists(CACHE):
        old = json.load(open(CACHE, encoding='utf-8'))
        keep = [c for c in old['categories'] if c['folder'] not in cats]
    else:
        keep = []
    t0 = time.time()
    data = build(cats)
    data['categories'] = sorted(data['categories'] + keep,
                                key=lambda c: c['cat'])
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    sz = os.path.getsize(CACHE)
    print('-' * 60)
    print('写入 %s  (%.1f KB, 共 %.1fs)' % (CACHE, sz / 1024, time.time() - t0))
    print('类别 %d 个，符号 %d，封装 %d' % (
        len(data['categories']),
        sum(len(c['sch']) for c in data['categories']),
        sum(len(c['pcb']) for c in data['categories'])))


if __name__ == '__main__':
    main()
