"""Summarise runs.jsonl into a per-stage table of calls, cache hits, latency, GPU minutes
and list-price cost. Writes outputs/costs.md; paste it into COSTS.md."""
from __future__ import annotations

import json
from collections import defaultdict

from .common import OUT, RUNS, say


def run() -> str:
    if not RUNS.exists():
        say("runs.jsonl is empty")
        return ""
    rows = [json.loads(l) for l in RUNS.read_text(encoding="utf-8").splitlines() if l.strip()]
    agg = defaultdict(lambda: {"calls": 0, "ok": 0, "errors": 0, "hits": 0, "lat": [], "gpu_s": 0.0, "usd": 0.0, "models": set()})
    for r in rows:
        if r.get("provider") in (None, "planner"):
            continue
        k = (r["stage"], r.get("provider"))
        a = agg[k]
        if r.get("cache") == "hit":
            a["hits"] += 1
            continue
        a["calls"] += 1
        a["ok"] += r.get("status") == "ok"
        a["errors"] += r.get("status") not in ("ok", None)
        if isinstance(r.get("latency_s"), (int, float)) and r.get("status") == "ok":
            a["lat"].append(r["latency_s"])
        a["gpu_s"] += float(r.get("gpu_seconds") or 0)
        a["usd"] += float(r.get("cost_usd_list") or 0)
        if r.get("model"):
            a["models"].add(str(r["model"]))
    lines = ["| stage | provider | calls | ok | errors | cache hits | median latency (s) | total latency (s) | GPU min | list-price USD | models |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    tot_usd = tot_gpu = 0.0
    for (stage, prov), a in sorted(agg.items()):
        lat = sorted(a["lat"])
        med = lat[len(lat) // 2] if lat else 0
        lines.append(f"| {stage} | {prov} | {a['calls']} | {a['ok']} | {a['errors']} | {a['hits']} | {med:.1f} | {sum(lat):.0f} | "
                     f"{a['gpu_s'] / 60:.1f} | {a['usd']:.4f} | {', '.join(sorted(a['models']))[:60]} |")
        tot_usd += a["usd"]
        tot_gpu += a["gpu_s"]
    lines.append(f"\nTotal list-price equivalent: **${tot_usd:.3f}**; GPU time: **{tot_gpu / 60:.1f} min**; "
                 f"actually paid: **$0** (free tiers). Generated from {len(rows)} lines of runs.jsonl.")
    md = "\n".join(lines)
    (OUT / "costs.md").write_text(md + "\n", encoding="utf-8")
    say(md)
    return md
