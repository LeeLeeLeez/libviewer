# -*- coding: utf-8 -*-
"""lib_delete.py —— 离线删除 Altium 类别库里的符号 / 封装（零 AD 依赖）。

设计：
  · SchLib 符号删除 = OLE 顶层 storage 树 BST 摘除 + 子树 entry_type=0
                    + FileHeader 摘三连段（等长 \x00 padding）
  · PcbLib 封装删除 = OLE 顶层 storage 同上
                    + Library/ComponentParamsTOC/Data 删行（4B 计数 + 行）
                    + Library/Data 尾部名单删条目（4B L + 1B m + mB 名）

铁律（实证）：
  🔴 root 必须从 Root Entry.sid_child 读，不能硬编码 1
     （SchLib 常为 1，PcbLib 常为其它 → 硬编码会炸掉整个顶层树）
  🔴 olefile.write_stream 要求等长 → 一律用尾部 \x00 padding
  🔴 TOC 行分隔不是固定 \r\n → 按 `Name=` 锚点切分
  🔴 名单名字可含 ( ) . - 等 → 用可打印 ASCII 判据，别用窄白名单
  🔴 名单与 TOC 必须同步删，否则悬挂名

用法：
    from lib_delete import delete_schlib_symbols, delete_pcblib_footprints
    delete_schlib_symbols(cat_dir, 'Transistor.SchLib', ['06M010000104'], dry_run=True)
"""
import os
import re
import shutil
import struct
import hashlib

import olefile

NIL = 0xFFFFFFFF
_OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
NAME_RX = re.compile(rb'^[\x20-\x7e]+$')          # 可打印 ASCII（含 ( ) . - _ 等）

# 🔴🔴 单次删除上限 —— 不是性能限制，是**正确性**限制。
#     OLE 目录树 = 按 name 排序的红黑树（我们只做 BST 摘除，不做重平衡）。
#     `_bst_delete` 的「双子 + 后继 ≠ 右子」分支有**已知未修缺陷**：
#     后继 y 被搬走补位时，y 原右子树 (y.R) 的补位链会断 → 产生
#     「entry_type=1 但已不可达」的孤儿节点（幽灵节点）。
#     实测：删 1~5 个 = 100% 正确；删 8 个起出现孤儿（删 10 得 11 个消失）。
#     根因定位见 `E:\AI_model\altium_MCP\_exp_r94.py`：
#       删 sid 94 → 后继 y=130，y.R=168(06M010006245) 最终脱离树。
#     ⇒ 用本上限兜住。要大批量删请分批（每批 ≤ MAX_BATCH）。
MAX_BATCH = 6

_TOO_MANY = ('单次最多删 %d 个（本次 %d 个）。大批量会触发 OLE 红黑树摘除的已知缺陷'
             '（产生不可达孤儿节点），请分批执行。')


def _guard(names):
    """数量守卫。→ None 表示放行，否则返回错误 dict。"""
    if len(names) > MAX_BATCH:
        return {'ok': False, 'deleted': 0, 'backup': None,
                'msg': _TOO_MANY % (MAX_BATCH, len(names))}
    return None


# ================================================================ 工具
def kind(path):
    """'ole' / 'dlp' / 'missing'。"""
    if not os.path.exists(path):
        return 'missing'
    with open(path, 'rb') as f:
        h = f.read(8)
    if h == _OLE_MAGIC:
        return 'ole'
    if h.hex().startswith('255453442d486561'):
        return 'dlp'
    return 'other'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ================================================================ OLE 目录树
class _Dir(object):
    """OLE 目录流的可读写视图（按 sid 索引，每项 128 B）。

    🔴 所有 set_entry 都记录到 self.dirty —— 批量删除时 BST 会改**别的节点**的
       父指针（不只是被删节点），漏写回就会让整棵树与磁盘不一致
       （症状：删 20 个后 FileHeader「找不到」，实际是 Root Entry.child 指向了
        一个磁盘上还是旧值的节点）。
    """

    def __init__(self, blob, SS):
        self.b, self.SS, self.n = blob, SS, len(blob) // 128
        self.dirty = set()          # 被改过的 sid

    def f(self, sid):
        o = sid * 128
        return struct.unpack('<64sHBBIII', self.b[o:o + 80])

    def name(self, sid):
        nm, nl = self.f(sid)[0], self.f(sid)[1]
        return nm[:max(0, nl - 2)].decode('utf-16-le', 'replace') if nl >= 2 else ''

    def ty(self, sid):
        return self.b[sid * 128 + 66]

    def set_entry(self, sid, **kw):
        o = sid * 128 + 64
        _, nl, t, c, L, R, C = self.f(sid)
        struct.pack_into('<HBBIII', self.b, o,
                         kw.get('namelen', nl), kw.get('ty', t), kw.get('color', c),
                         kw.get('left', L), kw.get('right', R), kw.get('child', C))
        self.dirty.add(sid)

    def root_child(self):
        return self.f(0)[6]

    def find_parent(self, sid, root):
        if sid == root:
            return (None, None)
        stack, seen = [root], set()
        while stack:
            cur = stack.pop()
            if cur in (NIL,) or cur >= self.n or cur in seen:
                continue
            seen.add(cur)
            L, R, C = self.f(cur)[4], self.f(cur)[5], self.f(cur)[6]
            if L == sid:
                return (cur, 'sid_left')
            if R == sid:
                return (cur, 'sid_right')
            if C == sid:
                return (cur, 'sid_child')
            for x in (L, R, C):
                if x != NIL:
                    stack.append(x)
        return (None, None)

    def walk(self, sid, out=None, seen=None):
        if out is None:
            out, seen = [], set()
        if sid in (NIL,) or sid >= self.n or sid in seen:
            return out
        seen.add(sid)
        out.append(sid)
        for x in self.f(sid)[4:7]:
            self.walk(x, out, seen)
        return out



# ------------------------------------------------------------------
# 🔴 2026-10-22: 删除功能已整体移除（「删除」tab 的显示与功能一并下线）。
#   保留本文件是因为 worker 的 list/sch/pcb 读取模式要用 kind() 判断库形态。
#   真正的删除实现（_transplant / _bst_delete / _remove_top_storages /
#   delete_schlib_symbols / delete_pcblib_footprints 等）已全部删除，
#   即便有人绕过 GUI 直接调用也会立刻报错。
#
#   如需恢复：见同目录 lib_delete.py.bak_20261022_093344_deldrop。

def delete_schlib_symbols(*_a, **_k):
    raise NotImplementedError('删除功能已于 2026-10-22 下线')


def delete_pcblib_footprints(*_a, **_k):
    raise NotImplementedError('删除功能已于 2026-10-22 下线')
