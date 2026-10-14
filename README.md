# 元件库查看器 (LibViewer)

按物料类别浏览 Altium 原理图库 / PCB 库的 GUI 工具。

![风格](https://img.shields.io/badge/UI-现代浅色%20tkinter%2Fttk-2563EB)

## 用法

**双击 `启动元件库查看器.cmd`** 即可。

或在命令行：

```
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe main.py
```

## 密文转明文→ 已独立成 **ad_convert**

工具已搬到 **`E:\AI_model\ad_convert`**，与本仓库分开维护。

- 入口：`ad_convert\启动密文转明文.cmd`（双击，支持拖入文件夹）
- 支持：`.SchLib` `.PcbLib` `.SchDoc` `.PcbDoc`
- 详情见 `ad_convert\README.md`

> 🔴 **为什么要单独拿出去：**DLP 按**进程白名单**解密 ——
> `python.exe` 在白名单里能读到明文，而**本仓库打包的 exe 不在白名单**，
> 读到的是原始密文。所以「转明」必须由跑 Python 的脚本来做。
> 本仓库的查看器 exe 若提示「读不了密文快照」，就用 `ad_convert` 转完再回来。

## 上传到 GitHub

**双击 `启动上传GitHub.cmd`** 打开上传工具：
填 Token → 点「开始上传」。首次运行会自动建库。

- 点「**先体检（不联网）**」可先看看哪些文件还是 DLP 密文（密文会被拒绝上传）。
- 勾「**打包 exe + 库文件作为附件**」后，Release 会带上：
  `元件库查看器.exe` + `lib_cache.json` + **16 个 AD 库正本**（约 52 MB）。
- Token 获取：<https://github.com/settings/tokens> → Generate new token (classic) → 勾 `repo`。
- 命令行等价用法见 `gh_upload.py` 的模块说明。

## 发布（Release 内容）

用 `upload_gui.py` 发版本时，Release 会带上：

| 附件 | 说明 |
|---|---|
| `元件库查看器.exe` | 单文件程序（olefile 已打进包，**零外部依赖**）|
| `lib_cache.json` | 库数据快照（各类符号 / 封装名单）|
| **AD 库正本 ×16** | `resistor/Resistor.SchLib` … `Port/Port.PcbLib`（约 **52 MB**）|

🔴 **所有附件上传前都做明文检验**：读到 `%TSD-Header-###%`
（本机 DLP 加密特征）的**直接跳过并在结果里报警**，绝不把密文推到仓库。

密文库的转明办法：在 AD 里**打开该库 → `File | Save`**
（生成明文的 History 快照），再点查看器的「**读取库数据**」，
然后重新上传。也可在上传 GUI 里先点「**先体检（不联网）**」
看看哪些附件还是密文。

## 相关仓库（两个 MCP 服务器）

本工具的「读库」能力可以完全离线跑（Python + olefile），
但在**转明文 / 读 DLP 密文 / 驱动 AD** 等场景下会用到下面两个独立开源的 MCP 服务器：

| MCP | 作用 | 仓库 |
|---|---|---|
| **离线 MCP**<br>`altium-designer-mcp` | 纯文件级读写 `.SchLib` / `.PcbLib`，
**不需要 AD 运行** | <https://github.com/embedded-society/altium-designer-mcp> |
| **桥接 MCP**<br>`eda-agent` | 驱动**正在运行的 Altium Designer**，
可读 DLP 密文、执行 UI 命令 | <https://github.com/salitronic/eda-agent> |

- **离线 MCP**（v1.0.5，GPL-3.0-or-later）：产物是 `altium-designer-mcp.exe`，
  配置在 `%USERPROFILE%\.altium-designer-mcp\config.json`，
  授权库目录写在 `allowed_paths`。
- **桥接 MCP**（`eda-agent`）：stdio 直连，本机用
  `eda-agent --no-dashboard serve` 启动。
  🔴 AD 重启后需重跑轮询：
  `File → Run Script… → Altium_API → Dispatcher.pas → StartMCPServer → Run`。

> 上述两个仓库均为**独立项目**，与本仓库无从属关系；
> 本仓库只是在本地环境中与它们配合使用。

## symbol更新（工具页）

**给已建库的物料条目换原理图符号图形**，而**保留该物料的身份**。

| 来源 | 取自 |
|---|---|
| **图形**（pins / 线条 / 多边形 / 标签 …） | **参考件**（你提供 SchLib 或下载包目录） |
| **名字 · 描述 · 参数 · 封装链** | **目标件**（库里那条既有物料，默认全部保留） |

- **目标 MPN**：**可输入关键词筛选的下拉**。
  - 进来就自动按 MPN 列好（形如 `IMT65R015M2H  (06M010002034)`，括号内是库里的符号名），**按 MPN 排序**
  - **输入即筛选**（大小写不敏感，型号片段 / 料号都能匹配），边打边缩候选并自动展开
  - 无匹配会提示；**清空输入恢复全量**；聚焦即全选，方便选完再搜一次
  - 换类别**自动刷新**；之前选的值若已失效（改过名/换过类别）**自动清空**并提示重选
  - 严格校验：**必须是候选里的项**才能执行 —— 免得停在半截关键词上白跑一次
  - 选中后内部自动还原成真正的符号名
  - 候选来自查看器缓存，所以是**瞬时**填充，不用等、也不用先点按钮
- 参考 SchLib 支持**直接给下载包目录**（自动定位 `.SchLib`）
- 想顺手改参数/描述/封装链？取消对应的「保留」勾选即可开放表单
  （取消后会先「从目标件带出」当前值供你改）
- **PcbLib 全程不动**

### 实现要点

`update_component` **只干一件事 —— 换图形（+ 必要时改描述）**；
参数改动交给 `manage_schlib_parameters`、封装链交给 `manage_schlib_footprints`。

> 为什么拆开：`update_component` 是整体替换，`parameters` 里带 `raw_params`（保真载体），
> 若同时改 `value` / `hidden` 容易和 raw 字段打架。让每个工具只做它最擅长的那一件事，
> 是这里最稳的组合（实测：图形 12 pins → 5 pins，而名字/描述/6 参数/封装链**逐条不变**）。

### 校验（不通过不回写）

图形计数 == 参考件 · 名字未变 · 描述符合预期 · 6 参数逐条对值与可见性 ·
封装链一致 · **符号数不变** · `validate_library` 的 error 数不超基线。

## Schlib更新（工具页）

把「**只更新原理图库、不动 PcbLib**」这套流程做成了一键操作。

### 两种模式

| 模式 | 你要提供的 | 行为 |
|---|---|---|
| **带 symbol** | **源 SchLib 文件地址**（里面已有那颗新符号）+ 符号名 | 把该符号按家规追加进目标类别库；LibRef 保持源符号名 |
| **不带 symbol** | **同族物料符号作参考**（参考库 + 参考符号名）+ **MPN** | 复制参考符号的**图形**，改名为 MPN；参考就在目标库里时走库内复制，否则走跨库复制 |

> 「不带 symbol」是对「同族新料」的固化解法：图形沿用同族（尺寸/引脚一致），只换参数。
> 参考库默认就是目标类别库（切类别时会跟着走，除非你手动改过）。

### 参数由你手动填写

`Comment`（可见）/ `Part Number` / `Specification` / `MPN` / `Manufacturer` / `Footprint` / `Description`
—— 全部手动输入。旁边提供「**从源/参考符号带出**」，把参考件已有的参数填进表单供你改，省去重敲。

### 执行流程与护栏

```
P0 前置检查 → P1 备份正式库 → P2 复制符号 → P3 按家规整改参数
→ P4 链接封装（指向该类别 PcbLib 里已有的封装）→ P5 全项校验 → P6 回写
```

- **先写 temp 副本**：P5 校验不过 ⇒ **绝不回写**，正式库保持原样
- **P1 自动备份**正式库（可关，但不建议）
- **P5 校验全项**：符号数 +1 / 原有符号一个不少 / 6 个参数逐条对值 + 可见性 /
  恰好 1 条封装链且 `library_path` 指向该类别 PcbLib / `validate_library` 的 error 数不得超过基线
- **PcbLib 全程只读**：工具只往封装链里写路径，不触碰 PcbLib 文件
- 执行在**后台线程**，右侧日志实时滚动；可点「中止」（当前步骤结束后停）

### 家规（内置，无需你记）

- 参数集固定 6 项：`Comment` **可见**，其余 5 项**隐藏**
- **`Preferred` 不设**（库内旧符号里的 `P` 属遗留）
- D 类：`LibRef = MPN`、`Part Number = ''`（显式置空）—— 「不带 symbol」模式会把新符号名校验为必须等于 MPN
- 封装链带 `library_path` 绝对路径

### 相关文件

| 文件 | 作用 |
|---|---|
| `schlib_update.py` | 两条流程：`run_update`（新增/建档）与 `run_symbol_update`（换图） |
| `mcp_client.py` | 离线 MCP 的 stdio JSON-RPC 客户端（GUI 不依赖 olefile） |
| `schlib_update_cfg.json` | Schlib更新 tab 的表单状态 |
| `symbol_update_cfg.json` | symbol更新 tab 的表单状态 |
| `smoke_test_update.py` | 两个工具 tab 的自检（66 项，含真实 MCP 只读调用） |
| `_e2e_update.py` | Schlib更新 端到端实测（15 项，不动真库） |
| `_e2e_symupd.py` | symbol更新 端到端实测（17 项，不动真库） |

## 图标

以单词 **「Lib」** 为蓝本的字母标：圆角方形徽章（主色渐变 `#4F8DF7 → #1D4ED8`）+ 白色粗体 **Lib**，
顶部一道渐隐高光。字号按**实测墨迹宽度**反推（占 66%），保证左右留白一致、小尺寸不贴边。

```bash
# 重新生成（默认 A 款）
python make_icon.py A      # 或 B（书脊）/ C（堆叠卡片）
```

| 文件 | 作用 |
|---|---|
| `appicon.ico` | 多尺寸（16/20/24/32/40/48/64/128/256），`iconbitmap` 用 —— **任务栏小图锐利** |
| `appicon_data.py` | **内嵌 base64** 的 PNG + ICO（自动生成，勿手改） |
| `appicon.png` / `icon_preview.png` | 参考图 / 三款对比图（**会被 DLP 加密，仅供人看**） |
| `make_icon.py` | 生成脚本（唯一需要 Pillow 的地方） |

### 🔴 为什么要内嵌而不用 png 文件

本机 **DLP 会加密 `.png`**（实测变 `%TSD-Header`），Tk 不是授权进程 → 读不了，
`iconphoto` 会静默失败。而 **`.py` / `.ico` 不受影响** ⇒ 把字节 base64 内嵌进 `.py`
是唯一 DLP-proof 的载体。运行时：

- **大图**：`PhotoImage(data=<内嵌PNG>)` → `iconphoto`（Alt-Tab / 任务栏缩略图）
- **小图**：内嵌 ICO 落一个临时文件 → `iconbitmap`（任务栏 16×16）

⚠️ 顺序有讲究：**`iconphoto` 会覆盖 `iconbitmap`**，所以 `iconbitmap` 必须放在最后。
⚠️ `PhotoImage` 必须留引用，否则被 GC，图标随即消失。

### 任务栏认不认你，还得靠 AppUserModelID

不调 `SetCurrentProcessExplicitAppUserModelID` 的话，Windows 会把窗口算到 `pythonw.exe` 名下、
显示 Python 图标。程序在**建窗之前**就设好它（`set_app_user_model_id()`）。

### 怎么验证图标真的挂上了

`wm_iconbitmap()` 查询对 .ico 不可靠（返回空字符串，即使设置成功）。
用 Win32 直接读窗口句柄：

```python
import ctypes
hwnd = int(root.frame(), 16)
u = ctypes.windll.user32
for k, lbl in ((1, 'BIG'), (0, 'SMALL'), (2, 'SMALL2')):
    print(lbl, u.SendMessageW(hwnd, 0x7F, k, 0))     # WM_GETICON，非 0 即已挂上
```

`smoke_test.py` 已把这三槽位非空做成断言。

## 界面

- **左侧**：按物料类别分组的竖排图标菜单（仅显示**已建库**的类别）
  - 无源器件：01电阻 · 02电容 · 09电感
  - 半导体：05二极管 · 06晶体管
  - 集成电路：07IC
  - **工具：Schlib更新 · symbol更新**（见下方专节）
- **右侧**：当前类别的两个纵向分栏（sash 可拖拽调高）
  - **原理图库 (SchLib)** —— 显示 `符号名 / 料号 / MPN / 厂商 / 封装 / spec`
  - **PCB库 (PcbLib)** —— 显示 `封装名`
- **筛选：每列独立 + 全表搜索**
  - **每列下方有自己的筛选框**（就在表头正下方，随列宽自动对齐/同步）
  - 全表搜索框（跨所有列 OR）与列筛选**叠加生效**（AND）
  - 列筛选语法：
    | 写法 | 含义 |
    |---|---|
    | `CJ` | 该列含 `CJ`（不区分大小写） |
    | `100V 0.25A` | 该列**同时**含 `100V` 与 `0.25A`（空格 = 且） |
    | `!CJ` | 该列**不含** `CJ`（`!` = 排除） |
    | `!CJ !Yangjie` | 两个词都排除 |
  - **双击任意筛选框** → 弹出该列全部去重取值（带出现次数），点选即应用 —— 适合「厂商/封装」这类低基数列
  - 「清空筛选」一键清掉全部列筛选 + 全表搜索
  - 标题右侧显示 `命中 / 总数` 与当前生效的筛选项数
- **双击数据行** → 弹出完整参数详情（Toplevel 弹窗）
- 模板/占位符号（`U_3` / `Q_5` / `D` 这类无参数通用图形）以灰色显示
- 每行标注数据**来源**：`当前文件` / `History 备份` / `明文备份`，非当前文件的会带 `⚠ 可能非最新`

## 文件说明

| 文件 | 作用 |
|---|---|
| `main.py` | GUI 主程序（**仅依赖 tkinter + 标准库**） |
| `lib_cache.json` | 预解析的库数据（GUI 读它，因此启动即秒开） |
| `build_cache.py` | 重建缓存的脚本（**需要 olefile**，用隔离 venv 跑） |
| `lib_reader.py` | 库解析模块（olefile 读 OLE 结构） |
| `schlib_update.py` | 两条流程：新增建档 + 换图 |
| `mcp_client.py` | 离线 MCP 的 stdio JSON-RPC 客户端 |
| `gui_style.py` | 统一 UI 风格模块（本机所有 GUI 的视觉基准） |
| `gui_sidebar.py` | 左侧竖排图标菜单组件 + 17 个矢量图标 |
| `smoke_test.py` | 查看器无头自检（54 项，含阴性对照） |
| `smoke_test_update.py` | 工具 tab 自检（66 项，含真实 MCP 只读） |
| `_e2e_update.py` / `_e2e_symupd.py` | 两条流程的端到端实测（不动真库） |
| `_run3s.py` | 真 mainloop 跑 3 秒后自关（验证能起窗口） |
| `启动元件库查看器.cmd` | 双击启动器 |

## 两个必需的解释器（架构要点）

GUI 只依赖 `tkinter`，而 `tkinter` 只在**系统 Python 3.12** 里有；
重建缓存需要 `olefile`，而装了 `olefile` 的**隔离 venv 3.13 没有 tkinter**。

因此拆成两半：

| 用途 | 解释器 | 配置位置 |
|---|---|---|
| 跑 GUI | `C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe` | `启动元件库查看器.cmd` |
| 重建缓存 | `C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | `main.py` 顶部 `BUILD_PY` |

界面里的「**重建缓存**」按钮会自动调对那个 venv 解释器，无需手配。

## 数据来源与 DLP 说明

### 库根目录（2026-09-23 起）

库正本已整理到 **`E:\MaterialLib\AD_Lib\<类别>\`**，以后在此维护。
（2026-09-23：上级目录由「物料表」改名为英文 **`MaterialLib`**；原 `E:\project\800-6\PCB_prj\script_test\` 保留为历史归档——History / `.bak_*` / 快照 / CSV 仍在原处。）

路径解析优先级（见 `lib_reader.get_base()`）：

1. 环境变量 `LIBVIEWER_BASE`
2. `libviewer_cfg.json` 的 `lib_base`
3. 代码默认 `BASE_DEFAULT = E:\MaterialLib\AD_Lib`

改路径三选一：改代码常量、写 `libviewer_cfg.json`、或 `set LIBVIEWER_BASE=...` 临时覆盖。
也可在 Python 里调 `lib_reader.set_base(r'...')`（原子写 + 自动备份）。

### 目录布局

```
E:\MaterialLib\AD_Lib\
├── resistor\   Resistor.SchLib   Resistor.PcbLib
├── capacitor\  Capacitor.SchLib  Capacitor.PcbLib
├── diode\      Diode.SchLib      Diode.PcbLib
├── transistor\ Transistor.SchLib Transistor.PcbLib
├── inductor\   Inductor.SchLib   Inductor.PcbLib
├── ic\         IC.SchLib         IC.PcbLib
├── others\     Others.SchLib     Others.PcbLib
├── Port\       Port.SchLib       Port.PcbLib   ← 25端子（结构件/连接器类），目录已建，待放库
└── EPIC.SchLib / EPIC.PcbLib     ← 他人放置的另一套库，与本项目无关，未动
```

### 类别清单（单一真源 = `lib_reader.CATEGORIES`）

| BOM 分类 | 子目录 | 文件主干 | 侧边栏分组 | 图标 |
|---|---|---|---|---|
| 01电阻 | `resistor` | `Resistor` | 基础件 | `equalizer` |
| 02电容 | `capacitor` | `Capacitor` | 基础件 | `battery` |
| 05二极管 | `diode` | `Diode` | 基础件 | `signal` |
| 06晶体管 | `transistor` | `Transistor` | 基础件 | `power` |
| 09电感 | `inductor` | `Inductor` | 基础件 | `ring` |
| 07IC | `ic` | `IC` | 芯片 | `code` |
| 25端子 | `Port` | `Port` | 结构件 | `plug` |
| 10其他 | `others` | `Others` | 其他 | `file` |

> 新增类别只需改 `lib_reader.CATEGORIES`；`main.py` 的 `META`/`ORDER` 只管分组与排序，未列入 `ORDER` 的类别会自动追加到末尾。
> ⚠️ `Port` **大写 P** 是刻意的（与 BOM 分类「25端子」对应），同库其余子目录为小写。

> ⚠️ `download\` 厂商参考包**未**随库搬迁，仍在 `script_test\transistor\download\`（它不属于「库正本」）。

### DLP 与明文来源

**AD 保存落盘的文件是 DLP 密文**（头 `%TSD-Header-%%`），olefile 读不了。
`lib_reader.resolve_sources()` 自动按优先级挑**明文**来源：

1. **当前文件** —— 若本身是明文（未被 AD 重存过）
2. **History 明文 zip** —— AD 自带备份（`History/*.SchLib.Zip`），内容为明文 OLE
3. **`.bak_*` 明文备份** —— 取最新的一个

界面会显示当前用的是哪一种，用备份时标注 `⚠ 可能非最新`。

### 当前各类别来源（2026-09-23 整理后 · 全部为当前文件）

| 类别 | 目录 | 符号 | 封装 | 原理图库来源 | PCB库来源 |
|---|---|---|---|---|---|
| 01电阻 | `resistor\` | 456 | 8 | 当前文件 | 当前文件 |
| 02电容 | `capacitor\` | 163 | 27 | 当前文件 | 当前文件 |
| 05二极管 | `diode\` | 51 | 12 | 当前文件 | 当前文件 |
| 06晶体管 | `transistor\` | 104 | 51 | 当前文件 | 当前文件 |
| 09电感 | `inductor\` | 1 | 1 | 当前文件 | 当前文件 |
| 07IC | `ic\` | 174 | 89 | 当前文件 | 当前文件 |
| 10其他 | `others\` | 9 | 5 | 当前文件 | 当前文件 |
| 25端子 | `Port\` | — | — | 待放库（2026-09-23 目录已建，暂空） | 待放库 |
| **合计** | | **958** | **193** | | |

> 整理时把 5 个「正本为密文」的 PcbLib 换成了**最新的明文版本**（来自 `.bak_*` / `History`），
> 使上述 7 类全部可直接离线读取，不再需要回退。迁移明细见 `_migrate_report.json`。
> **`Port`（25端子）为 2026-09-23 新增的第 8 类**，`AD_Lib\Port\` 目录与 GUI 页签/图标已就位，库文件稍后再放。

## 自检

```bash
# 一键跑全部（推荐；自动带上 olefile 的 PYTHONPATH）
C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe _runtests.py

# 单独跑（需先 export PYTHONPATH=<venv>\Lib\site-packages，系统 Python 无 olefile）
# 查看器自检（63 项，含阴性对照）
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe smoke_test.py

# 两个工具 tab 自检（82 项；--fast 跳过真实 MCP 调用）
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe smoke_test_update.py

# 端到端实测（write_back=False，不动真库）
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe _e2e_update.py
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe _e2e_symupd.py

# 真 mainloop 起窗口
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe _run3s.py

# 风格模块自检（各应为 RESULT: PASS）
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe gui_style.py
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe gui_sidebar.py
```

> 测试里的类别数与页面数均**动态取自 `lib_reader.CATEGORIES`**，新增类别不必改测试。

## 库搬迁工具（一次性，已执行完，留档备查）

| 脚本 | 作用 |
|---|---|
| `_plan_migrate.py` | 只读盘点：确定每类 SchLib/PcbLib 的明文来源 → `_plan_migrate.json` |
| `_do_migrate.py` | 按方案复制到 `AD_Lib\<类别>\`（`--apply` 才真写；默认干跑），含 SHA256 双端校验 |
| `_migrate_report.json` | 搬迁结果报告（每项的来源、SHA、符号/封装数、校验结论） |
| `_runtests.py` | 用系统 Python 跑全部自检（shell 环境异常时的替代入口） |

搬迁安全策略：**绝不覆盖目标已存在的同名文件**（除非 `--force`，且会先备份被覆盖者）。

## 技术要点（改动前必读）

### SchLib 参数记录格式
```
|RECORD=41|IndexInSheet=n|OwnerPartId=1|...|IsHidden=T|Text=<值>|Name=<参数名>|UniqueID=...
```
- ⚠️ **`Text=` 在 `Name=` 之前** —— 但**顺序不固定**！同一文件内两种顺序都可能出现
  （`Diode.SchLib` 里第 1 个符号 `Name` 先行、第 2 个 `Text` 先行）。
  **必须段内分别取 `Text=` 与 `Name=` 再配对，不要假定先后。**
- ⚠️ 一个段里可能同时有 `Text=` 与 `%UTF8%Text=`；**中文以 UTF-8 存储**，解码须优先 `%UTF8%Text=`，再 UTF-8，最后回退 GBK
  （否则 `Coilcraft(线艺)` 会变成 `Coilcraft(绾胯壓)`）
- 每个符号 = 独立 OLE 存储 `<LibReference>/Data`

### 🔴 封装名（Footprint）的权威来源是 `RECORD=45`，不是 `RECORD=41` 参数

这是个**曾导致电阻/电容封装整列空白**的坑：

| 库 | 符号数 | 有 `RECORD=45` | 有 `Name=Footprint` 参数 |
|---|---|---|---|
| 01电阻 | 456 | 456 | **0** |
| 02电容 | 163 | 157 | **0** |
| 05二极管 | 51 | 46 | 44 |
| 06晶体管 | 102 | 90 | 72 |
| 09电感 | 1 | 1 | 1 |
| 07IC | 174 | 159 | 154 |

**电阻库 456/456、电容库 157/163 的符号压根没有 `Footprint` 参数** ——
封装名只存在于 `RECORD=45` 的 `ModelName=` 里：
```
|RECORD=45|ModelName=DIP-D10xL16mm|ModelType=PCBLIB|IsCurrent=T|ModelDatafileEntity0=...|
```
其余库两者都有，实测取值**从无不一致**。

⇒ 解析规则（`lib_reader._pick_footprint`）：
**「当前 + PCBLIB」的模型 → 任一 PCBLIB 模型 → `Footprint` 参数**。

### ⚠️ 7 个符号有料号但未挂封装（数据缺口，非解析问题）

| 库 | 符号 | MPN | 厂商 |
|---|---|---|---|
| 02电容 | `02E108000703` | ERS1VM102G20OT | AISHI(艾华集团) |
| 02电容 | `02E127000200` | B40940A8127M000 | TDK |
| 02电容 | `02E127000413` | VKME1001J121MVTJH9 | Ymin(永铭) |
| 02电容 | `02M205000210` | C3D3L205KB00382 | Faratronic(厦门法拉) |
| 02电容 | `02Y222000912` | SC08B222K302YB | FH(风华) |
| 02电容 | `02Y472000917` | YU7AC472M120D05A7W | Walsin(华新科) |
| 06晶体管 | `06M010000139` | MP87006GMWTH-Z | MPS(芯源) |

这 7 个在 BOM 里都是正常料（有 MPN/厂商/spec），但 SchLib 里**没有 `RECORD=45` 模型链接**。
界面上这 7 行会在「封装」列显示 **`⚠ 未挂封装`**（不再留空，避免被误认为解析失败）。

### PcbLib 结构
- 每个封装 = 顶层 OLE 存储 `<封装名>/Data`
- 必须排除非封装的顶层存储：`FileHeader`、`FileVersionInfo`、`Library`

### 每列筛选框如何与列对齐
筛选行是**放在 Treeview 同一个父容器的上一行**（`inner` 的 grid row 0，tree 在 row 1），
所以左边缘天然对齐。每个筛选框所在的 grid 列 `minsize` = 对应 Treeview 列的 `width`：

```python
self.tree.bind('<Configure>', self._sync_filter_widths)
# 变化时才重设，避免 Configure 递归
w = tuple(self.tree.column(k, 'width') for k in self.keys)
if w != self._last_w:
    self._last_w = w
    for i, ww in enumerate(w):
        self.fbar.columnconfigure(i, minsize=ww)
```

⚠️ 注意 **`_vals()` 同时用于「插入行」与「反查行对象」**：
「未挂封装」的替换值必须走同一个函数，否则双击反查会取不到行（此前踩过）。

## 不在本次范围

BOM 里共 23 个物料类别。**已建库 8 类**：`01电阻 / 02电容 / 05二极管 / 06晶体管 / 09电感 /
07IC / 10其他 / 25端子`（其中 **25端子** 于 2026-09-23 建目录与 GUI 页签，库文件尚未放入）。

其余 15 个（03磁材 / 04压敏 / 08变压器 / 10外壳 / 12开关 / 13PCB /
14保险丝 / 15散热器 / 16塑胶材料 / 17铜柱铜块 / 18螺丝螺母金属垫片 / 19胶 / 22包装材料 /
27标签 / 7阶测试板料号 / 9阶成品料号）**尚未建库**，故不设 tab。
