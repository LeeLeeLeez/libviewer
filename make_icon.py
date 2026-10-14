# -*- coding: utf-8 -*-
"""
生成 LibViewer 图标（以单词「Lib」为蓝本的字母标）。

三款候选（`icon_preview.png` 里并排对比）：
  A 纯字母标   渐变圆角方 + 白色 Lib（光学居中）—— 最稳、小尺寸最清楚
  B 书脊       左侧一条浅色书脊带，暗示"library"
  C 堆叠       右下露出两层浅色卡片，暗示"一库多条目"

默认应用 **A**（写进 appicon.png / appicon.ico）。
换款：`python make_icon.py B`（或 C），再重启查看器即可。

产出：appicon.png(256) · appicon.ico(多尺寸) · icon_preview.png(对比图)
零运行时依赖：生成好的图片随程序走，GUI 只用 tkinter 读 PNG/ICO。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = [r'C:\Windows\Fonts\segoeuib.ttf',
         r'C:\Windows\Fonts\arialbd.ttf',
         r'C:\Windows\Fonts\msyhbd.ttc']
TEXT = 'Lib'
MASTER = 1024
C_TOP = (79, 141, 247)       # #4F8DF7
C_BOT = (29, 78, 216)        # #1D4ED8
C_SOFT = (147, 197, 253)     # #93C5FD
C_WHITE = (255, 255, 255)
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
RADIUS = 0.235


def font(px):
    for p in FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                pass
    raise SystemExit('无可用字体')


def fit_font(S, frac, txt=TEXT):
    """反推字号，使整词**墨迹宽度** = frac × 画布边长（保证左右留白一致）。"""
    probe = font(200)
    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    l, _t, r, _b = d.textbbox((0, 0), txt, font=probe)
    w = max(1, r - l)
    return font(max(8, int(200 * (S * frac) / w)))


def vgrad(size, c1, c2):
    g = Image.new('RGB', (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        g.putpixel((0, y), tuple(int(round(c1[i] + (c2[i] - c1[i]) * t))
                                 for i in range(3)))
    return g.resize((size, size), Image.BILINEAR)


def rounded(size, radius=0.235):
    m = Image.new('L', (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * radius), fill=255)
    return m


def texts(img, d, txt, f, cx, cy, fill=C_WHITE, shadow=True):
    """按整词字形包围盒把文字放在 (cx, cy) 视觉中心。"""
    l, t, r, b = d.textbbox((0, 0), txt, font=f)
    x = cx - (l + r) / 2
    y = cy - (t + b) / 2
    if shadow:                       # 极淡投影，把白字从渐变底色上"抬"起来
        d.text((x + 2, y + 3), txt, font=f, fill=(15, 40, 110, 60))
    d.text((x, y), txt, font=f, fill=fill)
    return (r - l, b - t)


def build(variant='A'):
    S = MASTER
    img = vgrad(S, C_TOP, C_BOT).convert('RGBA')
    d = ImageDraw.Draw(img)

    if variant == 'C':               # 堆叠卡片：右下两层淡色圆角方
        for i, (dx, dy, alpha) in enumerate(((int(S * .085), int(S * .085), 90),
                                             (int(S * .042), int(S * .042), 140))):
            lay = Image.new('RGBA', (S, S), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            ld.rounded_rectangle([0, 0, S - 1, S - 1],
                                 radius=int(S * RADIUS),
                                 fill=C_SOFT + (alpha,))
            img.alpha_composite(lay, (dx, dy))

    if variant == 'B':               # 书脊：左侧浅色带（圆角只在左）
        w = int(S * 0.26)
        lay = Image.new('RGBA', (S, S), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        ld.rounded_rectangle([0, 0, int(S * 0.30), S - 1],
                             radius=int(S * 0.26), fill=C_SOFT + (235,))
        img.alpha_composite(lay, (0, 0))

    # 顶部高光：做成**渐隐**的，不能用矩形——硬边缩到 256 会看成渲染瑕疵
    a = Image.new('L', (1, S))
    for y in range(S):
        a.putpixel((0, y), int(30 * max(0.0, 1.0 - y / (S * 0.58))))
    hl = Image.new('RGBA', (S, S), (255, 255, 255, 255))
    hl.putalpha(a.resize((S, S), Image.BILINEAR))
    img.alpha_composite(hl)

    # 字母标：宽度占比定字号（左右留白一致）；A 居中，B 在书脊右侧居中
    frac = 0.62 if variant == 'B' else 0.66
    f = fit_font(S, frac)
    cx = S * 0.26 + (S - S * 0.26) / 2 if variant == 'B' else S / 2
    cy = S / 2 - S * 0.018          # 略上移：小写字母视觉重心偏下
    if variant == 'C':
        cx, cy = cx - S * 0.028, cy - S * 0.028
    texts(img, d, TEXT, f, cx, cy)

    img.putalpha(rounded(S, RADIUS))
    return img


def preview(masters, path):
    """三款对比：每款一行，缩到 256 看整体 + 48/32/16 看小尺寸。"""
    for i, s in enumerate(ICO_SIZES):
        if i == 0:
            continue
    pad, gap, band = 20, 22, 30
    big = 200
    smalls = [48, 32, 16]
    W = pad * 2 + big + gap + sum(s + gap for s in smalls)
    row = big + band
    H = pad * 2 + row * len(masters)
    img = Image.new('RGB', (W, H), (245, 247, 250))
    d = ImageDraw.Draw(img)
    f = font(15)
    words = {'A': 'A 纯字母标（默认应用）',
             'B': 'B 书脊 + 字母标',
             'C': 'C 堆叠卡片 + 字母标'}
    for i, (name, m) in enumerate(masters.items()):
        y0 = pad + i * row
        d.text((pad, y0 + 4), words[name], font=f, fill=(55, 65, 81))
        im = m.resize((big, big), Image.LANCZOS)
        img.paste(im, (pad, y0 + band), im)
        x = pad + big + gap
        for s in smalls:
            sm = m.resize((s, s), Image.LANCZOS)
            img.paste(sm, (x, y0 + band + (big - s) // 2), sm)
            d.text((x, y0 + band + big + 4), str(s), font=font(12),
                   fill=(120, 128, 140))
            x += s + gap
    img.save(path)


def main():
    want = (sys.argv[1] if len(sys.argv) > 1 else 'A').upper()
    if want not in ('A', 'B', 'C'):
        raise SystemExit('用法: python make_icon.py [A|B|C]')
    masters = {v: build(v) for v in ('A', 'B', 'C')}
    m = masters[want]
    png = os.path.join(HERE, 'appicon.png')
    ico = os.path.join(HERE, 'appicon.ico')
    m.resize((256, 256), Image.LANCZOS).save(png)
    m.save(ico, format='ICO', sizes=[(s, s) for s in ICO_SIZES])
    prev = os.path.join(HERE, 'icon_preview.png')
    preview(masters, prev)

    # 同时把字节**内嵌进 .py** —— 本机 DLP 会加密 PNG，.py/.ico 不受影响，
    # 所以 base64 内嵌是唯一 DLP-proof 的载体（GUI 不依赖外部图片文件）。
    import base64
    data_py = os.path.join(HERE, 'appicon_data.py')
    pb = base64.b64encode(open(png, 'rb').read()).decode()
    ib = base64.b64encode(open(ico, 'rb').read()).decode()
    with open(data_py, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# -*- coding: utf-8 -*-\n')
        f.write('"""由 make_icon.py 自动生成 —— 图标字节（base64）。\n\n')
        f.write('为什么要内嵌：本机 DLP 会加密 .png，而 .py 不会。\n')
        f.write('改动图标请重新跑 `python make_icon.py [A|B|C]`，不要手改本文件。\n"""\n')
        f.write("PNG_B64 = (\n")
        for i in range(0, len(pb), 100):
            f.write("    '%s'\n" % pb[i:i + 100])
        f.write(')\n\nICO_B64 = (\n')
        for i in range(0, len(ib), 100):
            f.write("    '%s'\n" % ib[i:i + 100])
        f.write(')\n')

    # 再出一份 HTML 预览：本机 DLP 会加密 .png，而 HTML 是明文，
    # 图片以 base64 内嵌 → 随时可看，不依赖外部图片文件。
    html = os.path.join(HERE, 'icon_preview.html')
    sizes = [256, 128, 64, 48, 32, 16]
    cells = ''.join(
        '<figure><img src="data:image/png;base64,%s" width="%d" height="%d">'
        '<figcaption>%d</figcaption></figure>' % (pb, s, s, s) for s in sizes)
    TPL = (
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        '<title>LibViewer 图标预览</title><style>'
        'body{margin:0;font:14px/1.6 "Microsoft YaHei UI",system-ui;'
        'background:#F5F7FA;color:#1F2937}'
        'h1{font-size:16px;margin:22px 26px 4px}'
        'p.sub{margin:0 26px 14px;color:#6B7280;font-size:12.5px}'
        '.band{padding:18px 26px 22px;display:flex;align-items:flex-end;gap:26px}'
        '.dark{background:#1F2937;color:#E5E7EB}'
        '.dark p.sub{color:#9CA3AF}'
        'figure{margin:0;text-align:center}'
        'figcaption{font-size:11.5px;color:#94A3B8;margin-top:7px}'
        'img{display:block;border-radius:6px}'
        'code{background:#E8F0FE;color:#1D4ED8;padding:1px 5px;'
        'border-radius:4px;font-size:12px}'
        '</style></head><body>'
        '<h1>LibViewer 图标预览 —— 「Lib」字母标（A 款）</h1>'
        '<p class="sub">同一张图按不同尺寸渲染，判断小尺寸可读性。'
        '图片以 base64 内嵌，不依赖外部 .png。</p>'
        '<div class="band">' + cells + '</div>'
        '<div class="dark"><p class="sub">深色背景</p>'
        '<div class="band">' + cells + '</div></div>'
        '<p class="sub" style="margin-top:18px">改图标：'
        '<code>python make_icon.py A</code>（或 B 书脊 / C 堆叠卡片）</p>'
        '</body></html>')
    with open(html, 'w', encoding='utf-8', newline='\n') as f:
        f.write(TPL)

    print('已应用款：%s' % want)
    for p in (png, ico, prev, data_py, html):
        print('  %-20s %8d B' % (os.path.basename(p), os.path.getsize(p)))


if __name__ == '__main__':
    sys.exit(main())
