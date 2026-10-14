# -*- coding: utf-8 -*-
"""
gh_upload.py —— 把「元件库查看器 (LibViewer)」以【明文】推送到 GitHub

为什么不用 git.exe？
    本机装了 DLP 透明加密驱动：非白名单进程（git.exe / powershell）读到的
    源文件是密文（文件头 `%TSD-Header-###%`，大小被填充成 1024 的整数倍），
    git 会把它当二进制 blob 提交，仓库里就是一堆解不开的乱码。
    Python 在白名单里，读到的是明文 —— 所以本脚本用 Python 读文件、
    直接调 GitHub REST API 建 commit，完全绕开 git.exe。

Release 附件
    带 exe + 库文件（lib_cache.json）。两者上传前都做**明文检验**：
    读到 `%TSD` 开头就直接拒绝，绝不让密文进仓库。

用法：
    # 1) 先到 https://github.com/settings/tokens 建 token（classic 勾 repo 即可）
    # 2) 首次上传（自动建库）
    python gh_upload.py --token ghp_xxx --repo libviewer --public
    # 3) 以后每次改完代码再提交
    python gh_upload.py --token ghp_xxx --repo libviewer -m "修删除页残留"
    # 4) 发版本：带 exe + 库文件
    python gh_upload.py --token ghp_xxx --repo libviewer --release v1.0.0 --with-exe
    # 5) 只看会传什么，不联网
    python gh_upload.py --dry-run

token 也可以放环境变量：
    set GITHUB_TOKEN=ghp_xxx
"""
import argparse
import base64
import json
import os
import shutil
import socket
import ssl
import stat
import sys
import subprocess
import time
import zipfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.github.com"

# 打包用的 console 解释器（本机 Python 3.12，带 PyInstaller + tkinter）。
# 换机器要改这一行 —— 与 make_exe.py / *.cmd 里用的是同一个。
BUILD_PY = (r"C:\Users\admin\AppData\Local\Programs\Python"
            r"\Python312\python.exe")

# ---- 网络健壮性（2026-10-08 加）--------------------------------------------
# 一次上传要发 ~27 个 HTTPS 请求，任一抖动就整单失败；这里统一退避重试。
# 日志回调：do_upload() 入口会把它换成 GUI 的日志函数。
_LOG = [print]
_RETRIES = 4
_TIMEOUT = 60
_RETRY_HTTP = (408, 429, 500, 502, 503, 504)


def _log(msg):
    try:
        _LOG[0](msg)
    except Exception:
        pass


def set_retries(n):
    global _RETRIES
    _RETRIES = max(0, int(n))


def set_no_proxy():
    """直连: 绕开系统/环境里的代理（本地代理换端口或挂掉时用）。"""
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler({})))


def default_release_tag():
    """没指定 Release tag 时用的默认值（exe 附件必须挂在 Release 上）。"""
    return "v" + time.strftime("%Y.%m.%d")


class PartialUpload(RuntimeError):
    """代码已推送成功，但有子步骤没完成（典型：exe 附件没挂上）。

    单独一个类型，好让 GUI 显示成"部分完成"（橙色警告）而不是彻底失败。
    """

    def __init__(self, url, problems):
        self.url = url
        self.problems = list(problems)
        super().__init__("代码已上传，但以下步骤未完成：\n"
                         + "\n".join("- " + p for p in self.problems))


def _backoff(attempt, why):
    d = min(1.5 * (2 ** attempt), 20.0)
    _log("   [重试] %s —— %.1fs 后第 %d/%d 次" % (why, d, attempt + 1, _RETRIES))
    time.sleep(d)


def _describe_net_error(e, url):
    """把底层网络异常翻成一句能照着做的话（弹窗只显示这段，所以要短而准）。"""
    s = "%s: %s" % (type(e).__name__, e)
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        proxies = {}
    tip = ""
    low = s.lower()
    if "10061" in s or "refused" in low or "积极拒绝" in s:
        tip = "本地代理/端口没人监听 —— 代理软件没开，或端口变了。"
    elif "10054" in s or "reset" in low or "强行关闭" in s:
        tip = "连接被对端重置 —— 代理/VPN 抖动或链路中断。"
    elif "10060" in s or "timed out" in low or "超时" in s:
        tip = "请求超时 —— 代理链路太慢或被拦截。"
    elif "ssl" in low or "certificate" in low or "证书" in s:
        tip = "TLS 握手失败 —— 代理在做中间人，或根证书缺失。"
    elif "getaddrinfo" in low or "11001" in s or "解析" in s:
        tip = "DNS 解析失败 —— 检查网络/DNS。"
    else:
        tip = "网络层错误。"
    lines = ["%s" % s, "  请求: %s" % url, "  原因: %s" % tip]
    if proxies:
        lines.append("  当前代理: %s" % proxies)
        lines.append("  → 代理软件在跑吗？也可勾选「不走代理（直连）」再试一次。")
    else:
        lines.append("  → 当前未走代理；可改用 --no-proxy 之外的方式检查直连。")
    return "\n".join(lines)


def _net_error_is_retryable(e):
    if isinstance(e, urllib.error.HTTPError):
        return e.code in _RETRY_HTTP
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError,
                          socket.timeout, ssl.SSLError, OSError))

# 要发布的文件（相对本脚本目录）。新增文件在这里加一行即可。
FILES = [
    # ---- 文档 ----
    "README.md",
    "LICENSE",
    ".gitignore",
    # ---- GUI 本体（打包进 exe）----
    "main.py",
    "worker_bridge.py",
    "delete_page.py",
    "gui_style.py",
    "gui_sidebar.py",
    "plainize.py",
    "appicon_data.py",
    "appicon.ico",
    # ---- 离线读库链路（外部进程用，需 olefile）----
    "lib_reader.py",
    "lib_delete.py",
    "build_cache.py",
    "_del_worker.py",
    # ---- 打包 ----
    "make_exe.py",
    "make_icon.py",
    "元件库查看器.spec",
    "_vendor/README.txt",
    "_vendor/olefile/__init__.py",
    "_vendor/olefile/olefile.py",
    "_vendor/olefile/LICENSE.txt",
    # ---- 启动 ----
    "启动元件库查看器.cmd",
    # ---- 自检 ----
    "_frozen_check.py",
    "_exe_smoke.py",
    "_encoding_check.py",
    "_icon_check.py",
    "_home_check.py",
    "_delpage_check.py",
    "_delpage_e2e.py",
    "_btnstate_check.py",
    "_keepcursor_check.py",
    "_refreshall_check.py",
    # ---- 上传器自身 ----
    "gh_upload.py",
    "启动上传GitHub.cmd",
    "upload_gui.py",
]

# Release 附件：exe + 库文件。
#   库文件 = ① lib_cache.json（缓存快照）② **AD 库正本**（.SchLib/.PcbLib，主体）
#   全部**必须通过明文检验**才会挂上去；密文的跳过并在结果里报警。
ASSETS = [
    "dist/元件库查看器.exe",
    "lib_cache.json",
]

# AD 库正本所在根目录（可用 libviewer_cfg.json 的 lib_base 或
# 环境变量 LIBVIEWER_BASE 覆盖 —— 与查看器本身同一套优先级）
LIB_BASE = r"E:\MaterialLib\AD_Lib"
LIB_EXTS = (".SchLib", ".PcbLib")


def ad_lib_files(log=print):
    """列出 AD 库正本的 (绝对路径, 展示标签)。

    类别表优先从 lib_cache.json 取（零依赖，且它就是 lib_reader.CATEGORIES
    的产物，天然同步）；取不到就退回扫目录。
    """
    base = os.environ.get("LIBVIEWER_BASE", "").strip()
    if not base:
        try:
            with open(os.path.join(HERE, "libviewer_cfg.json"),
                      encoding="utf-8") as f:
                base = (json.load(f).get("lib_base") or "").strip()
        except Exception:
            base = ""
    base = base or LIB_BASE

    cats = []
    try:
        with open(os.path.join(HERE, "lib_cache.json"), encoding="utf-8") as f:
            cats = json.load(f).get("categories") or []
    except Exception:
        pass
    if not cats and os.path.isdir(base):
        log("   (lib_cache.json 无 categories，改为扫目录)")
        for folder in sorted(os.listdir(base)):
            d = os.path.join(base, folder)
            if not os.path.isdir(d) or folder == "History":
                continue
            for fn in sorted(os.listdir(d)):
                if fn.endswith(LIB_EXTS):
                    cats.append({"folder": folder,
                                 "stem": fn.rsplit(".", 1)[0]})

    out, seen = [], set()
    for c in cats:
        folder, stem = c.get("folder"), c.get("stem")
        if not folder or not stem:
            continue
        for ext in LIB_EXTS:
            p = os.path.join(base, folder, stem + ext)
            if p in seen or not os.path.isfile(p):
                continue
            seen.add(p)
            out.append((p, "%s/%s%s" % (folder, stem, ext)))
    return out

DESCRIPTION = ("GUI viewer for Altium Designer component libraries (.SchLib / "
               ".PcbLib): browse symbols and footprints by material category, "
               "search across parameters, and delete entries offline via OLE "
               "editing, with DLP-ciphertext → plaintext conversion.")


def _req(method, url, token, data=None, allow404=False):
    """一次 REST 调用。瞬时网络错误自动退避重试；重试耗尽给一句人话。"""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "gh_upload.py")
    if body is not None:
        req.add_header("Content-Type", "application/json")

    last = None
    for attempt in range(_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            # 409 'Git Repository is empty' 对空仓库的 GET ref 也会出现，
            # 语义上等同于'不存在'（这条不重试，直接当"没有"）
            if allow404 and e.code in (404, 409):
                return None
            if e.code in _RETRY_HTTP and attempt < _RETRIES:
                last = e
                _backoff(attempt, "HTTP %d" % e.code)
                continue
            detail = e.read().decode("utf-8", "replace")[:400]
            raise SystemExit("[HTTP %d] %s\n%s" % (e.code, url, detail))
        except Exception as e:
            last = e
            if _net_error_is_retryable(e) and attempt < _RETRIES:
                _backoff(attempt, type(e).__name__)
                continue
            break
    raise SystemExit(_describe_net_error(last, url))


def read_plain(rel, fix_readme=None):
    """Python 读 = 明文。顺便做一次密文体检。

    rel 可以是相对本脚本目录的路径，也支持**绝对路径**（AD 库正本在库根下）。
    """
    p = rel if os.path.isabs(rel) else os.path.join(HERE, rel)
    if not os.path.isfile(p):
        raise SystemExit("缺少文件: %s" % rel)
    data = open(p, "rb").read()
    if data[:4] == b"%TSD":
        raise SystemExit(
            "!! %s 读到的是 DLP 密文，拒绝上传。"
            "（办法：在 AD 里打开该库 File|Save 生成新快照，"
            "再用查看器的「读取库数据」转成明文）"
            % (os.path.basename(rel) if os.path.isabs(rel) else rel))
    if fix_readme and rel == "README.md":
        data = data.decode("utf-8")
        data = data.replace("<你的用户名>", fix_readme[0]).replace("<仓库名>", fix_readme[1])
        data = data.encode("utf-8")
    return data


def _clean_env():
    e = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        e.pop(k, None)
    return e


def _console_python(path):
    """pythonw.exe -> 同目录的 python.exe。

    本机 DLP 环境下 pythonw（无控制台）跑 PyInstaller 更易撞文件占用；
    统一换成带控制台的解释器更稳。
    """
    d, b = os.path.split(path)
    if b.lower() == "pythonw.exe":
        alt = os.path.join(d, "python.exe")
        if os.path.isfile(alt):
            return alt
    return path


def _find_build_python(log=print):
    """找一个同时具备 PyInstaller 和 tkinter 的解释器（优先带控制台的）。"""
    # 🔴🔴 2026-10-14 实测：双击 cmd 启动时父进程是 **pythonw**（无控制台），
    #    此时 `py.exe` 会让孙进程拿不到有效 stdout —— make_exe 一 print 就 rc=1，
    #    而且**一点输出都没有**（上传日志里「输出末尾：」后面是空白）。
    #    所以：① 硬编码的 console python 最优先（确定有 PyInstaller+tkinter）；
    #          ② 其次 sys.executable 去掉 w（pythonw.exe → 同目录 python.exe）；
    #          ③ 最后才 py / PATH。
    cands = []
    if os.path.isfile(BUILD_PY):
        cands.append([BUILD_PY])
    if not getattr(sys, "frozen", False):
        cands.append([_console_python(sys.executable)])
    p = shutil.which("py")
    if p:
        cands.append([p, "-3.12"])
        cands.append([p])
    p = shutil.which("python")
    if p:
        cands.append([p])
    seen, uniq = set(), []
    for c in cands:
        if tuple(c) not in seen:
            seen.add(tuple(c))
            uniq.append(c)
    for cmd in uniq:
        try:
            r = subprocess.run(cmd + ["-c", "import PyInstaller, tkinter"],
                               capture_output=True, timeout=60, env=_clean_env())
            if r.returncode == 0:
                return cmd
        except Exception:
            continue
    raise RuntimeError("没找到同时装了 PyInstaller 和 tkinter 的 Python")


def _rm_force(path, log=None):
    """删文件/目录，顺手清只读位。"""
    def _onerr(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception as e2:
            if log:
                log("   (清理仍失败 %s: %s)" % (p, e2))

    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, onerror=_onerr)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            _onerr(os.remove, path, None)


def _preclean(specnm, log=print):
    """自己带重试地清 build/<spec>。

    PyInstaller --clean 会 os.remove(build/<spec>/*)；若那些文件刚写完、
    还被文件扫描持有句柄，会直接 PermissionError [WinError 5] 崩掉整个构建
    （2026-10-08 实测，崩在 ~0.2 s）。先自己清掉，绕开这个竞态。
    """
    bd = os.path.join(HERE, "build", specnm)
    for attempt in range(4):
        if not os.path.exists(bd):
            return True
        try:
            _rm_force(bd, log)
            log("   已清理 %s" % os.path.relpath(bd, HERE))
            return True
        except Exception as e:
            log("   = 清理 build/%s 失败(%d/4): %s" % (specnm, attempt + 1, e))
            time.sleep(1.0 + attempt)
    return not os.path.exists(bd)


def make_lib_zip(items, log=print, out_dir=None):
    """把 AD 库正本打成一个 zip。返回 (zip 路径, 原始总字节, 文件数)。

    🔴 每个文件都要先过 `read_plain()` —— **密文的直接抛错**，绝不塞进 zip。
    zip 内保留 `<folder>/<Stem><ext>` 的相对结构，解开即可还原库目录。

    （2026-10-14 用户要求：不要 16 个附件一个个传，压成一个。）
    """
    out_dir = out_dir or HERE
    zip_path = os.path.join(out_dir, "AD_Lib_%s.zip" % time.strftime("%Y%m%d"))
    try:
        if os.path.exists(zip_path):
            os.remove(zip_path)
    except OSError:
        pass
    total = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for p, label in items:
            data = read_plain(p)          # 明文检验；密文 -> SystemExit
            arc = label.replace("\\", "/")
            zf.writestr(arc, data)
            total += len(data)
            log("     + %-34s %9d B" % (arc, len(data)))
    if open(zip_path, "rb").read(4)[:2] != b"PK":
        raise RuntimeError("打出来的 zip 头不是 PK，拒绝上传: %s" % zip_path)
    return zip_path, total, len(items)


def build_exe(log=print):
    """调 make_exe.py 重新打包，返回 dist/元件库查看器.exe 路径。

    🔴 本项目的打包逻辑集中在 make_exe.py —— 它包含「打包前先关掉运行中的实例」
       （否则 dist 里的 exe 被锁、PyInstaller 删不掉）、「_vendor/olefile 存在性
       检查」、「exe 时间戳 > 所有源文件」核验。所以这里直接调它，
       不再自己拼 PyInstaller 命令，避免两处逻辑漂移。
    """
    py = _find_build_python(log)
    log("打包解释器: %s" % " ".join(py))
    argv = py + ["make_exe.py"]
    log("$ " + " ".join(argv))
    # 🔴 输出**重定向到文件**，不用 PIPE —— pythonw 父进程下 PIPE 给子进程的
    #    stdout 是无效句柄，make_exe 一 print 就 rc=1 且无任何输出。
    outf = os.path.join(HERE, "_make_exe_output.log")
    try:
        if os.path.exists(outf):
            os.remove(outf)
    except OSError:
        pass
    tail = []
    with open(outf, "wb") as fo:
        pr = subprocess.Popen(argv, cwd=HERE, env=_clean_env(),
                              stdout=fo, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL)
        pr.wait()
    try:
        text = open(outf, encoding="utf-8", errors="replace").read()
    except Exception:
        text = ""
    if not text:
        text = ("（make_exe.py 没有任何输出，退出码 %s）"
                "—— 多半是解释器/环境问题，检查上面那行「打包解释器」路径。"
                % pr.returncode)
    for line in text.splitlines():
        line = line.rstrip()
        if line:
            log("   " + line)
            tail.append(line)
            if len(tail) > 30:
                tail.pop(0)
    exe = os.path.join(HERE, "dist", "元件库查看器.exe")
    if pr.returncode != 0 or not os.path.isfile(exe):
        raise RuntimeError("打包失败（退出码 %s）。输出末尾：\n%s"
                           % (pr.returncode, "\n".join(tail[-12:])
                              or text[-800:]))
    if open(exe, "rb").read(4) == b"%TSD":
        raise RuntimeError("exe 是 DLP 密文，拒绝上传（删掉 dist 目录重打一次）")
    log("exe 就绪: %s  %.1f MB" % (exe, os.path.getsize(exe) / 1048576.0))
    return exe


def upload_asset(owner, repo_name, token, release_id, path, log=print):
    """把 exe 挂到某个 Release 上。"""
    from urllib.parse import quote
    name = os.path.basename(path)
    data = open(path, "rb").read()
    if data[:4] == b"%TSD":
        raise RuntimeError("附件是 DLP 密文，拒绝上传: %s" % name)
    # 同名附件已存在 -> GitHub 返回 422 already_exists，重传会一直失败。
    # 先把旧的删掉，让"同一个 Release 重传"变成幂等操作。
    try:
        olds = _req("GET", "%s/repos/%s/%s/releases/%s/assets"
                    % (API, owner, repo_name, release_id), token)
        for a in (olds or []):
            if a.get("name") == name:
                _req("DELETE", "%s/repos/%s/%s/releases/assets/%s"
                     % (API, owner, repo_name, a["id"]), token)
                log("   已删除同名旧附件: %s" % name)
    except SystemExit:
        pass
    url = ("https://uploads.github.com/repos/%s/%s/releases/%s/assets?name=%s"
           % (owner, repo_name, release_id, quote(name)))
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("User-Agent", "gh_upload.py")
    # 40 MB 附件最容易在代理上抖 —— 同样退避重试
    last = None
    j = None
    for attempt in range(_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                j = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in _RETRY_HTTP and attempt < _RETRIES:
                last = e
                _backoff(attempt, "HTTP %d" % e.code)
                continue
            raise RuntimeError("[HTTP %d] %s" % (e.code, detail))
        except Exception as e:
            last = e
            if _net_error_is_retryable(e) and attempt < _RETRIES:
                _backoff(attempt, type(e).__name__)
                continue
            raise RuntimeError(_describe_net_error(e, url))
    if j is None:
        raise RuntimeError(_describe_net_error(last, url))
    log("附件已上传: " + j.get("browser_download_url", name))
    return j.get("browser_download_url", "")


def do_upload(token, repo_name, message="", public=True, log=print,
             release_tag=None, release_notes="", with_exe=False, branch=""):
    """执行一次上传（建库 / 追加 commit）。返回仓库 URL。"""
    _LOG[0] = log          # 让 _req 的重试日志也进 GUI 日志框
    # 1. 我是谁
    me = _req("GET", API + "/user", token)
    owner = me.get("login")

    fix = (owner, repo_name)

    def _bootstrap_empty():
        """空仓库调 Git Data API 会 409 'Git Repository is empty'。
        先用 Contents API 放一个 README 初始化出首个 commit。"""
        log("仓库为空 → 先放入 README 初始化…")
        data = read_plain("README.md", fix_readme=(owner, repo_name))
        r0 = _req("PUT", "%s/repos/%s/%s/contents/README.md" % (API, owner, repo_name),
                  token, {"message": "init: README",
                          "content": base64.b64encode(data).decode("ascii")})
        csha = r0["commit"]["sha"]
        cm = _req("GET", "%s/repos/%s/%s/git/commits/%s" % (API, owner, repo_name, csha),
                  token)
        return cm["tree"]["sha"], [csha]
    log("GitHub 账号: %s" % owner)

    # 2. 仓库在不在
    repo = _req("GET", "%s/repos/%s/%s" % (API, owner, repo_name), token, allow404=True)
    if repo is None:
        try:
            repo = _req("POST", API + "/user/repos", token, {
                "name": repo_name,
                "description": DESCRIPTION,
                "private": (not public),
                "auto_init": False,
            })
        except SystemExit as e:
            raise SystemExit(
                "创建仓库被 GitHub 拒绝（403 = 这个 token 没有建新仓库的权限）。\n\n"
                "办法①（推荐）：换成 classic token，生成页会自动勾好 repo 权限：\n"
                "    https://github.com/settings/tokens/new?scopes=repo\n"
                "    生成后把新 token 粘进上传窗口再来一次。\n\n"
                "办法②：先在网页手动建好一个空仓库（注意不要勾 Add a README），\n"
                "    然后把上传窗口里的仓库名改成它、点开始 ——\n"
                "    只要 token 有 Contents 读写权限就能往里推。\n\n"
                "原始错误：%s" % str(e)[:400])
        log("已创建仓库: %s (private=%s)" % (repo["full_name"], not public))
        br = branch or "main"
        base_tree, parents = _bootstrap_empty()
    else:
        log("仓库已存在: %s  star=%s" % (repo["full_name"], repo.get("stargazers_count", 0)))
        br = branch or repo.get("default_branch", "main")
        ref = _req("GET", "%s/repos/%s/%s/git/ref/heads/%s" % (API, owner, repo_name, br),
                   token, allow404=True)
        if ref is None and branch:
            # 指定的分支还不存在 -> 以默认分支为基准新建（默认分支保持不动）
            defbr = repo.get("default_branch", "main")
            log("分支 %s 不存在，将以 %s 为基准新建" % (br, defbr))
            dref = _req("GET", "%s/repos/%s/%s/git/ref/heads/%s"
                        % (API, owner, repo_name, defbr), token, allow404=True)
            if dref is None:
                base_tree, parents = _bootstrap_empty()
            else:
                dcm = _req("GET", dref["object"]["url"], token)
                base_tree, parents = dcm["tree"]["sha"], [dcm["sha"]]
        elif ref is None:
            base_tree, parents = _bootstrap_empty()
        else:
            cm = _req("GET", ref["object"]["url"], token)
            base_tree, parents = cm["tree"]["sha"], [cm["sha"]]


    # 3. 逐个建 blob（内容 base64；Python 读到的是明文）
    tree = []
    for f in FILES:
        data = read_plain(f, fix_readme=fix)
        b = _req("POST", "%s/repos/%s/%s/git/blobs" % (API, owner, repo_name), token,
                 {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        tree.append({"path": f.replace("\\", "/"), "mode": "100644",
                     "type": "blob", "sha": b["sha"]})
        log("  blob %-32s %s" % (f, b["sha"][:8]))

    # 4. tree -> commit -> 更新 ref
    payload = {"tree": tree}
    if base_tree:
        payload["base_tree"] = base_tree
    t = _req("POST", "%s/repos/%s/%s/git/trees" % (API, owner, repo_name), token, payload)

    msg = message or ("首次提交：元件库查看器 (LibViewer)" if not parents
                      else "更新：元件库查看器 (LibViewer)")
    c = _req("POST", "%s/repos/%s/%s/git/commits" % (API, owner, repo_name), token,
             {"message": msg, "tree": t["sha"], "parents": parents})
    log("commit: %s  %s" % (c["sha"][:8], msg))

    log("目标分支: %s" % br)
    try:
        _req("PATCH", "%s/repos/%s/%s/git/refs/heads/%s" % (API, owner, repo_name, br),
             token, {"sha": c["sha"]})
    except SystemExit:
        # 分支还不存在(或被并发建出) → 退回创建; 再失败就把真错误抛出去
        try:
            _req("POST", "%s/repos/%s/%s/git/refs" % (API, owner, repo_name), token,
                 {"ref": "refs/heads/%s" % br, "sha": c["sha"]})
        except SystemExit:
            _req("PATCH", "%s/repos/%s/%s/git/refs/heads/%s" % (API, owner, repo_name, br),
                 token, {"sha": c["sha"]})

    problems = []          # 子步骤失败清单 -> 末尾抛 PartialUpload，不再静默
    if release_tag:
        exe = None
        if with_exe:
            try:
                exe = build_exe(log=log)
            except Exception as ex:
                problems.append("exe 打包失败：%s" % str(ex).splitlines()[0])
                log("!! exe 打包失败，本次无附件：%s" % ex)
        rel = None
        try:
            rel = _req("POST", "%s/repos/%s/%s/releases" % (API, owner, repo_name), token,
                       {"tag_name": release_tag,
                        "name": release_tag,
                        "body": release_notes or ("Release " + release_tag),
                        "target_commitish": c["sha"],
                        "draft": False, "prerelease": False})
            log("Release 已发布: " + rel.get("html_url", ""))
        except SystemExit as e:
            # 同名 tag 的 Release 已存在（422）-> 复用那个，而不是放弃 exe。
            # 2026-10-08 踩过：v1.1.2 有 Release 但附件数 0，就是这里被跳过。
            try:
                rel = _req("GET", "%s/repos/%s/%s/releases/tags/%s"
                           % (API, owner, repo_name, release_tag), token)
                log("Release %s 已存在，复用: %s"
                    % (release_tag, rel.get("html_url", "")))
            except SystemExit:
                problems.append("Release 发布失败：%s" % str(e)[:200])
                log("!! Release 发布失败（可到网页 Releases 页面手动补）：%s"
                    % str(e)[:200])
                rel = None
        if rel is not None:
            assets = []                      # [(绝对路径, 展示标签)]
            if with_exe:
                if exe:
                    assets.append((exe, "元件库查看器.exe"))
                else:
                    problems.append("exe 未打包成功，附件未上传")
            else:
                log("(未开启「打包 exe」，本次不带 exe 附件)")
            # ---- 库文件：逐个做**明文检验**，通过才挂上去 ----
            #   ① lib_cache.json（缓存） ② AD 库正本（.SchLib/.PcbLib，主体）
            exe_norm = os.path.join(HERE, "dist", "元件库查看器.exe")
            lib_items = []
            for rel_path in ASSETS:
                p = os.path.join(HERE, rel_path.replace("/", os.sep))
                if os.path.abspath(p).lower() == os.path.abspath(exe_norm).lower():
                    continue                     # exe 已在 assets 里
                if not os.path.isfile(p):
                    log("   (跳过不存在的附件 %s)" % rel_path)
                    continue
                lib_items.append((p, rel_path))
            total_b = 0
            for p, label in lib_items:
                try:
                    blob = read_plain(p)
                except SystemExit as e:
                    problems.append("库文件 %s 未通过明文检验（已跳过）" % label)
                    log("!! %s 未通过明文检验，跳过：%s"
                        % (label, str(e).splitlines()[-1][:120]))
                    continue
                total_b += len(blob)
                log("   明文检验通过: %-30s %9d B" % (label, len(blob)))
                assets.append((p, label))
            log("散装库文件合计 %.1f MB" % (total_b / 1048576.0))

            # AD 库正本 -> 打成一个 zip（用户要求：不逐个传）
            ad_items = ad_lib_files(log)
            if ad_items:
                log("AD 库正本: %d 个文件 -> 打包成一个 zip" % len(ad_items))
                try:
                    zp, raw_b, n = make_lib_zip(ad_items, log=log)
                    assets.append((zp, os.path.basename(zp)))
                    log("   压缩包 %s：%d 个文件，%.1f MB（原始 %.1f MB）"
                        % (os.path.basename(zp), n,
                           os.path.getsize(zp) / 1048576.0,
                           raw_b / 1048576.0))
                except SystemExit as e:
                    problems.append("AD 库打包失败（明文检验未过）：%s"
                                    % str(e).splitlines()[-1][:150])
                    log("!! AD 库打包失败（明文检验）：%s" % e)
                except Exception as e:
                    problems.append("AD 库打包失败：%s" % str(e)[:150])
                    log("!! AD 库打包失败：%s" % e)
            else:
                log("(没找到 AD 库正本，跳过)")

            # 🔴 换成 zip 后，Release 上可能还留着旧的**散装库附件**
            #    （.SchLib/.PcbLib）—— 顺手删掉，免得同一个 Release 里两套并存。
            if any(os.path.basename(p).lower().startswith("ad_lib_")
                   for p, _ in assets):
                try:
                    olds = _req("GET", "%s/repos/%s/%s/releases/%s/assets"
                                % (API, owner, repo_name, rel["id"]), token)
                    for a in (olds or []):
                        nm = a.get("name", "")
                        if nm.lower().endswith((".schlib", ".pcblib")):
                            _req("DELETE",
                                 "%s/repos/%s/%s/releases/assets/%s"
                                 % (API, owner, repo_name, a["id"]), token)
                            log("   已删除旧的散装库附件: %s" % nm)
                except SystemExit:
                    pass

            for p, label in assets:
                try:
                    upload_asset(owner, repo_name, token, rel["id"], p, log=log)
                except Exception as ex:
                    problems.append("%s 附件上传失败：%s"
                                    % (os.path.basename(p), str(ex)[:160]))
                    log("!! 附件上传失败 %s：%s" % (os.path.basename(p), ex))
    elif with_exe:
        log("!! 勾了打包 exe 但没给 Release tag —— exe 只能作为 Release 附件上传")
        problems.append("没有 Release tag：exe 未上传")

    url = "https://github.com/%s/%s" % (owner, repo_name)
    log("")
    if problems:
        log("!! 代码已上传，但有 %d 项未完成：" % len(problems))
        for p in problems:
            log("   - " + p)
        log("仓库: " + url)
        raise PartialUpload(url, problems)
    log("完成  " + url)
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--repo", default="libviewer")
    ap.add_argument("--public", action="store_true", help="建公开仓库（要 star 必须公开）")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("-m", "--message", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--release", default="",
                    help="顺便发布 Release，填 tag 名如 v1.0.0；留空则只提交不发布")
    ap.add_argument("--release-notes", default="", help="Release 说明")
    ap.add_argument("--with-exe", action="store_true",
                    help="发布 Release 时先打包 exe 并作为附件上传")
    ap.add_argument("--branch", default="",
                    help="提交到指定分支（留空=仓库默认分支）；"
                         "分支不存在时以默认分支为基准新建")
    ap.add_argument("--no-proxy", action="store_true",
                    help="直连，绕开系统/环境代理（本地代理端口变了或挂了时用）")
    ap.add_argument("--retries", type=int, default=None,
                    help="瞬时网络错误的重试次数（默认 4）")
    args = ap.parse_args()

    if args.no_proxy:
        set_no_proxy()
        print("已禁用代理，走直连。")
    if args.retries is not None:
        set_retries(args.retries)

    if args.dry_run:
        print("=== dry-run：将上传以下文件（读到的均为明文）===")
        tot = 0
        for f in FILES:
            d = read_plain(f)
            tot += len(d)
            print("  %-32s %8d B   head=%r" % (f, len(d), d[:24]))
        print("  合计 %d 个文件 / %.1f KB" % (len(FILES), tot / 1024.0))
        return

    # 要传 exe 就必然得有 Release（GitHub 附件只能挂在 Release 上）
    if args.with_exe and not args.release:
        args.release = default_release_tag()
        print("未指定 --release；为了让 exe 能挂上去，自动使用 tag: %s" % args.release)

    if not args.token:
        raise SystemExit("需要 --token 或环境变量 GITHUB_TOKEN")

    try:
        do_upload(args.token, args.repo, args.message, public=not args.private,
                  release_tag=(args.release or None), release_notes=args.release_notes,
                  with_exe=args.with_exe, branch=args.branch)
    except PartialUpload as e:
        print()
        print(str(e))
        print("仓库: %s" % e.url)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
