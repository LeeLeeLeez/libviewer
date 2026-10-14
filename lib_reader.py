# -*- coding: utf-8 -*-
"""
lib_reader.py —— Altium 类别库读取模块（离线 olefile）

功能：
  · 解析明文来源：当前文件 > History 明文 zip > .bak 明文备份
  · SchLib：逐 <name>/Data 流提取 料号 / MPN / 厂商 / 封装 / spec / Comment
  · PcbLib：顶层 OLE 存储名 = 封装名（排除 FileHeader / FileVersionInfo / Library）

关键事实（实证，勿改）：
  · SchLib 参数记录格式 = `|RECORD=41|IndexInSheet=n|...|IsHidden=T|Text=<值>|Name=<参数名>|`
    ⚠️ **Text 在 Name 之前**，且一个 RECORD=41 段里可能同时有 `Text=` 与 `%UTF8%Text=`。
  · 中文以 UTF-8 存储（`%UTF8%Text=` 标记）；解码策略 = 优先 %UTF8%Text= → UTF-8 → GBK。
  · 每个符号 = 独立 OLE 存储 `<LibReference>/Data`。
  · PcbLib 每个封装 = 顶层存储 `<封装名>/Data`。
"""
import olefile, os, re, glob, zipfile, io, json, sys

# ---------------------------------------------------------------- 库根目录
# 单一真源：E:\MaterialLib\AD_Lib
#   （2026-09-23 迁自 script_test；随后用户把上级「物料表」目录改名为英文 MaterialLib）
#
# 优先级：环境变量 LIBVIEWER_BASE  >  libviewer_cfg.json 的 lib_base  >  BASE_DEFAULT
# 改路径请改这里，或建 libviewer_cfg.json 写 {"lib_base": "..."}，
# 或临时用 set LIBVIEWER_BASE=... 覆盖（不改文件）。
BASE_DEFAULT = r'E:\MaterialLib\AD_Lib'
# 🔴  打包(frozen) 后本模块也进了 exe，__file__ 指向临时解包目录(sys._MEIPASS)。
#    而 libviewer_cfg.json 应放在 **exe 旁边**（用户可改）。
#    所以统一取「app 目录」：冻结=exe 目录，否则=本文件目录。
#    🔴 必须与 plainize._CFG 保持一致 —— 否则 GUI(plainize) 与 worker(lib_reader)
#       读到的库根目录不同（2026-10-14 发现的隐患）。
if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(_APP_DIR, 'libviewer_cfg.json')


def get_base():
    """按优先级解析库根目录，并在不可用时给出提示。"""
    env = os.environ.get('LIBVIEWER_BASE', '').strip()
    if env:
        return env
    try:
        with open(CFG_FILE, encoding='utf-8') as f:
            v = (json.load(f).get('lib_base') or '').strip()
        if v:
            return v
    except Exception:
        pass
    return BASE_DEFAULT


def set_base(path):
    """把库根目录写入 libviewer_cfg.json（原子写 + 备份）。"""
    path = os.path.abspath(path)
    old = None
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE, encoding='utf-8') as f:
                old = json.load(f)
        except Exception:
            old = None
        import shutil as _sh, datetime as _dt
        _sh.copy2(CFG_FILE, CFG_FILE + '.bak_' +
                  _dt.datetime.now().strftime('%Y%m%d_%H%M%S'))
    data = dict(old or {})
    data['lib_base'] = path
    tmp = CFG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CFG_FILE)
    return path


OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

# 类别定义：BOM 分类名, 目录, 文件主干名
#
# 目录布局（2026-09-23 迁到 E:\MaterialLib\AD_Lib 后沿用原样）：
#     <base>\<folder>\<stem>.SchLib  /  <stem>.PcbLib
# 即「每类一个子目录」，与 script_test 时期完全一致。
#
# ⚠️ 目录名大小写按用户实际创建为准（Windows 不区分，但显示要一致）：
#    端子类用户建的目录名是 **Port**（首字母大写），与其它全小写目录不同。
CATEGORIES = [
    ('01电阻',   'resistor',   'Resistor'),
    ('02电容',   'capacitor',  'Capacitor'),
    ('05二极管', 'diode',      'Diode'),
    ('06晶体管', 'transistor', 'Transistor'),
    ('09电感',   'inductor',   'Inductor'),
    ('07IC',     'ic',         'IC'),
    ('10其他',   'others',     'Others'),
    ('25端子',   'Port',       'Port'),
]


def _libdirs(base, folder, stem):
    """返回该类别可能的库目录候选，按优先级：
       ① <base>/<folder>   标准布局（每类一个子目录）
       ② <base>            兼容：文件被平铺在根目录时也能读到

    🔴 2026-09-23 修复：候选 ② 只在「根目录里确实有本类别的库文件」时才加入。
       否则一个**空类别目录**（如刚建的 `Port/`）会一路 fallback 到根目录，
       再被 `resolve_sources` 的 `**/*` 备份递归 glob 命中**别的类别**的 .bak_*
       （实测：空 Port 读出 104 个晶体管的符号、5 个 Others 的封装 —— 静默串类）。
    """
    out = []
    if folder:
        out.append(os.path.join(base, folder))
    # 仅当根目录平铺着本类别的库文件（或其 .bak）时才把根目录当候选
    flat_ok = False
    if os.path.isdir(base):
        for ext in ('.SchLib', '.PcbLib'):
            pat = os.path.join(base, stem + ext + '*')
            if glob.glob(pat):
                flat_ok = True
                break
    if flat_ok:
        out.append(base)
    return out

# PcbLib 里非封装的顶层存储
NON_FOOTPRINT = {'FileHeader', 'FileVersionInfo', 'Library'}

# SchLib 里要采集的参数名 → 输出列
PARAM_COLS = [
    ('Part Number',   '料号'),
    ('MPN',           'MPN'),
    ('Manufacturer',  '厂商'),
    ('Footprint',     '封装'),
    ('Specification', 'spec'),
    ('Comment',       'Comment'),
    ('Description',   'Description'),
    ('Preferred',     'Preferred'),
]

TPL = re.compile(r'^([A-Z]{1,3}(_[0-9]+)?|[A-Z]{2,3})$')


# ------------------------------------------------------------------ 解码
def dec(b):
    """UTF-8 优先，回退 GBK。"""
    if isinstance(b, str):
        return b
    try:
        return b.decode('utf-8')
    except UnicodeDecodeError:
        return b.decode('gbk', 'replace')


# ------------------------------------------------------------------ 源解析
def _is_ole(path):
    try:
        with open(path, 'rb') as f:
            return f.read(8) == OLE_MAGIC
    except Exception:
        return False


def resolve_sources(folder, stem, base=None):
    """返回 dict(sch=..., pcb=...)，每项 None 或
       {'kind','path','zip_entry','size','mtime'}

    解析顺序（每个目录候选都走一遍）：
      ① 正本明文 → ② History 明文 zip → ③ .bak 明文备份
    目录候选见 _libdirs()：旧「每类一目录」布局优先，其次平铺布局。

    🔴🔴 2026-09-24 血案：② / ③ 是**快照**，会读到**过时内容**而不报错。
      IC.PcbLib 当时已删掉 18 个占位封装（实时 72 个），但 History 里的旧快照
      仍写着 90 个 → 缓存静默回退到 history，GUI 显示 90，比真实值多 18。
      所以：**凡是「库刚被改过」的场合，不要信这里的 history/backup 结果**，
      改用 `LibViewer/build_cache_bridge.py`（经 AD 读实时内容，密文也能读）。
      判据：`build_cache.py` 输出里 `pcb:history` / `pcb:-` 就是可疑信号。
    """
    if base is None:
        base = get_base()
    dirs = _libdirs(base, folder, stem)
    out = {}
    for ext, key in (('.SchLib', 'sch'), ('.PcbLib', 'pcb')):
        found = None
        for d in dirs:
            cur = os.path.join(d, stem + ext)
            cur_abs = os.path.abspath(cur)

            # ① 当前文件为明文
            if os.path.exists(cur) and _is_ole(cur):
                found = {'kind': 'current', 'path': cur, 'zip_entry': None,
                         'size': os.path.getsize(cur),
                         'mtime': os.path.getmtime(cur)}
                break

            # ② History 里的明文 zip（AD 自带备份）
            best = None
            for z in glob.glob(os.path.join(d, 'History', '*' + ext + '.Zip')):
                try:
                    with zipfile.ZipFile(z) as zf:
                        names = [n for n in zf.namelist() if n.endswith(ext)]
                        if not names:
                            continue
                        n0 = names[0]
                        info = zf.getinfo(n0)
                        with zf.open(n0) as fh:
                            if fh.read(8) != OLE_MAGIC:
                                continue
                        cand = {'kind': 'history', 'path': z, 'zip_entry': n0,
                                'size': info.file_size,
                                'mtime': os.path.getmtime(z)}
                        if best is None or cand['mtime'] > best['mtime']:
                            best = cand
                except Exception:
                    continue
            if best:
                found = best
                break

            # ③ .bak 明文备份（取最新；允许同名不同目录）
            #    🔴 只在**本类别目录**内查找，且**不递归跨目录** —— 防止空类别
            #       目录 glob 到 `base` 根（或兄弟类别）的备份而串类。
            baks = []
            _seen = set()
            _pats = [os.path.join(d, '*')]
            if os.path.isdir(d):
                for sub in os.listdir(d):
                    sp = os.path.join(d, sub)
                    if os.path.isdir(sp):
                        _pats.append(os.path.join(sp, '*'))
            for pat in _pats:
                for p in glob.glob(pat):
                    if not os.path.isfile(p) or p in _seen:
                        continue
                    _seen.add(p)
                    bn = os.path.basename(p)
                    if ext.lower() not in bn.lower():
                        continue
                    if '.Master.' in bn:
                        continue
                    if os.path.abspath(p) == cur_abs:
                        continue
                    # 备份名必须与**本类别主干**相关（stem 前缀），否则视为无关文件
                    if not bn.lower().startswith(stem.lower()):
                        continue
                    if _is_ole(p):
                        baks.append({'kind': 'backup', 'path': p, 'zip_entry': None,
                                     'size': os.path.getsize(p),
                                     'mtime': os.path.getmtime(p)})
            if baks:
                found = max(baks, key=lambda x: x['mtime'])
                break

        out[key] = found
    return out


def _open_ole(src):
    if src is None:
        return None
    if src.get('zip_entry'):
        with zipfile.ZipFile(src['path']) as zf:
            return olefile.OleFileIO(io.BytesIO(zf.read(src['zip_entry'])))
    return olefile.OleFileIO(src['path'])


# ------------------------------------------------------------------ SchLib
_RX1 = re.compile(rb'RECORD=1\|LibReference=([^\|\r\n\x00]*)')
_RX41 = re.compile(rb'RECORD=41\|([^\x00]*)')
_RX_UTF8 = re.compile(rb'%UTF8%Text=([^\|\x00\r\n]*)')
_RX_TEXT = re.compile(rb'(?<!UTF8%)Text=([^\|\x00\r\n]*)')
_RX_NAME = re.compile(rb'Name=([^\|\x00\r\n]*)')
# RECORD=45 = 模型/实现链接（封装挂在这里，**不是** RECORD=41 参数）
_RX45 = re.compile(rb'\|RECORD=45\|([^\x00]*?)(?=\|RECORD=|\x00)')


def _parse_models(data):
    """解析 RECORD=45 模型链接 → [{'name','type','current'}, ...]

    🔴 封装名的**权威来源**是这里：
       电阻库 456/456、电容库 157/163 的符号**只有 RECORD=45**，没有 `Name=Footprint` 参数。
       其余库（二极管/晶体管/IC/电感）两者都有，实测取值**从无不一致**。
    """
    out = []
    for seg in _RX45.finditer(data):
        s = seg.group(1)
        mn = re.search(rb'ModelName=([^\|\x00]*)', s)
        if not mn:
            continue
        mt = re.search(rb'ModelType=([^\|\x00]*)', s)
        cur = re.search(rb'IsCurrent=([A-Za-z])', s)
        out.append({
            'name': dec(mn.group(1)),
            'type': dec(mt.group(1)) if mt else '',
            'current': bool(cur and cur.group(1) == b'T'),
        })
    return out


def _pick_footprint(models, param_fp=''):
    """选封装名：优先「当前 + PCBLIB」的模型 → 任一 PCBLIB 模型 → Footprint 参数。"""
    if models:
        cur = [m for m in models if m['current'] and m['type'].upper() == 'PCBLIB']
        pcb = [m for m in models if m['type'].upper() == 'PCBLIB']
        for group in (cur, pcb, models):
            if group:
                return group[0]['name'], [m['name'] for m in models]
    return param_fp, [m['name'] for m in models]


def _parse_params(data):
    """解析一个符号 Data 流里的全部 RECORD=41 参数 → {参数名: 值}"""
    params, order = {}, []
    for seg in _RX41.finditer(data):
        s = seg.group(1)
        nm = _RX_NAME.search(s)
        if not nm:
            continue
        name = dec(nm.group(1))
        raw = _RX_UTF8.search(s) or _RX_TEXT.search(s)
        val = dec(raw.group(1)) if raw else ''
        if name not in params:
            params[name] = val
            order.append(name)
    return params


def read_schlib(src):
    ole = _open_ole(src)
    if ole is None:
        return []
    rows = []
    for st in ole.listdir():
        if len(st) != 2 or st[-1] != 'Data':
            continue
        try:
            data = ole.openstream(st).read()
        except Exception:
            continue
        m1 = _RX1.search(data)
        if not m1:
            continue
        libref = dec(m1.group(1))
        params = _parse_params(data)
        rec = {'name': libref, '_raw': params}
        for k, col in PARAM_COLS:
            rec[col] = params.get(k, '')
        # 封装：以 RECORD=45 模型链接为准（电阻/电容库只有这一条路）
        models = _parse_models(data)
        rec['封装'], rec['models'] = _pick_footprint(models, rec['封装'])
        # 分类：
        #   template 模板/占位 = 无料号无 MPN 且名字形如 U_3 / D / Q_5
        #   dclass  D 类物料   = 有 MPN、无料号（**按设计留空，不是缺陷**）
        #   normal  物料条目   = 有料号
        if rec['料号']:
            cls = 'normal'
        elif rec['MPN'] and not TPL.match(libref):
            cls = 'dclass'
        elif not rec['MPN'] and TPL.match(libref):
            cls = 'template'
        else:
            cls = 'other'
        rec['cls'] = cls
        rec['template'] = (cls == 'template')
        rec['multi'] = data.count(b'RECORD=41|')
        rows.append(rec)
    ole.close()
    rows.sort(key=lambda r: r['name'])
    return rows


# ------------------------------------------------------------------ PcbLib
def read_pcblib(src):
    ole = _open_ole(src)
    if ole is None:
        return []
    names = []
    for st in ole.listdir():
        if not st:
            continue
        top = st[0]
        if top in NON_FOOTPRINT:
            continue
        if top not in names:
            names.append(top)
    out = []
    for n in names:
        try:
            if ole.exists([n, 'Data']):
                # 读 Header 里的描述（若有）
                desc = ''
                try:
                    p = ole.openstream([n, 'Parameters']).read()
                    # PcbLib 的字段名是 DESCRIPTION（SchLib 是 Text）。
                    # 先找 DESCRIPTION，找不到再退回 Text（向后兼容）。
                    for _pat in (rb'\|DESCRIPTION=([^\|\x00\r\n]*)',
                                 rb'\|Text=([^\|\x00\r\n]*)'):
                        td = re.search(_pat, p)
                        if td:
                            desc = dec(td.group(1))
                            break
                except Exception:
                    pass
                out.append({'name': n, 'desc': desc})
        except Exception:
            pass
    ole.close()
    out.sort(key=lambda r: r['name'])
    return out


# ------------------------------------------------------------------ 汇总
def load_category(cat, folder, stem, base=None):
    if base is None:
        base = get_base()
    src = resolve_sources(folder, stem, base)
    sch = read_schlib(src['sch']) if src['sch'] else []
    pcb = read_pcblib(src['pcb']) if src['pcb'] else []
    return {
        'cat': cat, 'folder': folder, 'stem': stem,
        'sch_src': src['sch'], 'pcb_src': src['pcb'],
        'sch': sch, 'pcb': pcb,
    }


def src_label(s):
    if s is None:
        return '无明文来源'
    k = s['kind']
    name = os.path.basename(s['path'])
    if k == 'current':
        return '当前文件'
    if k == 'history':
        return 'History备份'
    return '明文备份'


if __name__ == '__main__':
    total_s = total_p = 0
    for cat, folder, stem in CATEGORIES:
        r = load_category(cat, folder, stem)
        ns, np_ = len(r['sch']), len(r['pcb'])
        total_s += ns; total_p += np_
        tpl = sum(1 for x in r['sch'] if x['template'])
        print('%-9s 符号 %4d (模板 %2d)  封装 %3d   sch:%s  pcb:%s' % (
            cat, ns, tpl, np_, src_label(r['sch_src']), src_label(r['pcb_src'])))
    print('-' * 70)
    print('合计 符号 %d  封装 %d' % (total_s, total_p))
    # 抽查带中文的厂商
    ind = load_category('09电感', 'inductor', 'Inductor')
    if ind['sch']:
        print('抽查电感厂商字段:', repr(ind['sch'][0]['厂商']))
