"""Survey: how many SOLIDWORKS parts of a folder open with an exact, editable feature history.

    python tools/survey.py run CORPUS_DIR -o results.jsonl [--timeout 115] [--limit N] [--smallest-first]
        open every .sldprt under CORPUS_DIR the way the application does (isolated rebuild, time limit) and
        append one line per part to results.jsonl. Resumable: parts already in the file are skipped, so it
        can be run in chunks (--limit) and survives being stopped.
    python tools/survey.py summary results.jsonl
    python tools/survey.py compare before.jsonl after.jsonl
        parts gained and lost between two runs - check this before trusting a change to the rebuild.

The corpus is yours to supply (for example the sample parts of a SOLIDWORKS installation); no SOLIDWORKS
files are part of this repository, and none should be committed. Result files hold part names and outcomes
only. openswx reads the files natively: SOLIDWORKS is not started.

Outcome of a part: exact (rebuilt history gives the stored body) - with `parametric` true when no feature
had to be imported from the stored body; inexact; timeout; crash; error (with the reason).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: str) -> dict[str, dict]:
    out = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["part"]] = r
    return out


def classify(report: dict | None, error: str | None) -> dict:
    """One result line from what rebuild_isolated returned or raised."""
    if error is not None:
        low = error.lower()
        outcome = "timeout" if "took longer" in low else "crash" if "crash" in low else "error"
        return {"outcome": outcome, "reason": error[:300]}
    vol, stored = report.get("volume"), report.get("stored_volume")
    exact = stored is not None and vol is not None and abs(vol - stored) <= 1e-6 * max(1.0, abs(stored))
    imported = report.get("imported") or []
    return {"outcome": "exact" if exact else "inexact", "parametric": exact and not imported,
            "features": len(report.get("features") or []), "imported": len(imported),
            "not_rebuilt": len(report.get("skipped") or []), "volume": vol, "stored_volume": stored}


def run(corpus: str, out: str, timeout: float, limit: int | None, smallest_first: bool, rebuild=None) -> int:
    if rebuild is None:
        sys.path.insert(0, str(ROOT))
        os.environ.setdefault("OPENSWX_CACHE", "off")  # (measure the rebuild, not the cache)
        from openswx.sw_rebuild import RebuildError, rebuild_isolated

        def rebuild(path):
            try:
                _data, report = rebuild_isolated(path, timeout=timeout)
                return report, None
            except RebuildError as exc:
                return None, str(exc)
    files = sorted(p for p in Path(corpus).rglob("*") if p.suffix.lower() == ".sldprt")
    if smallest_first:
        files.sort(key=lambda p: p.stat().st_size)
    done = load(out)
    todo = [p for p in files if p.name not in done]
    if limit is not None:
        todo = todo[:limit]
    print(f"{len(files)} parts, {len(done)} already surveyed, {len(todo)} to do", file=sys.stderr)
    with open(out, "a", encoding="utf-8") as fh:
        for i, p in enumerate(todo, 1):
            t = time.time()
            try:
                report, error = rebuild(str(p))
            except Exception as exc:  # noqa: BLE001 - one part must not end the survey
                report, error = None, f"{type(exc).__name__}: {exc}"
            row = {"part": p.name, "seconds": round(time.time() - t, 1), **classify(report, error)}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"[{i}/{len(todo)}] {p.name}: {row['outcome']} ({row['seconds']} s)", file=sys.stderr)
    return 0


def summary(rows: dict[str, dict]) -> dict:
    counts: dict[str, int] = {}
    for r in rows.values():
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return {"parts": len(rows), "exact": counts.get("exact", 0),
            "fully_parametric": sum(1 for r in rows.values() if r.get("parametric")), "by_outcome": dict(sorted(counts.items()))}


def compare(before: dict[str, dict], after: dict[str, dict]) -> dict:
    both = sorted(set(before) & set(after))

    def ok(r):
        return r["outcome"] == "exact"
    return {"compared": len(both),
            "gained": [p for p in both if not ok(before[p]) and ok(after[p])],
            "lost": [p for p in both if ok(before[p]) and not ok(after[p])],
            "parametric_gained": [p for p in both if not before[p].get("parametric") and after[p].get("parametric")],
            "parametric_lost": [p for p in both if before[p].get("parametric") and not after[p].get("parametric")],
            "only_before": sorted(set(before) - set(after)), "only_after": sorted(set(after) - set(before)),
            "before": summary({p: before[p] for p in both}), "after": summary({p: after[p] for p in both})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Survey SOLIDWORKS parts: exact, editable feature histories.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("corpus")
    r.add_argument("-o", "--output", required=True)
    r.add_argument("--timeout", type=float, default=115.0)
    r.add_argument("--limit", type=int)
    r.add_argument("--smallest-first", action="store_true")
    s = sub.add_parser("summary")
    s.add_argument("results")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a.corpus, a.output, a.timeout, a.limit, a.smallest_first)
    if a.cmd == "summary":
        print(json.dumps(summary(load(a.results)), indent=1))
        return 0
    res = compare(load(a.before), load(a.after))
    print(json.dumps(res, indent=1))
    return 1 if res["lost"] or res["parametric_lost"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
