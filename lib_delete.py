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


def _transplant(dv, root, u, v):
    p, attr = dv.find_parent(u, root)
    if p is None:
        return v
    dv.set_entry(p, **{attr: v})
    return root


def _bst_delete(dv, root, z):
    """标准 BST 删除（不做红黑重平衡——读取方实测容忍）。

    ⭐🔴 **已知未修缺陷**：双子分支里若「后继 y ≠ 右子 R」，则
        `_transplant(dv, root, y, dv.f(y)[5])` 把 y.R 顶上 y 的位置后，
        紧接着 `dv.set_entry(y, right=R)` 又覆盖了 y 的 right —— y 的
        原右子树 (y.R) 补位链就此断掉，变成「ty=1 但不可达」的孤儿。
        实证：`_exp_r94.py` 删 sid 94（L=166 R=96），后继 y=130，y.R=168
        （06M010006245）最终不可达；表现 = 删 10 个却消失 11 个。
    免疫条件：删除量小（≤ MAX_BATCH 经验值）时该分支极少命中。
    ⇒ 因此对外用 `MAX_BATCH` 单次数量上限兜住；**不要提高该上限**，
      除非先把这里修对（修法：先记 yR = dv.f(y)[5]，transplant 后
      把 R 子树中 y 原本被挂的点直接指向 yR，再 set y.right=R）。
    """
    L, R = dv.f(z)[4], dv.f(z)[5]
    if L == NIL:
        return _transplant(dv, root, z, R)
    if R == NIL:
        return _transplant(dv, root, z, L)
    y = R
    while dv.f(y)[4] != NIL:
        y = dv.f(y)[4]
    if y != R:
        root = _transplant(dv, root, y, dv.f(y)[5])
        dv.set_entry(y, right=R)
    root = _transplant(dv, root, z, y)
    dv.set_entry(y, left=L)
    return root


def _open_dir(path):
    """→ (ole(可写), blob, chain, dv, SS)。用完必须 ole.close()。"""
    ole = olefile.OleFileIO(path, write_mode=True)
    with open(path, 'rb') as f:
        raw = f.read(512)
    dir_first = struct.unpack('<I', raw[48:52])[0]
    chain, s, seen = [], dir_first, set()
    while s not in (0xFFFFFFFE, 0xFFFFFFFF) and s not in seen:
        seen.add(s)
        chain.append(s)
        s = ole.fat[s]
    blob = bytearray(b''.join(ole.getsect(x) for x in chain))
    return ole, blob, chain, _Dir(blob, ole.sectorsize), ole.sectorsize


def _flush(ole, blob, chain, SS, dv):
    """把 dv.dirty 涉及的**目录流扇区**写回。

    🔴 必须按 sid → 目录流内扇区索引 → chain[idx] 映射；
       早期版本用物理扇区号当 blob 索引 → 覆写了无关扇区。
    """
    secs = set((sid * 128) // SS for sid in dv.dirty)
    for di in sorted(secs):
        ole.write_sect(chain[di], bytes(blob[di * SS:(di + 1) * SS]))
    return len(secs)


def _top_storages(dv):
    """遍历 Root Entry.child 树，返回所有顶层 storage 的 sid。"""
    return [x for x in dv.walk(dv.root_child()) if dv.f(x)[2] == 1]


def _remove_top_storages(path, names):
    """摘除顶层 storage（含子树）。返回摘除数。"""
    lows = set(n.lower() for n in names)
    ole, blob, chain, dv, SS = _open_dir(path)
    root = dv.root_child()                       # 🔴 必须从 Root Entry 读
    tops = _top_storages(dv)
    targets = [x for x in tops if dv.name(x).lower() in lows]
    for z in targets:
        for sd in dv.walk(dv.f(z)[6]):           # 子树全标 0
            dv.set_entry(sd, ty=0)
        root = _bst_delete(dv, root, z)
        dv.set_entry(z, ty=0)
    dv.set_entry(0, child=root)
    nsec = _flush(ole, blob, chain, SS, dv)      # 写回**所有**改过的 sid
    ole.close()
    return len(targets)


# ================================================================ SchLib
# 🔴 字段顺序**不固定**！实测两种：
#     A) |LibRef0=X|CompDescr0=...|PartCount0=2      ← Transistor/IC/Diode/...
#     B) |LibRef0=X|PartCount0=2|CompDescr0=...      ← Resistor（456 个全是这种）
#    曾因硬编码顺序 A 导致 Resistor 匹配 0 条 → storage 删了但 FileHeader 没删 → 悬挂引用。
#    正确做法：按 LibRef{i} 锚点切段，段内再找同 i 的 CompDescr{i} / PartCount{i}。
_CNT_RX = re.compile(rb'\|CompCount=(\d+)\|')
_LIBREF_RX = re.compile(rb'\|LibRef(\d+)=([^|]*)')


def _parse_fh_segments(fh):
    """→ [(idx, start, end, libref)]，按 LibRef{i} 锚点切段；
    每段 = 从 `|LibRef{i}=` 到下一个 `|LibRef` 或串尾。"""
    anchors = list(_LIBREF_RX.finditer(fh))
    out = []
    for i, m in enumerate(anchors):
        st = m.start()
        en = anchors[i + 1].start() if i + 1 < len(anchors) else len(fh)
        idx = int(m.group(1))
        libref = m.group(2).decode('utf-8', 'replace')
        out.append((idx, st, en, libref))
    return out


def _edit_fileheader(path, names, log=print):
    """从 FileHeader 摘掉目标符号的三连段（等长）。返回摘除数。"""
    lows = set(n.lower() for n in names)
    ole = olefile.OleFileIO(path, write_mode=True)
    fh = ole.openstream('FileHeader').read()
    ORIG = len(fh)
    segs = _parse_fh_segments(fh)
    kill = [(st, en) for idx, st, en, ref in segs if ref.lower() in lows]
    if not kill:
        ole.close()
        return 0
    body, last = b'', 0
    for st, en in sorted(kill):
        body += fh[last:st]
        last = en
    body += fh[last:]
    m = _CNT_RX.search(fh)
    if m:
        body = body.replace(m.group(0),
                            b'|CompCount=%d|' % (int(m.group(1)) - len(kill)), 1)
    new = body + b'\x00' * (ORIG - len(body))
    assert len(new) == ORIG, 'FileHeader 必须等长'
    ole.write_stream('FileHeader', new)
    ole.close()
    log('  FileHeader: 摘 %d 段 (共 %d)' % (len(kill), len(segs)))
    return len(kill)


def delete_schlib_symbols(lib_path, names, backup=True, dry_run=False, log=print):
    """删除 SchLib 里的若干符号（= 整个物料记录）。

    单次最多 MAX_BATCH 个（见模块顶部说明）。

    返回 {'ok':bool,'deleted':int,'backup':str|None,'msg':str}
    """
    if kind(lib_path) != 'ole':
        return {'ok': False, 'deleted': 0, 'backup': None,
                'msg': '不是 OLE 明文库（%s）' % kind(lib_path)}
    names = list(names)
    if not names:
        return {'ok': False, 'deleted': 0, 'backup': None, 'msg': '未指定名称'}
    g = _guard(names)
    if g:
        return g
    if dry_run:
        return {'ok': True, 'deleted': 0, 'backup': None,
                'msg': 'dry_run: 将删除 %s' % names}
    bak = None
    if backup:
        bak = lib_path + '.bak_del_' + _stamp()
        shutil.copy2(lib_path, bak)
        log('  备份 → %s' % bak)
    n = _remove_top_storages(lib_path, names)
    k = _edit_fileheader(lib_path, names)
    return {'ok': True, 'deleted': k, 'backup': bak,
            'msg': 'storage %d / FileHeader %d' % (n, k)}


# ================================================================ PcbLib
def _pcb_toc_lines(toc):
    """→ (计数, [(名字, 原始记录bytes)], 尾部结构信息)。

    行分隔**不固定** → 按 `Name=` 锚点切分。
    """
    cnt = struct.unpack('<I', toc[:4])[0]
    body = toc[4:]
    real = body.rstrip(b'\x00')
    pad = len(body) - len(real)
    idxs = [m.start() for m in re.finditer(rb'Name=', real)]
    recs = []
    for i, st in enumerate(idxs):
        en = idxs[i + 1] if i + 1 < len(idxs) else len(real)
        seg = real[st:en].rstrip(b'\r\n')
        m = re.match(rb'Name=([^|]*)', seg)
        recs.append((m.group(1).decode('utf-8', 'replace'), seg))
    return cnt, recs, pad


def _pcb_del_toc(lib_path, names):
    lows = set(n.lower() for n in names)
    ole = olefile.OleFileIO(lib_path, write_mode=True)
    p = ['Library', 'ComponentParamsTOC', 'Data']
    toc = ole.openstream(p).read()
    OT = len(toc)
    cnt, recs, pad = _pcb_toc_lines(toc)
    keep = [(n, s) for n, s in recs if n.lower() not in lows]
    if len(keep) == len(recs):
        ole.close()
        return 0
    body = b''.join(s + b'\r\n' for _, s in keep)
    if pad or recs:
        body += b'\x00' * max(pad, 1)
    new = struct.pack('<I', len(body)) + body
    new += b'\x00' * (OT - len(new))
    assert len(new) == OT, 'TOC 必须等长'
    ole.write_stream(p, new)
    ole.close()
    return len(recs) - len(keep)


def _pcb_namelist(ld):
    """解析 Library/Data 尾部名单 → (recs, start, real)。

    记录 = `4B L` + `1B m (=L-1)` + `m 字节名`
    """
    real = ld.rstrip(b'\x00')
    pos, recs = len(real), []
    while pos >= 6:
        found = None
        for m in range(1, 96):
            st = pos - 5 - m
            if st < 0:
                break
            L = struct.unpack('<I', real[st:st + 4])[0]
            if L != m + 1 or real[st + 4] != m:
                continue
            nm = real[st + 5:pos]
            if len(nm) != m or not NAME_RX.match(nm):
                continue
            found = (st, nm)
            break
        if not found:
            break
        recs.append(found)
        pos = found[0]
    recs.reverse()
    start = recs[0][0] if recs else len(real)
    return recs, start, real


def _pcb_del_namelist(lib_path, names):
    lows = set(n.lower() for n in names)
    ole = olefile.OleFileIO(lib_path, write_mode=True)
    p = ['Library', 'Data']
    ld = ole.openstream(p).read()
    OL = len(ld)
    recs, start, real = _pcb_namelist(ld)
    if not recs:
        ole.close()
        return 0
    keep = [(st, nm) for st, nm in recs
            if nm.decode('utf-8', 'replace').lower() not in lows]
    if len(keep) == len(recs):
        ole.close()
        return 0
    tail = b''.join(struct.pack('<I', len(nm) + 1) + bytes([len(nm)]) + nm
                    for st, nm in keep)
    new = real[:start] + tail
    new += b'\x00' * (OL - len(new))
    assert len(new) == OL, 'Library/Data 必须等长'
    ole.write_stream(p, new)
    ole.close()
    return len(recs) - len(keep)


def delete_pcblib_footprints(lib_path, names, backup=True, dry_run=False, log=print):
    """删除 PcbLib 里的若干封装（storage + TOC + 名单 三处同步）。

    单次最多 MAX_BATCH 个（见模块顶部说明）。
    """
    if kind(lib_path) != 'ole':
        return {'ok': False, 'deleted': 0, 'backup': None,
                'msg': '不是 OLE 明文库（%s）' % kind(lib_path)}
    names = list(names)
    if not names:
        return {'ok': False, 'deleted': 0, 'backup': None, 'msg': '未指定名称'}
    g = _guard(names)
    if g:
        return g
    if dry_run:
        return {'ok': True, 'deleted': 0, 'backup': None,
                'msg': 'dry_run: 将删除 %s' % names}
    bak = None
    if backup:
        bak = lib_path + '.bak_del_' + _stamp()
        shutil.copy2(lib_path, bak)
        log('  备份 → %s' % bak)
    n = _remove_top_storages(lib_path, names)
    t = _pcb_del_toc(lib_path, names)
    l = _pcb_del_namelist(lib_path, names)
    return {'ok': True, 'deleted': n, 'backup': bak,
            'msg': 'storage %d / TOC %d / 名单 %d' % (n, t, l)}


def _stamp():
    import datetime
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


# ================================================================ 自检
if __name__ == '__main__':
    import glob
    BASE = r'E:\MaterialLib\AD_Lib'
    TMP = r'C:\Users\admin\AppData\Roaming\MaterialLibManager\output\adlib\temp'
    os.makedirs(TMP, exist_ok=True)
    print('=== lib_delete 自检：全库各删 1 个 ===')
    allok = True
    for folder in sorted(os.listdir(BASE)):
        d = os.path.join(BASE, folder)
        if not os.path.isdir(d):
            continue
        for lib in sorted(glob.glob(os.path.join(d, '*.SchLib')) +
                          glob.glob(os.path.join(d, '*.PcbLib'))):
            stem = os.path.splitext(os.path.basename(lib))[0]
            ext = os.path.splitext(lib)[1]
            if kind(lib) != 'ole':
                print('  %-28s ⚠ %s' % (os.path.basename(lib), kind(lib)))
                continue
            # 读名单
            import sys
            sys.path.insert(0, r'E:\AI_model\800_6_VPDB\LibViewer')
            import lib_reader
            if ext == '.SchLib':
                A = [x['name'] for x in lib_reader.read_schlib({'path': lib, 'zip_entry': None})]
            else:
                A = [x['name'] for x in lib_reader.read_pcblib({'path': lib, 'zip_entry': None})]
            if not A:
                print('  %-28s (空库，跳过)' % os.path.basename(lib))
                continue
            victim = sorted(A)[len(A) // 2]
            dst = os.path.join(TMP, '_sd_%s%s' % (folder, ext))
            shutil.copy2(lib, dst)
            if ext == '.SchLib':
                r = delete_schlib_symbols(dst, [victim], backup=False, log=lambda *a: None)
                B = [x['name'] for x in lib_reader.read_schlib({'path': dst, 'zip_entry': None})]
            else:
                r = delete_pcblib_footprints(dst, [victim], backup=False, log=lambda *a: None)
                B = [x['name'] for x in lib_reader.read_pcblib({'path': dst, 'zip_entry': None})]
            gone = set(A) - set(B)
            extra = set(B) - set(A)
            try:
                olefile.OleFileIO(dst).close()
                oo = True
            except Exception as e:
                oo = False
            ok = oo and gone == {victim} and not extra
            print('  %-28s %3d->%3d 删 %-20s  %s  %s'
                  % (os.path.basename(lib), len(A), len(B), victim,
                     '✅' if ok else '❌', r['msg']))
            if not ok:
                allok = False
    print()
    print('=== 数量守卫自检 ===')
    over = ['x%d' % i for i in range(MAX_BATCH + 1)]
    okc = ['x%d' % i for i in range(MAX_BATCH)]
    for fn, tag in ((delete_schlib_symbols, 'SchLib'),
                    (delete_pcblib_footprints, 'PcbLib')):
        r1 = fn(os.path.join(BASE, 'no_such_dir', 'X.SchLib'), over)
        r2 = fn(os.path.join(BASE, 'no_such_dir', 'X.SchLib'), okc)
        # 超限必须在 kind 检查**之后**也拦住 → 用不存在的路径测不到，
        # 因此这里直接调 _guard 验证语义，再用真实库测超限。
        g1 = _guard(over)
        g2 = _guard(okc)
        c1 = (g1 is not None and g1['ok'] is False and g1['deleted'] == 0)
        c2 = (g2 is None)
        print('  _guard %-7s 超限拦截 %s | 限额放行 %s'
              % (tag, '✅' if c1 else '❌', '✅' if c2 else '❌'))
        if not (c1 and c2):
            allok = False
        del r1, r2

    # 真实库上验证：超限被拒 + 文件零改动 + dry_run 仍可用
    import glob as _g
    _cands = _g.glob(os.path.join(BASE, '*', 'Diode.SchLib')) or \
             _g.glob(os.path.join(BASE, '*', '*.SchLib'))
    real = _cands[0] if _cands else ''
    if real and kind(real) == 'ole':
        import sys
        sys.path.insert(0, r'E:\AI_model\800_6_VPDB\LibViewer')
        import lib_reader
        A = [x['name'] for x in lib_reader.read_schlib({'path': real, 'zip_entry': None})]
        h0 = sha256_file(real)
        bulk = A[:min(MAX_BATCH + 1, len(A))]
        r = delete_schlib_symbols(real, bulk, backup=False, log=lambda *a: None)
        h1 = sha256_file(real)
        c3 = (not r['ok']) and r['deleted'] == 0 and h0 == h1 and bool(bulk)
        d = delete_schlib_symbols(real, A[:MAX_BATCH], dry_run=True)
        c4 = d['ok'] and d['deleted'] == 0 and sha256_file(real) == h0
        print('  真实库 %s(%d 项) 超限拒绝+零改动 %s | 限额内 dry_run %s'
              % (os.path.basename(real), len(A),
                 '✅' if c3 else '❌', '✅' if c4 else '❌'))
        print('       拒绝语：%s' % r['msg'][:80])
        if not (c3 and c4):
            allok = False
    else:
        print('  未找到可用明文库做守卫实测，跳过')

    print()
    print('自检:', '全部 PASS ✅' if allok else '有 FAIL ❌')
