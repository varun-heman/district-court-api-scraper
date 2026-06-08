#!/usr/bin/env python3
"""Kaplan-Meier analysis for Saras (Ahmedabad) cases.

Two lifecycle endpoints, both measured from the filing date:
  1. time to disposal     — event = case disposed; pending cases are censored.
  2. time to first hearing — event = first hearing held; cases with no hearing
                             yet (incl. future-scheduled) are censored.

Input is the ``cases_min.jsonl`` produced by scrape_saras_km.py. Survival is
estimated with the product-limit (Kaplan-Meier) estimator and Greenwood
standard errors; outputs are a survival-table CSV per endpoint plus a PNG plot.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ORDINAL_RE = re.compile(r"(\d{1,2})(st|nd|rd|th)", re.IGNORECASE)


def parse_court_date(value: str) -> date | None:
    """Handle both 'DD-MM-YYYY' and ordinal text like '21st August 2025'."""
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    cleaned = ORDINAL_RE.sub(r"\1", s)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Obs:
    duration: float  # days
    event: int       # 1 = event observed, 0 = censored


def km_estimate(obs: list[Obs]) -> list[dict]:
    """Product-limit estimator. Returns rows at each distinct event time."""
    n = len(obs)
    if n == 0:
        return []
    durations = sorted({o.duration for o in obs if o.event == 1})
    rows: list[dict] = []
    survival = 1.0
    var_sum = 0.0  # Greenwood accumulator
    for t in durations:
        at_risk = sum(1 for o in obs if o.duration >= t)
        d = sum(1 for o in obs if o.event == 1 and o.duration == t)
        c = sum(1 for o in obs if o.event == 0 and o.duration == t)
        if at_risk == 0:
            continue
        survival *= (1.0 - d / at_risk)
        if at_risk - d > 0:
            var_sum += d / (at_risk * (at_risk - d))
        se = survival * math.sqrt(var_sum) if survival > 0 else 0.0
        rows.append(
            {
                "time_days": t,
                "n_risk": at_risk,
                "n_event": d,
                "n_censored_at_t": c,
                "survival": survival,
                "ci_low": max(0.0, survival - 1.96 * se),
                "ci_high": min(1.0, survival + 1.96 * se),
            }
        )
    return rows


def median_survival(rows: list[dict]) -> float | None:
    for r in rows:
        if r["survival"] <= 0.5:
            return r["time_days"]
    return None


def quantile_time(rows: list[dict], frac_event: float) -> float | None:
    """Time at which `frac_event` of the population has had the event (survival <= 1-frac)."""
    target = 1.0 - frac_event
    for r in rows:
        if r["survival"] <= target:
            return r["time_days"]
    return None


def build_obs(records: list[dict], endpoint: str, snapshot: date) -> tuple[list[Obs], dict]:
    obs: list[Obs] = []
    stats = {"total": 0, "events": 0, "censored": 0, "dropped_no_filing": 0, "dropped_bad_duration": 0}
    for rec in records:
        filing = parse_court_date(rec.get("filing_date", ""))
        if filing is None:
            stats["dropped_no_filing"] += 1
            continue
        if endpoint == "disposal":
            decision = parse_court_date(rec.get("decision_date", ""))
            disposed = decision is not None and decision >= filing
            if disposed:
                dur = (decision - filing).days
                event = 1
            else:
                dur = (snapshot - filing).days
                event = 0
        elif endpoint == "first_hearing":
            hearing_dates = [parse_court_date(d) for d in rec.get("hearing_dates", [])]
            cand = [d for d in hearing_dates if d is not None and d >= filing and d <= snapshot]
            fh_field = parse_court_date(rec.get("first_hearing_date", ""))
            if fh_field is not None and filing <= fh_field <= snapshot:
                cand.append(fh_field)
            first = min(cand) if cand else None
            if first is not None:
                dur = (first - filing).days
                event = 1
            else:
                dur = (snapshot - filing).days
                event = 0
        else:
            raise ValueError(endpoint)
        stats["total"] += 1
        if dur < 0:
            stats["dropped_bad_duration"] += 1
            continue
        obs.append(Obs(duration=float(dur), event=event))
        stats["events" if event else "censored"] += 1
    return obs, stats


def write_table(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["time_days", "n_risk", "n_event", "n_censored_at_t", "survival", "ci_low", "ci_high"],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def step_xy(rows: list[dict]) -> tuple[list[float], list[float]]:
    xs = [0.0]
    ys = [1.0]
    for r in rows:
        xs.append(r["time_days"])
        ys.append(r["survival"])
    return xs, ys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Kaplan-Meier curves for Saras cases")
    p.add_argument("--input", required=True, help="cases_min.jsonl from scrape_saras_km.py")
    p.add_argument("--outdir", default=None, help="default: alongside input, ../analysis")
    p.add_argument("--snapshot", default="2026-05-28", help="as-of / censoring date YYYY-MM-DD")
    args = p.parse_args(argv)

    snapshot = datetime.strptime(args.snapshot, "%Y-%m-%d").date()
    inp = Path(args.input)
    records = [json.loads(ln) for ln in inp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    outdir = Path(args.outdir) if args.outdir else inp.parent.parent / "analysis"
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"records={len(records)} snapshot={snapshot}")

    results = {}
    for endpoint, title in [("disposal", "Time to Disposal"), ("first_hearing", "Time to First Hearing")]:
        obs, stats = build_obs(records, endpoint, snapshot)
        rows = km_estimate(obs)
        write_table(rows, outdir / f"km_{endpoint}.csv")
        med = median_survival(rows)
        results[endpoint] = (rows, stats, med, title)
        print(f"\n=== {title} ===")
        print(f"  observations={stats['total']} events={stats['events']} censored={stats['censored']} "
              f"dropped_no_filing={stats['dropped_no_filing']} dropped_bad_duration={stats['dropped_bad_duration']}")
        print(f"  median {title.lower()}: {med if med is not None else 'not reached'} days")
        for frac in (0.25, 0.5, 0.75):
            q = quantile_time(rows, frac)
            print(f"  {int(frac*100)}% had event by: {q if q is not None else 'not reached'} days")

    # Combined plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    for ax, endpoint in zip(axes, ["disposal", "first_hearing"]):
        rows, stats, med, title = results[endpoint]
        xs, ys = step_xy(rows)
        ax.step(xs, ys, where="post", color="#1f4e79", lw=1.8, label="S(t)")
        # CI band
        cx = [0.0] + [r["time_days"] for r in rows]
        lo = [1.0] + [r["ci_low"] for r in rows]
        hi = [1.0] + [r["ci_high"] for r in rows]
        ax.fill_between(cx, lo, hi, step="post", color="#1f4e79", alpha=0.15, label="95% CI")
        if med is not None:
            ax.axhline(0.5, color="grey", ls=":", lw=0.8)
            ax.axvline(med, color="#c00000", ls="--", lw=1.0, label=f"median={med:.0f}d")
        ax.set_title(f"{title}\n(n={stats['total']}, events={stats['events']}, censored={stats['censored']})")
        ax.set_xlabel("Days since filing")
        ax.set_ylabel("Proportion without event (survival)")
        ax.set_ylim(0, 1.02)
        ax.grid(alpha=0.25)
        ax.legend(loc="upper right", fontsize=9)
    fig.suptitle("Saras (Ahmedabad) District Court — Kaplan-Meier (start = filing date)", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    png = outdir / "km_saras_curves.png"
    fig.savefig(png, dpi=150)
    print(f"\nplot={png}")
    print(f"tables={outdir}/km_disposal.csv , {outdir}/km_first_hearing.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
