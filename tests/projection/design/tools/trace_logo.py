"""Trace the Lonvera logo PNG (lonvera-theme/assets/lp-bl-img-lonvera-logo-dark.png, 560x242, black on transparent)
into clean SVG paths with a small marching-squares tracer (no external tools).

Usage:  python trace_logo.py <logo.png> <out_dir>
Writes: lonvera-logo-{black,cream}.svg (stacked mark + wordmark), lonvera-mark-{black,cream}.svg,
        lonvera-wordmark-{black,cream}.svg, lonvera-lockup-cream.svg (mark + wordmark side by side, for the top bar),
        lonvera-paths.json (the traced subpaths, for the DESIGN.md provenance note).
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image

SCALE = 4           # supersample factor before tracing
LEVEL = 0.5
EPS = 0.35          # Douglas-Peucker tolerance in source pixels
BLACK, CREAM = "#0B0A08", "#ECE5D9"


def load_field(path):
    im = Image.open(path).convert("RGBA")
    a = im.split()[3]
    w, h = a.size
    big = a.resize((w * SCALE, h * SCALE), Image.BICUBIC)
    W, H = big.size
    px = big.load()
    f = [[0.0] * (W + 2) for _ in range(H + 2)]   # 1px zero border
    for y in range(H):
        row = f[y + 1]
        for x in range(W):
            row[x + 1] = px[x, y] / 255.0
    return f, W + 2, H + 2, (w, h)


def trace(f, W, H):
    segs = {}      # edge key -> list of neighbouring edge keys

    def pt(key):
        t, x, y = key
        if t == "h":
            a, b = f[y][x], f[y][x + 1]
            return (x + (LEVEL - a) / (b - a), y)
        a, b = f[y][x], f[y + 1][x]
        return (x, y + (LEVEL - a) / (b - a))

    def link(k1, k2):
        segs.setdefault(k1, []).append(k2)
        segs.setdefault(k2, []).append(k1)

    for y in range(H - 1):
        r0, r1 = f[y], f[y + 1]
        for x in range(W - 1):
            tl, tr, br, bl = r0[x] >= LEVEL, r0[x + 1] >= LEVEL, r1[x + 1] >= LEVEL, r1[x] >= LEVEL
            idx = tl * 8 + tr * 4 + br * 2 + bl
            if idx in (0, 15):
                continue
            T, R, B, L = ("h", x, y), ("v", x + 1, y), ("h", x, y + 1), ("v", x, y)
            if idx in (1, 14): link(L, B)
            elif idx in (2, 13): link(B, R)
            elif idx in (3, 12): link(L, R)
            elif idx in (4, 11): link(T, R)
            elif idx in (6, 9): link(T, B)
            elif idx in (7, 8): link(T, L)
            elif idx == 5:
                c = (r0[x] + r0[x + 1] + r1[x] + r1[x + 1]) / 4 >= LEVEL
                if c: link(T, L); link(B, R)
                else: link(T, R); link(L, B)
            elif idx == 10:
                c = (r0[x] + r0[x + 1] + r1[x] + r1[x + 1]) / 4 >= LEVEL
                if c: link(T, R); link(L, B)
                else: link(T, L); link(B, R)
    loops, seen = [], set()
    for start in segs:
        if start in seen:
            continue
        loop, prev, cur = [], None, start
        while True:
            seen.add(cur)
            loop.append(pt(cur))
            nxt = [n for n in segs[cur] if n != prev]
            if not nxt:
                break
            n = nxt[0]
            if n == start or n in seen and n != start and len(segs[cur]) == 1:
                break
            if n in seen:
                break
            prev, cur = cur, n
        if len(loop) > 8:
            loops.append(loop)
    return loops


def dp(points, eps):
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-9
    idx, dmax = 0, -1.0
    for i in range(1, len(points) - 1):
        d = abs(dy * points[i][0] - dx * points[i][1] + x2 * y1 - y2 * x1) / norm
        if d > dmax:
            idx, dmax = i, d
    if dmax > eps:
        return dp(points[: idx + 1], eps)[:-1] + dp(points[idx:], eps)
    return [points[0], points[-1]]


def simplify_closed(loop, eps):
    # split at the point farthest from the first so the closed ring simplifies in two halves
    p0 = loop[0]
    far = max(range(len(loop)), key=lambda i: (loop[i][0] - p0[0]) ** 2 + (loop[i][1] - p0[1]) ** 2)
    a = dp(loop[: far + 1], eps)
    b = dp(loop[far:] + [loop[0]], eps)
    return a[:-1] + b[:-1]


def bbox(loop):
    xs = [p[0] for p in loop]; ys = [p[1] for p in loop]
    return min(xs), min(ys), max(xs), max(ys)


def path_d(loop, dx=0.0, dy=0.0, k=1.0):
    pts = [((x - 1) / SCALE * k + dx, (y - 1) / SCALE * k + dy) for x, y in loop]   # remove the 1px border, back to source px
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z"


def svg(vb_w, vb_h, paths, fill, title):
    body = "".join(f'<path d="{d}"/>' for d in paths)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w:g} {vb_h:g}" role="img" aria-label="{title}">'
            f'<g fill="{fill}" fill-rule="evenodd">{body}</g></svg>\n')


def main(src, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    f, W, H, (w, h) = load_field(src)
    loops = [simplify_closed(l, EPS * SCALE) for l in trace(f, W, H)]
    mark, word = [], []
    for l in loops:
        x0, y0, x1, y1 = bbox(l)
        cy = ((y0 + y1) / 2 - 1) / SCALE
        (mark if cy < 175 else word).append(l)
    # source geometry: mark rows 0-157, wordmark rows 196-241
    mark_h, word_y0 = 158, 196
    word_h = 242 - word_y0
    mark_d = [path_d(l) for l in mark]
    word_d = [path_d(l, dy=-word_y0) for l in word]
    for name, fill in (("black", BLACK), ("cream", CREAM)):
        (out / f"lonvera-logo-{name}.svg").write_text(svg(w, h, [path_d(l) for l in mark + word], fill, "Lonvera"))
        (out / f"lonvera-mark-{name}.svg").write_text(svg(w, mark_h, mark_d, fill, "Lonvera mountain mark"))
        (out / f"lonvera-wordmark-{name}.svg").write_text(svg(w, word_h, word_d, fill, "Lonvera"))
    # horizontal lockup: mark (scaled to 40 high) + gap + wordmark (scaled so letter height ~ 12)
    mk = 40 / mark_h
    mw = w * mk
    wk = 16 / word_h
    ww = w * wk
    gap = 16
    total_w = mw + gap + ww
    paths = [path_d(l, 0, 0, mk) for l in mark] + [path_d(l, mw + gap, (40 - 16) / 2 - word_y0 * wk, wk) for l in word]
    # path_d scales the whole coordinate then adds dx/dy, so wordmark y needs its own offset: recompute explicitly
    paths = [path_d(l, 0, 0, mk) for l in mark]
    for l in word:
        pts = [(((x - 1) / SCALE) * wk + mw + gap, ((y - 1) / SCALE - word_y0) * wk + (40 - 16) / 2) for x, y in l]
        paths.append("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
    for name, fill in (("cream", CREAM), ("black", BLACK)):
        (out / f"lonvera-lockup-{name}.svg").write_text(svg(round(total_w, 1), 40, paths, fill, "Lonvera"))
    k = 52 / w
    fav_paths = "".join(f'<path d="{path_d(l)}"/>' for l in mark)
    (out / "favicon.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="{BLACK}"/>'
        f'<g transform="translate(6 {(64 - mark_h * k) / 2:.1f}) scale({k:.4f})" fill="{CREAM}" fill-rule="evenodd">{fav_paths}</g></svg>\n')
    (out / "lonvera-paths.json").write_text(json.dumps({"mark_loops": len(mark), "wordmark_loops": len(word), "scale": SCALE, "eps_px": EPS}, indent=1))
    print("loops", len(loops), "mark", len(mark), "word", len(word), "lockup w", round(total_w, 1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
