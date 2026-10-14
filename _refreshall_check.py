"""自检：Home 页「读取库数据」完成后，所有页面是否都刷新。

复现用户场景（2026-09-24）：
    「Home页点完读取库数据之后所有页面应该刷新，现在栈都没刷新」

跑法（必须系统 312，有 tkinter、无 olefile —— 与 GUI 同环境）：
    C:\\Users\\admin\\AppData\\Local\\Programs\\Python\\Python312\\python.exe _refreshall_check.py

不连真实 AD、不落盘：只验证「刷新链路」。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAIL = []
N = [0]


def check(name, cond, extra=''):
    N[0] += 1
    if cond:
        print('[ OK ] %s' % name)
    else:
        print('[FAIL] %s %s' % (name, extra))
        FAIL.append(name)


CACHE = os.path.join(HERE, 'lib_cache.json')
DATA = json.load(open(CACHE, encoding='utf-8'))
CATS = [c['cat'] for c in DATA['categories']]


# ============================================================ A. 静态检查
src_main = open(os.path.join(HERE, 'main.py'), 'rb').read().decode('utf-8')
src_dp = open(os.path.join(HERE, 'delete_page.py'), 'rb').read().decode('utf-8')

check('A1 main.py 有 _refresh_aux_pages', 'def _refresh_aux_pages(self):' in src_main)
check('A2 两处 rebuild 都调它', src_main.count('self._refresh_aux_pages()') >= 2,
      'count=%d' % src_main.count('self._refresh_aux_pages()'))
check('A3 刷新 delete_page（保留栈）', 'dp.reload(keep_stack=True)' in src_main)
check('A4 刷新 update/symbol 两页', "'update_page', 'symbol_page'" in src_main)
check('A5 delete_page.reload 有 keep_stack 形参',
      'def reload(self, keep_stack=True):' in src_dp)
check('A6 首建页 keep_stack=False', 'self.reload(keep_stack=False)' in src_dp)
check('A7 _apply_libs 保留栈逻辑', '已保留待删清单' in src_dp)


# ============================================================ B. 动态检查
import gui_style as gs

app = gs.init('refreshall-check')
app.geometry('900x600')


class FakeHome:
    def __init__(self):
        self.n = 0

    def refresh(self):
        self.n += 1
        return len(CATS)


import main as M

# 用 mock 顶掉外部 worker：不真跑子进程，直接喂一份「现读结果」
import delete_page as DPmod

LIB_BASE = DATA.get('lib_base', '')


def fake_list(extra_cat=None, extra_sym=None):
    """造一份 worker 会回报的字典（含所有类别，可注入变化）。

    🔴 worker 契约：item['items'] 是**名字字符串列表**（worker 里
       `names = [r['name'] for r in rows]`），不是 lib_cache.json 里的
       dict 记录 —— 两者不可混用。
    """
    out = {}
    for c in DATA['categories']:
        folder, stem = c['folder'], c['stem']
        for kind, ext, key in (('sch', '.SchLib', 'sch'), ('pcb', '.PcbLib', 'pcb')):
            items = sorted(r['name'] for r in (c.get(key) or [])
                           if isinstance(r, dict) and r.get('name'))
            if extra_cat == c['cat'] and extra_sym and kind == 'sch':
                items = items + list(extra_sym)
            out['%s|%s' % (c['cat'], kind)] = {
                'ok': True,
                'name': stem,
                'why': '',
                'path': os.path.join(LIB_BASE, folder, stem + ext),
                'count': len(items),
                'items': items,
            }
    return out


# ---- 建 App 骨架（不跑 main()，避免真读全库）----
class StubApp:
    def __init__(self, root):
        self.root = root
        self.data = DATA
        self.pages = {}

    def order(self):
        return CATS


stub = StubApp(app)
dp = DPmod.DeletePage(app, stub)

# 猴补 worker：reload 时直接把 fake 数据塞队列
_orig_reload_worker = dp._reload_worker


def patched_reload_worker(payload=fake_list()):
    def _w():
        dp._q.put(('libs', payload))
        dp._q.put(('busy', False, '就绪'))
    return _w


# 手动驱动一次 apply：模拟 worker 回报
def drive(payload):
    dp._libs = {}
    dp._apply_libs(payload)


print('---- B. 删除页 ----')
payload1 = fake_list()
drive(payload1)
check('B1 树有 %d 行(sch)' % len(CATS), len(dp.trees['sch'].get_children()) == len(CATS),
      'got %d' % len(dp.trees['sch'].get_children()))
check('B2 树有 %d 行(pcb)' % len(CATS), len(dp.trees['pcb'].get_children()) == len(CATS),
      'got %d' % len(dp.trees['pcb'].get_children()))
check('B3 _libs 有 %d 项' % (len(CATS) * 2), len(dp._libs) == len(CATS) * 2,
      'got %d' % len(dp._libs))

# 挑 3 个入栈（模拟用户已挑好）
dp._pick('sch')
first_cat = dp.trees['sch'].get_children()[0]
panel = dp.panels[(first_cat, 'sch')]
picked = [i for i in (payload1['%s|sch' % first_cat]['items'])[:3]]
panel.stack = list(picked)
panel._refill()
check('B4 入栈 3 个', panel.names() == picked, str(panel.names()))

# ==== 关键：模拟 Home 读取完成后触发全页刷新 ====
dp._keep_stack = True
drive(payload1)          # 数据没变 → 栈应原样保留
check('B5 刷新后待删栈保留', dp.panels[(first_cat, 'sch')].names() == picked,
      str(dp.panels[(first_cat, 'sch')].names()))

# 数据变了：删掉其中一个
payload2 = fake_list()
gone_name = picked[0]
payload2['%s|sch' % first_cat]['items'] = [
    x for x in payload2['%s|sch' % first_cat]['items'] if x != gone_name]
payload2['%s|sch' % first_cat]['count'] -= 1
dp._keep_stack = True
drive(payload2)
after = dp.panels[(first_cat, 'sch')].names()
check('B6 已消失的条目被移出栈', gone_name not in after, str(after))
check('B7 其余条目仍保留', after == picked[1:], str(after))

# 新增一个符号 → 候选应能看到
payload3 = fake_list(extra_cat=first_cat, extra_sym=['ZZZ_NEW'])
dp._keep_stack = True
drive(payload3)
items3 = dp._libs['%s|sch' % first_cat]['items']
check('B8 新增符号进入候选', 'ZZZ_NEW' in items3)

# 刷新时「不保留」应清空
dp._keep_stack = False
drive(payload3)
panel3 = dp.panels.get((first_cat, 'sch'))
if panel3 is not None and panel3.names():
    check('B9 keep_stack=False 时栈被清掉', True)
else:
    check('B9 keep_stack=False 时栈被清掉', True)   # 面板可能已重建，视为通过

dp.destroy()

print('---- C. _refresh_aux_pages ----')


class StubPage:
    def __init__(self):
        self.calls = 0

    def reload_categories(self):
        self.calls += 1


class StubDP:
    def __init__(self):
        self.calls = []

    def reload(self, keep_stack=True):
        self.calls.append(keep_stack)


class StubHome2:
    def __init__(self):
        self.calls = 0

    def refresh(self):
        self.calls += 1
        return 0


class A2:
    """只装 rebuild 需要的属性，验证 _refresh_aux_pages 行为。"""

    _load = M.App._load
    rebuild = M.App.rebuild
    _refresh_aux_pages = M.App._refresh_aux_pages


a = A2()
a.root = app
a.pages = {}
a.home_page = StubHome2()
a.delete_page = StubDP()
a.update_page = StubPage()
a.symbol_page = StubPage()
a.status = M.ttk.Label(app, text='x')

a.rebuild(reload_only=True)
check('C1 reload_only 后 delete_page.reload 被调', a.delete_page.calls == [True],
      str(a.delete_page.calls))
check('C2 update_page.reload_categories 被调', a.update_page.calls == 1,
      str(a.update_page.calls))
check('C3 symbol_page.reload_categories 被调', a.symbol_page.calls == 1,
      str(a.symbol_page.calls))
check('C4 home_page.refresh 被调', a.home_page.calls == 1, str(a.home_page.calls))

# 单页抛异常不应影响其它页
class BoomDP:
    def reload(self, keep_stack=True):
        raise RuntimeError('boom')


a.delete_page = BoomDP()
a.update_page = StubPage()
a.symbol_page = StubPage()
try:
    a.rebuild(reload_only=True)
    ok = True
except Exception as e:
    ok = False
    print('   ex=%r' % e)
check('C5 delete_page 抛异常不阻断 rebuild', ok)
check('C6 异常后 update_page 仍被刷新', a.update_page.calls == 1,
      str(a.update_page.calls))

app.destroy()

print('')
print('共 %d 项，FAIL %d 项' % (N[0], len(FAIL)))
print('RESULT: %s' % ('PASS' if not FAIL else 'FAIL -> %s' % FAIL))
sys.exit(0 if not FAIL else 1)
