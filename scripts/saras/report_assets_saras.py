#!/usr/bin/env python3
"""Generate styled chart PNGs for the SARAS report (editorial data-dossier palette)."""
from __future__ import annotations

import base64
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INP = ROOT / "output/runs/saras_km_full_20260528/normalized/cases_min.jsonl"
OUT = ROOT / "output/runs/saras_km_full_20260528/analysis/report_assets"
OUT.mkdir(parents=True, exist_ok=True)

PAPER = "#f4efe4"
INK = "#23232f"
OXBLOOD = "#8c2f39"
SLATE = "#4f6d8c"
BRASS = "#9a7b3f"
GRID = "#d8cfbd"

# Prefer a serif that exists; fall back gracefully.
for fam in ("Georgia", "Times New Roman", "DejaVu Serif"):
    try:
        font_manager.findfont(fam, fallback_to_default=False)
        plt.rcParams["font.serif"] = [fam]
        break
    except Exception:  # noqa: BLE001
        continue
plt.rcParams.update({
    "font.family": "serif",
    "figure.facecolor": PAPER,
    "axes.facecolor": PAPER,
    "savefig.facecolor": PAPER,
    "text.color": INK,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.linewidth": 0.8,
})

ORD = re.compile(r"(\d{1,2})(st|nd|rd|th)", re.I)


def pd(s: str):
    s = (s or "").strip()
    if not s:
        return None
    for f in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    c = ORD.sub(r"\1", s)
    for f in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(c, f).date()
        except ValueError:
            pass
    return None


recs = [json.loads(l) for l in INP.read_text().splitlines() if l.strip()]
SNAP = date(2026, 5, 28)


def km_cuminc(obs):
    """Return (xs, cumulative-incidence ys) where ci = 1 - product-limit survival."""
    ev = sorted({d for d, e in obs if e == 1})
    xs, ys = [0.0], [0.0]
    s = 1.0
    for t in ev:
        at_risk = sum(1 for d, _ in obs if d >= t)
        dd = sum(1 for d, e in obs if e == 1 and d == t)
        if at_risk == 0:
            continue
        s *= (1 - dd / at_risk)
        xs.append(t)
        ys.append(1 - s)
    return xs, ys


# ---- Chart 1: volume by month (stacked bars) ----
by_month = defaultdict(lambda: {"pending": 0, "disposed": 0})
for r in recs:
    f = pd(r["filing_date"])
    if not f:
        continue
    by_month[f"{f.year}-{f.month:02d}"][r["status_bucket"]] += 1
months = sorted(by_month)
labels = [datetime.strptime(m, "%Y-%m").strftime("%b\n%Y") for m in months]
pend = [by_month[m]["pending"] for m in months]
disp = [by_month[m]["disposed"] for m in months]

fig, ax = plt.subplots(figsize=(11, 4.6))
x = range(len(months))
ax.bar(x, pend, color=SLATE, label="Still pending", width=0.74)
ax.bar(x, disp, bottom=pend, color=OXBLOOD, label="Disposed", width=0.74)
ax.set_xticks(list(x))
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel("Cases filed", fontsize=11)
ax.legend(frameon=False, fontsize=10, loc="upper right")
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="y", color=GRID, lw=0.6)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig(OUT / "volume_over_time.png", dpi=150)
plt.close(fig)

# ---- Chart 2: cumulative disposal ----
obs_d = []
for r in recs:
    f = pd(r["filing_date"])
    if not f:
        continue
    d = pd(r["decision_date"])
    if r["status_bucket"] == "disposed" and d and d >= f:
        obs_d.append(((d - f).days, 1))
    else:
        obs_d.append(((SNAP - f).days, 0))
xs, ys = km_cuminc(obs_d)
fig, ax = plt.subplots(figsize=(7.2, 4.8))
ax.step(xs, [v * 100 for v in ys], where="post", color=OXBLOOD, lw=2.4)
ax.fill_between(xs, [v * 100 for v in ys], step="post", color=OXBLOOD, alpha=0.12)
ax.set_xlabel("Days since filing", fontsize=11)
ax.set_ylabel("% of cases disposed", fontsize=11)
ax.set_ylim(0, 100)
ax.axvline(468, color=BRASS, ls="--", lw=1.1)
ax.annotate("25% disposed\nby ~468 days", xy=(468, 25), xytext=(300, 55),
            fontsize=9, color=INK, arrowprops=dict(arrowstyle="->", color=BRASS))
ax.annotate("only ~31% disposed\nafter 17 months", xy=(xs[-1], ys[-1] * 100),
            xytext=(150, 80), fontsize=9, color=OXBLOOD)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(color=GRID, lw=0.6)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig(OUT / "cuminc_disposal.png", dpi=150)
plt.close(fig)

# ---- Chart 3: cumulative first hearing ----
obs_h = []
for r in recs:
    f = pd(r["filing_date"])
    if not f:
        continue
    hd = [pd(x) for x in r.get("hearing_dates", [])]
    cand = [d for d in hd if d and f <= d <= SNAP]
    fh = pd(r.get("first_hearing_date", ""))
    if fh and f <= fh <= SNAP:
        cand.append(fh)
    first = min(cand) if cand else None
    if first:
        obs_h.append(((first - f).days, 1))
    else:
        obs_h.append(((SNAP - f).days, 0))
xs2, ys2 = km_cuminc(obs_h)
fig, ax = plt.subplots(figsize=(7.2, 4.8))
ax.step(xs2, [v * 100 for v in ys2], where="post", color=SLATE, lw=2.4)
ax.fill_between(xs2, [v * 100 for v in ys2], step="post", color=SLATE, alpha=0.12)
ax.set_xlabel("Days since filing", fontsize=11)
ax.set_ylabel("% of cases with a first hearing", fontsize=11)
ax.set_ylim(0, 100)
ax.set_xlim(0, 60)
ax.axvline(3, color=BRASS, ls="--", lw=1.1)
ax.annotate("median first hearing\n= 3 days", xy=(3, 50), xytext=(14, 38),
            fontsize=9, color=INK, arrowprops=dict(arrowstyle="->", color=BRASS))
ax.spines[["top", "right"]].set_visible(False)
ax.grid(color=GRID, lw=0.6)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig(OUT / "cuminc_first_hearing.png", dpi=150)
plt.close(fig)

# Emit base64 for embedding
out = {}
for name in ("volume_over_time", "cuminc_disposal", "cuminc_first_hearing"):
    b = (OUT / f"{name}.png").read_bytes()
    out[name] = "data:image/png;base64," + base64.b64encode(b).decode()
(OUT / "assets_b64.json").write_text(json.dumps(out))
print("wrote", OUT)
for k, v in out.items():
    print(f"  {k}: {len(v)//1024} KB b64")
