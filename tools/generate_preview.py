"""
generate_preview.py — One-shot script to produce social_preview.png (1280×640)
Run once then delete. Output: social_preview.png in project root.
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1280, 640
OUT  = str(Path(__file__).resolve().parent.parent / "social_preview.png")

# ── Palette ────────────────────────────────────────────────────────────────────
BG_TOP    = (6,  6,  14)       # near-black navy
BG_BOT    = (10, 10, 22)
GOLD      = (212, 175, 55)     # classic gold
GOLD_LITE = (240, 210, 100)
GOLD_DIM  = (140, 110, 30)
WHITE     = (255, 255, 255)
MUTED     = (160, 160, 185)

img  = Image.new("RGB", (W, H), BG_TOP)
draw = ImageDraw.Draw(img)

# ── Vertical gradient background ──────────────────────────────────────────────
for y in range(H):
    t = y / H
    r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
    g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
    b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
    draw.line([(0, y), (W, y)], fill=(r, g, b))

# ── Subtle grid lines ──────────────────────────────────────────────────────────
for x in range(0, W, 80):
    draw.line([(x, 0), (x, H)], fill=(20, 20, 40), width=1)
for y in range(0, H, 80):
    draw.line([(0, y), (W, y)], fill=(20, 20, 40), width=1)

# ── Corner accent lines (gold frame, slightly inset) ──────────────────────────
margin = 24
line_len = 60
lw = 2
corners = [
    (margin, margin),                  # top-left
    (W - margin, margin),              # top-right
    (margin, H - margin),              # bottom-left
    (W - margin, H - margin),          # bottom-right
]
for cx, cy in corners:
    dx = 1 if cx < W // 2 else -1
    dy = 1 if cy < H // 2 else -1
    draw.line([(cx, cy), (cx + dx * line_len, cy)], fill=GOLD, width=lw)
    draw.line([(cx, cy), (cx, cy + dy * line_len)], fill=GOLD, width=lw)

# ── Horizontal gold rule at 35% height ────────────────────────────────────────
rule_y = int(H * 0.35)
draw.line([(margin + line_len + 12, rule_y), (W - margin - line_len - 12, rule_y)],
          fill=GOLD_DIM, width=1)

# ── "M" logo mark (geometric diamond / hexagon) ───────────────────────────────
lx, ly = 100, H // 2 - 10
r_outer = 38
r_inner = 18
pts_outer = []
pts_inner = []
for i in range(6):
    ang = math.radians(i * 60 - 30)
    pts_outer.append((lx + r_outer * math.cos(ang), ly + r_outer * math.sin(ang)))
    ang2 = math.radians(i * 60 + 30)
    pts_inner.append((lx + r_inner * math.cos(ang2), ly + r_inner * math.sin(ang2)))

draw.polygon(pts_outer, fill=None, outline=GOLD, width=2)
draw.polygon(pts_inner, fill=GOLD_DIM, outline=None)

# Draw "M" inside the hex
try:
    fnt_m = ImageFont.truetype("arialbd.ttf", 28)
except Exception:
    fnt_m = ImageFont.load_default()
bb = draw.textbbox((0, 0), "M", font=fnt_m)
tw, th = bb[2] - bb[0], bb[3] - bb[1]
draw.text((lx - tw // 2, ly - th // 2 - 2), "M", font=fnt_m, fill=GOLD)

# ── Main title ────────────────────────────────────────────────────────────────
try:
    fnt_title = ImageFont.truetype("arialbd.ttf", 96)
except Exception:
    fnt_title = ImageFont.load_default()

title = "MIDAS CAPITAL"
bb = draw.textbbox((0, 0), title, font=fnt_title)
tw, th = bb[2] - bb[0], bb[3] - bb[1]
tx = (W - tw) // 2
ty = H // 2 - th // 2 - 48

# Subtle shadow
draw.text((tx + 3, ty + 3), title, font=fnt_title, fill=(0, 0, 0, 180))
draw.text((tx, ty), title, font=fnt_title, fill=GOLD)

# ── Tagline ───────────────────────────────────────────────────────────────────
try:
    fnt_tag = ImageFont.truetype("arial.ttf", 28)
except Exception:
    fnt_tag = ImageFont.load_default()

tagline = "Algorithmic Gold Trading System"
bb = draw.textbbox((0, 0), tagline, font=fnt_tag)
tw2, th2 = bb[2] - bb[0], bb[3] - bb[1]
draw.text(((W - tw2) // 2, ty + th + 18), tagline, font=fnt_tag, fill=MUTED)

# ── Bot labels row ────────────────────────────────────────────────────────────
try:
    fnt_sm = ImageFont.truetype("arial.ttf", 18)
except Exception:
    fnt_sm = ImageFont.load_default()

labels = ["BOT 1  |  Structural Bias", "BOT 2  |  Volatility Regime"]
col_xs = [W // 4, 3 * W // 4]
label_y = ty + th + 18 + th2 + 36

for lbl, lx2 in zip(labels, col_xs):
    bb = draw.textbbox((0, 0), lbl, font=fnt_sm)
    lw2 = bb[2] - bb[0]
    draw.text((lx2 - lw2 // 2, label_y), lbl, font=fnt_sm, fill=GOLD_LITE)

# Separator between bot labels
draw.line([(W // 2, label_y - 4), (W // 2, label_y + 22)], fill=GOLD_DIM, width=1)

# ── Bottom caption ────────────────────────────────────────────────────────────
try:
    fnt_cap = ImageFont.truetype("arial.ttf", 16)
except Exception:
    fnt_cap = ImageFont.load_default()

caption = "XAUUSD  ·  M5  ·  MetaTrader 5  ·  Python"
bb = draw.textbbox((0, 0), caption, font=fnt_cap)
draw.text(((W - (bb[2] - bb[0])) // 2, H - margin - 20), caption, font=fnt_cap, fill=GOLD_DIM)

img.save(OUT, "PNG", dpi=(96, 96))
print(f"Saved: {os.path.abspath(OUT)}  ({W}×{H})")
