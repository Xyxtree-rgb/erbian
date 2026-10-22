# -*- coding: utf-8 -*-
"""
「手记」手写标题还原（复用知识库标题的同套流程）

源稿：4096x4096 PSD，笔迹 2437x1841，笔画中位粗 43.3px（占字高 2.35%）。
和知识库一样的问题：细笔画降采样后变灰糊。

沿用知识库验证过的方案（不要改用矢量化，源图笔画信噪比不够）：
  保留 alpha 连续灰阶 → gamma 0.55 预增强 → 高斯 0.6 软化台阶
  → 一步 LANCZOS 到 168px（@3x，供 56px 显示），全程只降采样一次
"""
import os
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage
from psd_tools import PSDImage

SRC = r'D:/wx/xwechat_files/wxid_edy9lsaqpyx422_2291/msg/file/2026-10/无标题188_20261022130758.psd'
OUT = r'D:/耳辩H5/erbian_hackathon/assets/icon/text_journal.png'
INK = (23, 23, 23)
OUT_H = 168
GAMMA = 0.55
BLUR = 0.6


def load_alpha():
    p = PSDImage.open(SRC)
    vis = [l for l in p.descendants() if l.visible]
    return np.array(vis[0].composite(force=True).convert('RGBA'))[:, :, 3].astype(np.float32)


def remove_specks(al):
    bw = al > 60
    lab, n = ndimage.label(bw)
    sizes = ndimage.sum(bw, lab, range(1, n + 1))
    drop = np.where(sizes < 120)[0] + 1
    if len(drop):
        al = al.copy()
        al[np.isin(lab, drop)] = 0
    return al, len(drop)


def render(al, out_h, gamma=GAMMA, blur=BLUR):
    ys, xs = np.where(al > 12)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    t = np.clip(al / 255.0, 0, 1) ** gamma
    img = Image.fromarray((t * 255).astype(np.uint8))
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    bh = y1 - y0
    w = int(round((x1 - x0) * out_h / bh))
    return img.crop((x0, y0, x1, y1)).resize((w, out_h), Image.LANCZOS), (x0, y0, x1, y1)


def main():
    al = load_alpha()
    al, dropped = remove_specks(al)
    ys, xs = np.where(al > 128)
    print(f'笔迹 bbox: ({xs.min()},{ys.min()})-({xs.max()},{ys.max()})  '
          f'{xs.max()-xs.min()}x{ys.max()-ys.min()}  剔除噪点 {dropped} 个')

    sh, bbox = render(al, OUT_H)
    out = Image.new('RGBA', sh.size, INK + (0,))
    out.putalpha(sh)
    out.save(OUT, optimize=True)
    print(f'输出 {out.size}  {os.path.getsize(OUT)/1024:.1f} KB')

    a = np.array(sh, dtype=np.float32)
    soft = ((a > 20) & (a < 235)).sum() / max(1, (a > 20).sum())
    g = np.abs(np.diff(a, axis=1))
    s = sh.resize((max(1, int(sh.width * 56 / OUT_H)), 56), Image.LANCZOS)
    sa = np.array(s, dtype=np.float32)
    ink = sa[sa > 25]
    print(f'过渡像素 {soft*100:.1f}%  边缘突变 {(g>100).sum()}  56px实心度 {ink.mean():.1f}')

    # 字形自检
    W = 68
    sw = s.resize((W, int(56 * W / s.width * 0.5)), Image.LANCZOS)
    sa2 = np.array(sw, dtype=np.int32)
    ch = ' .:-=+*#%@'
    for y in range(sa2.shape[0]):
        print(''.join(ch[max(0, min(9, int(sa2[y, x] * 9 / 255)))] for x in range(sa2.shape[1])))


if __name__ == '__main__':
    main()
