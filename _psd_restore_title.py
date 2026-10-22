# -*- coding: utf-8 -*-
"""
「知识库」手写标题 · 位图高质量重采样（v4，最终方案）

放弃矢量化：源图笔迹仅 803px 高、笔画中位粗 21.6px，二值化后轮廓本身
带 1~2px 台阶，矢量化把台阶当形状拟合，要么保留锯齿、要么糊成块，两头不到岸。

正解：**保留 alpha 的连续灰阶，只做一次高质量降采样。**
  1. 用原始 alpha（不二值化），保留笔画边缘的真实过渡
  2. 噪点用 alpha 低阈值剔除，不做形态学膨胀
  3. gamma 预增强补偿细笔画降采样后的变灰
  4. 一步 LANCZOS 到目标尺寸（不做中间尺寸，避免二次损失）
  5. 输出 @3x
"""
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage
from psd_tools import PSDImage

SRC = r'D:/wx/xwechat_files/wxid_edy9lsaqpyx422_2291/msg/file/2026-10/无标题188.psd'
OUT = r'D:/耳辩H5/erbian_hackathon/assets/icon/text_knowledge.png'
INK = (23, 23, 23)
OUT_H = 168


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


def render(al, out_h, gamma, blur):
    ys, xs = np.where(al > 12)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1

    # 先按浮点在原分辨率做 gamma（不缩放，不损失信息）
    t = np.clip(al / 255.0, 0, 1) ** gamma
    img = Image.fromarray((t * 255).astype(np.uint8))

    # 轻微模糊：把 1~2px 的台阶过渡成渐变带
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    # 只缩一次
    bh = y1 - y0
    w = int(round((x1 - x0) * out_h / bh))
    return img.crop((x0, y0, x1, y1)).resize((w, out_h), Image.LANCZOS)


def metrics(shape):
    a = np.array(shape, dtype=np.float32)
    g = np.abs(np.diff(a, axis=1))
    soft = ((a > 20) & (a < 235)).sum() / max(1, (a > 20).sum())
    s = shape.resize((max(1, int(shape.width * 56 / shape.height)), 56), Image.LANCZOS)
    sa = np.array(s, dtype=np.float32)
    ink = sa[sa > 25]
    return (g > 100).sum(), soft, (ink.mean() if ink.size else 0)


def main():
    al = load_alpha()
    al, dropped = remove_specks(al)
    print(f'剔除噪点块 {dropped} 个，笔迹面积 {(al>128).sum()}')

    best = None
    print('gamma blur | 突变 | 过渡% | 56px实心')
    for gamma in [0.55, 0.7, 0.85, 1.0]:
        for blur in [0.0, 0.6, 1.0, 1.5]:
            sh = render(al, OUT_H, gamma, blur)
            j, soft, solid = metrics(sh)
            print(f'{gamma:5.2f} {blur:4.1f} | {j:4d} | {soft*100:5.1f} | {solid:6.1f}')
            key = (j, abs(soft - 0.22), -solid)
            if best is None or key < best[0]:
                best = (key, (gamma, blur), sh, j, soft, solid)

    print()
    print(f'最优 gamma={best[1][0]} blur={best[1][1]} | 突变 {best[3]} | 过渡 {best[4]*100:.1f}% | 实心 {best[5]:.1f}')
    sh = best[2]
    out = Image.new('RGBA', sh.size, INK + (0,))
    out.putalpha(sh)
    out.save(OUT, optimize=True)
    sh.save('_psd/v4_shape.png')
    import os
    print(f'输出 {out.size} {os.path.getsize(OUT)/1024:.1f} KB')


if __name__ == '__main__':
    main()
