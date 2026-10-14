# -*- coding: utf-8 -*-
"""plainize.py —— 把 DLP 密文库「转成明文正本」，供离线路径直读。

## 为什么需要
AD 保存过的库落盘是 DLP 密文（head8 = `255453442d486561` = `%TSD-Header-###%`），
离线 `lib_reader` 读不了，只能走 AD 桥接 → 逐件 1.44 s，961 符号要 ~1384 s。

而 AD 的 `History\\<Stem>.~(N).<ext>.Zip` **内层就是 OLE 明文**。
只要把它解出来、覆盖回正本，正本就变明文了 —— 之后 `lib_reader` 直接读，**0.07 s**。

🔴 **SchLib 和 PcbLib 要分别转**，两类都重要：
   · SchLib 转明 → 符号/参数读得快（961 符号 1384 s → 0.07 s）
   · PcbLib 转明 → **封装列表读得到**。密文 + 无 History 快照时，
     `resolve_sources` 会返回 None，解析出 **0 个封装**（06晶体管 实测）。

## 关键设计：为什么要把明文写进「正本」
`lib_reader.resolve_sources()` 的顺序是
  ① 正本明文 → ② History zip → ③ .bak
②③ 是**快照**，会**静默读到过时内容**（2026-09-24 IC.PcbLib 血案：快照 90 个占位封装
vs 实时 72 个）。把正本转成明文后，① 永远命中，②③ 再也不会被用到 —— 这才是治本。

## 🔴🔴 最大禁忌
**永远不要调 `app_save_all`**。它会无条件重写所有已加载文档（实测 `documents_seen:60
/written:36`，文档没改也照写），落盘经 DLP 驱动 → 明文立刻变回密文。
本模块只做**文件级**操作，全程**不碰 AD**。

## 用法
    import plainize
    rep = plainize.plainize_all(log=print)              # 转明全部（SchLib+PcbLib）
    rep = plainize.plainize_one('ic','IC', log=print)   # 只转 IC 的 SchLib
    rep = plainize.plainize_one('ic','IC', log=print, ext='.PcbLib')  # 只转 PcbLib

CLI：
    python plainize.py                  # 全转
    python plainize.py --only ic diode  # 只转这两类
    python plainize.py --dry-run        # 只看报告不写入

返回报告结构：
    {'checked': n, 'converted': [...], 'already': [...],
     'no_snapshot': [...], 'failed': [...], 'missing': [...],
     'rows': [...], 'elapsed': s}
"""
import os
import sys
import glob
import time
import shutil
import zipfile
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OLE_MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
DLP_MAGIC = bytes.fromhex('255453442d486561')

# 🔴 两类库都要转：SchLib 决定「符号/参数」读得快不快，
#    PcbLib 决定「封装列表」读不读得到（密文 + 无快照 = 解析出 0 个，见 06晶体管）。
EXTS = ('.SchLib', '.PcbLib')

TODAY = time.strftime('%Y%m%d')


# ---------------------------------------------------------------- 零依赖取配置
# 🔴🔴 本模块**不能**在顶层 import lib_reader —— lib_reader 顶层 `import olefile`，
#     而 GUI 跑在系统 Python（有 tkinter、**无 olefile**）；一旦顶层依赖它，
#     整个 GUI 会 ModuleNotFoundError（2026-09-24 实测踩到）。
#     所以这里自带一份「类别 + 根目录」读取，逻辑与 lib_reader 保持一致但零依赖。
BASE_DEFAULT = r'E:\MaterialLib\AD_Lib'
# 🔴 与 lib_reader.CFG_FILE 保持一致：打包后 HERE 会变成 sys._MEIPASS
#    （临时解包目录），而配置必须在 **exe 旁边**。
def _app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


_CFG = os.path.join(_app_dir(), 'libviewer_cfg.json')


def get_base():
    """库根目录：环境变量 > libviewer_cfg.json > 默认。零依赖。"""
    env = os.environ.get('LIBVIEWER_BASE', '').strip()
    if env:
        return env
    try:
        import json
        with open(_CFG, encoding='utf-8') as f:
            v = (json.load(f).get('lib_base') or '').strip()
        if v:
            return v
    except Exception:
        pass
    return BASE_DEFAULT


def get_categories():
    """类别表 (显示名, 目录, 主干名)。零依赖读法：

    🔴 优先从 lib_reader 取（单一真源），但**只在它可导入时**；
       不可导入（无 olefile）则回退到内置副本。
       两处若不一致，以 lib_reader 为准 —— 所以这里改动要同步。
    """
    try:
        import lib_reader as LR
        return list(LR.CATEGORIES)
    except Exception:
        return [
            ('01电阻',   'resistor',   'Resistor'),
            ('02电容',   'capacitor',  'Capacitor'),
            ('05二极管', 'diode',      'Diode'),
            ('06晶体管', 'transistor', 'Transistor'),
            ('09电感',   'inductor',   'Inductor'),
            ('07IC',     'ic',         'IC'),
            ('10其他',   'others',     'Others'),
            ('25端子',   'Port',       'Port'),
        ]


# ------------------------------------------------------------------ 工具
def head8(path):
    try:
        with open(path, 'rb') as f:
            return f.read(8)
    except Exception:
        return b''


def kind(path):
    """'plain' / 'cipher' / 'missing' / 'unknown'"""
    if not os.path.exists(path):
        return 'missing'
    h = head8(path)
    if h == OLE_MAGIC:
        return 'plain'
    if h == DLP_MAGIC:
        return 'cipher'
    return 'unknown'


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _find_snapshots(libdir, stem, ext):
    """找 History 里该库的明文 zip 快照，按 mtime 升序返回 [(mtime, path, entry)]。

    🔴 必须逐份校验**内层是 OLE 明文**才计入 —— 有的快照内层仍是密文。
    🔴 多份快照要选**符号数最全**的，不能只看 mtime（Port 的 ~(1) 只有 1 符号、
       ~(2) 才有 2 个）。所以这里返回全部，由调用方挑。
    """
    hdir = os.path.join(libdir, 'History')
    if not os.path.isdir(hdir):
        return []
    out = []
    for z in glob.glob(os.path.join(hdir, '*.%s.Zip' % ext.lstrip('.'))):
        # 只认本 stem 的快照，别把兄弟库的卷进来
        base = os.path.basename(z)
        if not base.lower().startswith(stem.lower()):
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(ext)]
                for n in names:
                    with zf.open(n) as fh:
                        if fh.read(8) != OLE_MAGIC:
                            continue          # 内层不是明文 → 不计
                    info = zf.getinfo(n)
                    out.append((os.path.getmtime(z), z, n, info.file_size))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def _read_snapshot(zpath, entry):
    with zipfile.ZipFile(zpath) as zf:
        return zf.read(entry)


def _count_symbols(data, ext='.SchLib'):
    """把明文数据临时落地，用 lib_reader 解析条目数（符号 / 封装）。

    🔴 返回 -1 表示解析失败（调用方须视作**不可信**，不能当成 0 处理）。
    """
    import tempfile
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        with open(tmp, 'wb') as f:
            f.write(data)
        # 解析 OLE 必须用 olefile → 只能在装了它的 venv 里跑。
        # 若在 GUI 的系统 Python 下调用，这里会 ImportError → 返回 -1，
        # 调用方会退化为「取最新快照」（仍能转，只是不保证选到最全的那份）。
        import lib_reader as LR
        src = {'path': tmp, 'zip_entry': None}
        if ext.lower().endswith('pcblib'):
            rows = LR.read_pcblib(src)
        else:
            rows = LR.read_schlib(src)
        return len(rows)
    except Exception:
        return -1
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass


# ------------------------------------------------------------------ 核心
def plainize_one(cat, folder, stem, base=None, log=print, dry_run=False,
                 ext='.SchLib'):
    """把单个类别的密文正本转成明文。返回 dict(结果)。

    ext = '.SchLib'（符号）或 '.PcbLib'（封装）。

    流程（严格 7 步，缺一不可）：
      ① 查正本类型；已是明文 → 直接返回 'already'
      ② 找 History 明文快照；没有 → 返回 'no_snapshot'（**不硬跑**，
         提示用户手动在 AD 里 File|Open 该库造快照）
      ③ 多份快照时**选条目数最全**的（并列则取最新）
      ④ 备份密文正本 → <Stem>.<ext>_copy_<YYYYMMDD>（已存在则不覆盖）
      ⑤ 回写明文正本
      ⑥ sha 校验；不符 **自动从备份回滚**
      ⑦ 另存 <Stem>.<ext>_plain_<YYYYMMDD> 副本便于日后还原
    """
    if base is None:
        base = get_base()

    extl = ext.lower()                       # '.schlib' / '.pcblib'
    is_pcb = extl.endswith('pcblib')
    noun = '封装' if is_pcb else '符号'

    libdir = os.path.join(base, folder)
    cur = os.path.join(libdir, '%s%s' % (stem, ext))
    r = {'cat': cat, 'stem': stem, 'path': cur, 'action': 'skip',
         'detail': '', 'symbols': None, 'backup': None, 'ext': ext}

    # ① --------------------------------------------------------------
    k = kind(cur)
    if k == 'missing':
        r['action'] = 'missing'
        r['detail'] = '正本不存在'
        return r
    if k == 'plain':
        r['action'] = 'already'
        r['detail'] = '已是明文'
        return r
    if k != 'cipher':
        r['action'] = 'failed'
        r['detail'] = 'head8=%s 非密文非明文，不认识' % head8(cur).hex()
        return r

    # ② --------------------------------------------------------------
    snaps = _find_snapshots(libdir, stem, extl)
    if not snaps:
        # 🔴 不能一句「无快照」了事：实际分两种情况——
        #   (a) History 里**根本没有**同名 zip  → 还没保存过
        #   (b) **有 zip 但读不了**（DLP 加密 / 内层非明文）
        #       —— 2026-10-14 实测：09-23/24 那批旧快照就是这种，
        #          用户会困惑「我明明有 History」。故此处精确报告原因。
        hdir = os.path.join(libdir, 'History')
        raws = []
        if os.path.isdir(hdir):
            for z in glob.glob(os.path.join(hdir, '*.%s.Zip'
                                            % extl.lstrip('.'))):
                b = os.path.basename(z)
                if not b.lower().startswith(stem.lower()):
                    continue
                try:
                    with zipfile.ZipFile(z) as zf:
                        names = [x for x in zf.namelist()
                                 if x.lower().endswith(extl)]
                        if not names:
                            raws.append((b, 'zip 可读但无 %s 条目' % ext))
                        else:
                            raws.append((b, 'zip 可读但内层不是 OLE 明文'))
                except Exception:
                    h = head8(z)
                    raws.append((b, 'ZIP 读不了（head=%s%s）'
                                 % (h.hex(),
                                    '，疑为 DLP 加密'
                                    if h == DLP_MAGIC else '')))
        r['action'] = 'no_snapshot'
        if raws:
            r['detail'] = (
                'History 里有 %d 份同名快照，但**都不可用**：%s。'
                '→ 请在 AD 里重新打开该库并 **保存**一次，'
                '生成新的明文快照后再点一次'
                % (len(raws), '；'.join('%s（%s）' % x for x in raws[:3])))
        else:
            r['detail'] = (
                'History 内没有该库的快照。'
                '→ 请在 AD 里打开 %s 并 **保存** 一次'
                '（只打开不保存不会产生快照），'
                '再重新点一次' % cur)
        if getattr(sys, 'frozen', False):
            # 🔴 exe 不在 DLP 白名单 → 读到的快照 zip 是加密的，
            #   必须告诉用户改用配套工具。
            r['detail'] += ('（当前是 exe：它不在 DLP 白名单里，'
                            '读不了密文快照；'
                            '请用「启动密文转明文.cmd」'
                            '转完再回来）')
        log('      ! %s：%s' % (stem, r['detail']))
        return r

    # ③ 选条目数最全 ------------------------------------------------
    cands = []
    for mt, zp, entry, sz in snaps:
        try:
            data = _read_snapshot(zp, entry)
        except Exception as e:
            log('      · 快照解析失败 %s：%r' % (os.path.basename(zp), e))
            continue
        n = _count_symbols(data, ext)
        cands.append((n, mt, zp, entry, data))
    if not cands:
        r['action'] = 'failed'
        r['detail'] = '发现 %d 份快照但都无法解析' % len(snaps)
        return r

    # 条目数最大的优先；数量并列或都解析失败(-1) 时退化为取最新
    valid = [c for c in cands if c[0] >= 0]
    if valid:
        pick = max(valid, key=lambda c: (c[0], c[1]))
    else:
        pick = max(cands, key=lambda c: c[1])
        log('      ! 所有快照条目数都解析失败，退化为取最新一份')

    n_sym, mt, zpath, entry, data = pick
    log('      快照 %s → 内层 %s（%d B，%s %s，%s）'
        % (os.path.basename(zpath), entry, len(data),
           n_sym if n_sym >= 0 else '解析失败', noun,
           time.strftime('%m-%d %H:%M', time.localtime(mt))))
    if len(cands) > 1:
        log('      （共 %d 份快照，已选%s最全的）' % (len(cands), noun))
    r['symbols'] = n_sym

    if dry_run:
        r['action'] = 'dry_run'
        r['detail'] = '将转为明文（%d %s）' % (n_sym, noun)
        return r

    # ④ 备份 --------------------------------------------------------
    bak = os.path.join(libdir, '%s%s_copy_%s' % (stem, ext, TODAY))
    if os.path.exists(bak):
        bk = kind(bak)
        if bk == 'cipher':
            log('      备份已存在（密文），沿用 %s' % os.path.basename(bak))
        else:
            log('      ! 备份 %s 已存在但 [%s]，为安全另存带时间戳的副本'
                % (os.path.basename(bak), bk))
            bak = os.path.join(libdir, '%s%s_copy_%s_%s'
                               % (stem, ext, TODAY, time.strftime('%H%M%S')))
    if not os.path.exists(bak):
        # 🔴 字节级写入，不用 shutil.copy —— 防 DLP 驱动在复制途中二次加密
        with open(cur, 'rb') as fi, open(bak, 'wb') as fo:
            fo.write(fi.read())
        log('      备份密文 → %s（%d B）'
            % (os.path.basename(bak), os.path.getsize(bak)))
    if kind(bak) != 'cipher':
        r['action'] = 'failed'
        r['detail'] = '备份校验失败：%s 不是密文，拒绝回写' % os.path.basename(bak)
        return r
    r['backup'] = bak

    # ⑤ 回写 --------------------------------------------------------
    new_sha = hashlib.sha256(data).hexdigest()
    with open(cur, 'wb') as f:
        f.write(data)

    # ⑥ sha 校验 ----------------------------------------------------
    got = sha256_file(cur)
    if got != new_sha:
        log('      !! sha 不符（%s != %s），正在从备份回滚…'
            % (got[:12], new_sha[:12]))
        shutil.copyfile(bak, cur)
        r['action'] = 'failed'
        r['detail'] = 'sha 校验失败，已回滚'
        return r

    # ⑦ 明文副本 ----------------------------------------------------
    plain = os.path.join(libdir, '%s%s_plain_%s' % (stem, ext, TODAY))
    if not os.path.exists(plain):
        with open(plain, 'wb') as f:
            f.write(data)

    r['action'] = 'converted'
    r['detail'] = '%d B 密文 → %d B 明文（%d %s）' % (
        os.path.getsize(bak), os.path.getsize(cur), n_sym, noun)
    log('      ✔ %s' % r['detail'])
    return r


def plainize_all(base=None, log=print, only=None):
    """扫全部类别，把密文 SchLib 转明文。返回汇总报告。"""
    if base is None:
        base = get_base()

    rep = {'base': base, 'checked': 0, 'converted': [], 'already': [],
           'no_snapshot': [], 'failed': [], 'missing': [], 'rows': [],
           'elapsed': 0.0}
    t0 = time.time()
    for cat, folder, stem in get_categories():
        if only and folder not in only:
            continue
        rep['checked'] += 1
        log('  · %s' % cat)
        for ext in EXTS:
            r = plainize_one(cat, folder, stem, base=base, log=log, ext=ext)
            rep['rows'].append(r)
            act = r['action']
            if act == 'converted':
                rep['converted'].append(r)
            elif act == 'already':
                rep['already'].append(r)
            elif act == 'no_snapshot':
                rep['no_snapshot'].append(r)
            elif act == 'missing':
                rep['missing'].append(r)
            elif act == 'failed':
                rep['failed'].append(r)
    rep['elapsed'] = time.time() - t0
    return rep


def _tag(r):
    """报告里给条目加 ext 后缀，便于区分同一个库的 SchLib / PcbLib。"""
    return '%s%s' % (r['cat'], '' if r.get('ext', '.SchLib') == '.SchLib'
                     else '[Pcb]')


def format_report(rep):
    """把报告整理成给人看的几行。

    🔴 对残缺 dict 也要健壮：调用方（GUI / 外部脚本）可能只传部分键，
       不能因为缺 'converted' 之类就 KeyError 炸掉整个 on_fetch。
    """
    if not rep:
        return ['（无报告）']
    g = rep.get if hasattr(rep, 'get') else (lambda k, d=None: rep[k])
    L = []
    L.append('检查 %d 个类别（各含 SchLib + PcbLib），耗时 %.1f s'
             % (g('checked', 0) or 0, g('elapsed', 0.0) or 0.0))
    for key, fmt in (
            ('converted',   '✔ 转明 %d 个：%s'),
            ('already',     '· 已是明文 %d 个'),
            ('no_snapshot', '⚠ 缺快照 %d 个（需在 AD 里 File|Open 打开后再试）：%s'),
            ('missing',     '? 缺失 %d 个：%s')):
        items = g(key, []) or []
        if not items:
            continue
        if '%s' in fmt.split('%d')[-1]:
            L.append(fmt % (len(items), '、'.join(_tag(r) for r in items)))
        else:
            L.append(fmt % len(items))
    failed = g('failed', []) or []
    if failed:
        L.append('✘ 失败 %d 个：%s' % (
            len(failed),
            '；'.join('%s(%s)' % (_tag(r), r.get('detail', '?'))
                      for r in failed if hasattr(r, 'get'))))
    return L


# ------------------------------------------------------------------ CLI
def _main():
    import argparse
    ap = argparse.ArgumentParser(description='把 DLP 密文库转成明文正本')
    ap.add_argument('--only', nargs='*', help='只处理指定 folder（如 ic diode）')
    ap.add_argument('--dry-run', action='store_true', help='只报告不写入')
    a = ap.parse_args()

    print('库根目录：%s' % get_base())
    print('=' * 66)
    if a.dry_run:
        rep = {'base': get_base(), 'checked': 0, 'converted': [],
               'already': [], 'no_snapshot': [], 'failed': [], 'missing': [],
               'rows': [], 'elapsed': 0.0}
        t0 = time.time()
        for cat, folder, stem in get_categories():
            if a.only and folder not in a.only:
                continue
            rep['checked'] += 1
            print('  · %s' % cat)
            for ext in EXTS:
                r = plainize_one(cat, folder, stem, log=print,
                                 dry_run=True, ext=ext)
                rep['rows'].append(r)
                key = {'dry_run': 'converted'}.get(r['action'], r['action'])
                if key in rep:
                    rep[key].append(r)
        rep['elapsed'] = time.time() - t0
    else:
        rep = plainize_all(only=a.only)

    print('-' * 66)
    for line in format_report(rep):
        print(line)
    return 0 if not rep['failed'] else 1


if __name__ == '__main__':
    sys.exit(_main())
