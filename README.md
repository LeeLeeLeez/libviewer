# LibViewer — Altium Component Library Viewer

A lightweight desktop tool for browsing Altium schematic and PCB libraries by
material category. It reads a pre-built cache, so startup is fast and no Altium
installation is required for browsing.

![UI](https://img.shields.io/badge/UI-modern%20light%20tkinter%2Fttk-2563EB)

## Usage

Double-click **`启动元件库查看器.cmd`**, or run:

```
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe main.py
```

## Upload to GitHub

Double-click **`启动上传GitHub.cmd`** to open the uploader, enter your token,
then click **Start Upload**. The repository is created automatically on first
use.

- **Preflight (offline)** checks every local file before anything is sent.
- Check **Package exe + library files as release assets** to attach
  `元件库查看器.exe`, `lib_cache.json`, and an `AD_Lib_<YYYYMMDD>.zip`
  archive of the Altium library files.
- Get a token at <https://github.com/settings/tokens> → Generate new token
  (classic) → enable `repo`.
- See the module docstring of `gh_upload.py` for the command-line equivalent.

Token placeholder in this document: `LeeLeeLeez` / `libviewer`.

The uploader itself (`gh_upload.py`, `upload_gui.py`, and the launcher) is a
local-only tool and is not included in this repository.

## Releases

When publishing with `upload_gui.py`, a release carries:

| Asset | Description |
|---|---|
| `元件库查看器.exe` | Single-file executable (olefile bundled; no external dependencies) |
| `lib_cache.json` | Pre-parsed library snapshot (symbol / footprint lists per category) |
| `AD_Lib_<YYYYMMDD>.zip` | All Altium library masters in one archive; keeps the `<folder>/<Stem>.SchLib|.PcbLib` layout |

Uploading the archive automatically removes older, individually attached
`.SchLib` / `.PcbLib` assets so only one set is kept.

## Related repositories

Browsing works fully offline (Python + olefile). Two independent open-source
MCP servers are used when you need to drive a running Altium Designer or
execute UI commands:

| MCP | Purpose | Repository |
|---|---|---|
| **Offline MCP** `altium-designer-mcp` | File-level read/write of `.SchLib` / `.PcbLib`, no Altium required | <https://github.com/embedded-society/altium-designer-mcp> |
| **Bridge MCP** `eda-agent` | Controls a running Altium Designer, executes UI commands | <https://github.com/salitronic/eda-agent> |

- Offline MCP (v1.0.5, GPL-3.0-or-later): produces `altium-designer-mcp.exe`;
  configuration lives in `%USERPROFILE%\.altium-designer-mcp\config.json`,
  with allowed library directories listed under `allowed_paths`.
- Bridge MCP (`eda-agent`): stdio server, started with
  `eda-agent --no-dashboard serve`. After restarting Altium, restart the
  polling loop via
  `File → Run Script… → Altium_API → Dispatcher.pas → StartMCPServer → Run`.

Both are separate projects. This repository merely interoperates with them
in the local environment.

## Icons

The mark is a lettermark based on the word **Lib**: a rounded-square badge
(gradient `#4F8DF7 → #1D4ED8`) with bold white **Lib** and a soft highlight.
Glyph sizes are derived from measured ink width (66% of the box) so margins
stay balanced at small sizes.

```bash
python make_icon.py A      # or B (spine) / C (stacked cards)
```

| File | Purpose |
|---|---|
| `appicon.ico` | Multi-size icon (16/20/24/32/40/48/64/128/256) used by `iconbitmap` |
| `appicon_data.py` | Auto-generated base64-embedded PNG + ICO (do not edit by hand) |
| `appicon.png` / `icon_preview.png` | Reference images for human review |
| `make_icon.py` | Icon generator; the only place Pillow is needed |

Icons are embedded as bytes in `appicon_data.py` so the application stays
self-contained without external image assets. At runtime:

- Large image: `PhotoImage(data=<embedded PNG>)` → `iconphoto`
  (Alt-Tab / taskbar thumbnail)
- Small image: embedded ICO written to a temporary file → `iconbitmap`
  (16×16 taskbar icon)

Call order matters: **`iconphoto` overrides `iconbitmap`**, so `iconbitmap`
must come last. Keep a reference to `PhotoImage` or it is garbage-collected
and the icon disappears.

Taskbar ownership requires `SetCurrentProcessExplicitAppUserModelID`; without
it Windows groups the window under `pythonw.exe` and shows the Python icon.
It is set before any window is created (`set_app_user_model_id()`).

To verify the icon is actually attached, query the window handle directly —
`wm_iconbitmap()` is unreliable for `.ico`:

```python
import ctypes
hwnd = int(root.frame(), 16)
u = ctypes.windll.user32
for k, lbl in ((1, 'BIG'), (0, 'SMALL'), (2, 'SMALL2')):
    print(lbl, u.SendMessageW(hwnd, 0x7F, k, 0))     # WM_GETICON; non-zero = set
```

`smoke_test.py` asserts that all three slots are non-zero.

## Interface

- **Left**: vertical icon menu grouped by material category (only categories
  with a built library are shown)
  - Passives: resistors, capacitors, inductors
  - Semiconductors: diodes, transistors
  - Integrated circuits: IC
- **Right**: two vertically split panes for the active category (draggable
  sash): schematic symbols (`name / part number / MPN / manufacturer /
  footprint / spec`) and PCB footprints (`footprint name`).
- **Filtering**: every column has its own filter box directly under its
  header, aligned to the column width. The global search box (OR across all
  columns) and per-column filters (AND) combine. Filter syntax:

  | Expression | Meaning |
  |---|---|
  | `CJ` | column contains `CJ` (case-insensitive) |
  | `100V 0.25A` | column contains both `100V` and `0.25A` |
  | `!CJ` | column does not contain `CJ` |
  | `!CJ !Yangjie` | excludes both terms |

  Double-click a filter box to see all distinct values with occurrence
  counts — useful for low-cardinality columns such as manufacturer and
  footprint. A single button clears all filters. The header shows
  `matched / total` and the number of active filters.
- Double-click a row for full parameter details (Toplevel dialog).
- Template/placeholder symbols (e.g. `U_3`, `Q_5`, `D`) are shown in grey.
- Each row labels the data source; rows not using the current master file are
  marked as potentially outdated.

## Files

| File | Purpose |
|---|---|
| `main.py` | GUI application (tkinter + standard library only) |
| `lib_cache.json` | Pre-parsed library data (read by the GUI at startup) |
| `build_cache.py` | Cache rebuild script (needs olefile; run in the isolated venv) |
| `lib_reader.py` | Library parser (reads the OLE container with olefile) |
| `mcp_client.py` | stdio JSON-RPC client for the offline MCP |
| `gui_style.py` | Shared UI style module |
| `gui_sidebar.py` | Vertical icon menu and 17 vector icons |
| `smoke_test.py` | Headless viewer self-test, including negative cases |
| `_run3s.py` | Starts a real mainloop for three seconds and exits |
| `启动元件库查看器.cmd` | Double-click launcher |

## Two Python interpreters

The GUI only needs `tkinter`, which exists in the **system Python 3.12**.
Rebuilding the cache needs `olefile`, installed only in an **isolated
Python 3.13 venv** that has no `tkinter`. The two roles are therefore split:

| Role | Interpreter | Configured in |
|---|---|---|
| GUI | `C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe` | `启动元件库查看器.cmd` |
| Cache rebuild | `C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | `BUILD_PY` at the top of `main.py` |

The **Rebuild Cache** button in the GUI invokes the correct interpreter
automatically.

## Data source

### Library root

Library masters live in `E:\MaterialLib\AD_Lib\<category>\`. Path resolution
priority (see `lib_reader.get_base()`):

1. environment variable `LIBVIEWER_BASE`
2. `libviewer_cfg.json` key `lib_base`
3. built-in default `E:\MaterialLib\AD_Lib`

Override it by editing the config file, setting the environment variable, or
calling `lib_reader.set_base(r'...')` from Python (atomic write with backup).

### Directory layout

```
E:\MaterialLib\AD_Lib\
├── resistor\   Resistor.SchLib   Resistor.PcbLib
├── capacitor\  Capacitor.SchLib  Capacitor.PcbLib
├── diode\      Diode.SchLib      Diode.PcbLib
├── transistor\ Transistor.SchLib Transistor.PcbLib
├── inductor\   Inductor.SchLib   Inductor.PcbLib
├── ic\         IC.SchLib         IC.PcbLib
├── others\     Others.SchLib     Others.PcbLib
└── Port\       Port.SchLib       Port.PcbLib
```

### Categories (single source of truth: `lib_reader.CATEGORIES`)

| BOM category | Directory | File stem | Sidebar group | Icon |
|---|---|---|---|---|
| 01 resistor | `resistor` | `Resistor` | Passives | `equalizer` |
| 02 capacitor | `capacitor` | `Capacitor` | Passives | `battery` |
| 05 diode | `diode` | `Diode` | Passives | `signal` |
| 06 transistor | `transistor` | `Transistor` | Passives | `power` |
| 09 inductor | `inductor` | `Inductor` | Passives | `ring` |
| 07 IC | `ic` | `IC` | ICs | `code` |
| 25 connector | `Port` | `Port` | Mechanical | `plug` |
| 10 other | `others` | `Others` | Other | `file` |

Adding a category only requires a change to `lib_reader.CATEGORIES`;
`META` / `ORDER` in `main.py` only control grouping and ordering.

Current symbol and footprint counts are reported in `lib_cache.json` and in
the viewer header; they are intentionally not hard-coded here.

## Self-test

```bash
# All tests in one go (PYTHONPATH for olefile is set automatically)
C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe _runtests.py

# Viewer self-test (including negative cases)
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe smoke_test.py

# Real mainloop smoke test
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe _run3s.py

# Style modules (each should print RESULT: PASS)
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe gui_style.py
C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe gui_sidebar.py
```

Category counts and page counts in the tests are read dynamically from
`lib_reader.CATEGORIES`, so new categories need no test changes.

## Migration utilities (one-off, kept for reference)

| Script | Purpose |
|---|---|
| `_plan_migrate.py` | Read-only inventory: chooses a source snapshot for each SchLib/PcbLib → `_plan_migrate.json` |
| `_do_migrate.py` | Copies into `AD_Lib\<category>\` (`--apply` to write; dry-run by default), with SHA256 checks on both ends |
| `_migrate_report.json` | Migration result report (source, SHA, symbol/footprint counts, verification) |
| `_runtests.py` | Runs all self-tests with the system Python |

Migration never overwrites an existing target unless `--force` is given, and
even then the overwritten file is backed up first.

## Technical notes

### SchLib parameter records

```
|RECORD=41|IndexInSheet=n|OwnerPartId=1|...|IsHidden=T|Text=<value>|Name=<name>|UniqueID=...
```

- `Text=` may appear **before or after** `Name=` — both orders occur in the
  same file. Always extract both fields within a segment and pair them.
- A segment may carry both `Text=` and `%UTF8%Text=`. Decode with
  `%UTF8%Text=` first, then UTF-8, then GBK as a last resort; otherwise
  non-ASCII characters in manufacturer names are mangled.
- Each symbol is a separate OLE storage `<LibReference>/Data`.

### Footprint names come from `RECORD=45`, not a `Footprint` parameter

This once caused the entire footprint column to be blank for resistors and
capacitors: their symbols have no `Footprint` parameter at all; the name
lives only in `ModelName=` of `RECORD=45`:

```
|RECORD=45|ModelName=DIP-D10xL16mm|ModelType=PCBLIB|IsCurrent=T|ModelDatafileEntity0=...|
```

Resolution rule in `lib_reader._pick_footprint`: current + PCBLIB model →
any PCBLIB model → `Footprint` parameter.

### Symbols with a part number but no footprint

A few symbols are valid BOM items but have no `RECORD=45` model link. Those
rows show `⚠ no footprint` in the footprint column instead of a blank cell,
so they are not mistaken for a parser failure.

### PcbLib structure

- Each footprint is a top-level OLE storage `<footprint name>/Data`.
- Non-footprint storages (`FileHeader`, `FileVersionInfo`, `Library`) must be
  excluded.

### Column filter alignment

The filter row sits in the same parent container as the Treeview, one grid
row above it, so the left edges align naturally. Each filter box's grid
column `minsize` equals the matching Treeview column width:

```python
self.tree.bind('<Configure>', self._sync_filter_widths)
w = tuple(self.tree.column(k, 'width') for k in self.keys)
if w != self._last_w:
    self._last_w = w
    for i, ww in enumerate(w):
        self.fbar.columnconfigure(i, minsize=ww)
```

`_vals()` is used both to insert rows and to look a row object up by value;
the `no footprint` placeholder must go through the same function or the
double-click lookup fails.

## Out of scope

The BOM has 23 material categories. Eight have a built library: resistors,
capacitors, diodes, transistors, inductors, IC, connectors, and other. The
remaining categories (magnetics, varistors, transformers, enclosures,
switches, PCBs, fuses, heatsinks, plastics, standoffs, fasteners, adhesives,
packaging, labels, and test/finished-board part numbers) are not yet in the
library and therefore have no tab.
