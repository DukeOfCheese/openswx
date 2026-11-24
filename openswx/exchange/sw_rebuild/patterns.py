"""Pattern trial ranking for the SOLIDWORKS history rebuild."""
from __future__ import annotations

import copy
import math

from openswx.core.document import PartDocument

from ._base import RebuildError, _Oracle, _check_time, _log

def _try_pattern(doc: PartDocument, oracle: "_Oracle", ftype: str, name: str, variants: list[dict]) -> dict:
    """A pattern's variants ranked by its copies alone: an added copy should lie inside the stored body, a cut
    one outside it. Each copy is a small check; building the patterned body for every variant is not (24 rib
    copies fused into a body took 15-90 s per variant). The best variant is then built."""
    best, best_fit = None, -math.inf
    for params in variants:
        _check_time()
        ctx = copy.copy(doc.ctx)
        for fld in ("tools", "info"):
            setattr(ctx, fld, dict(getattr(ctx, fld)))
        ctx.info["_tools_only"] = True
        try:
            from openswx.core.features import BUILDERS
            BUILDERS[ftype](ctx, params, name)
        except Exception:
            continue
        seeds = len(doc.ctx.tools.get(params["features"][0], [])) if params.get("features") else 0
        copies = ctx.tools.get(name, [])[seeds:]
        inside = total = 0.0
        for op, tool in copies:
            v = tool.volume
            if v <= 0:
                continue
            c = oracle._common(tool, v)
            inside += c if op == "add" else v - c
            total += v
        fit = inside / total if total > 0 else -math.inf
        _log(f"{name}: pattern variant fits {fit:.4f} ({len(copies)} copies)")
        if fit > best_fit + 1e-9:
            best, best_fit = params, fit
    if best is None:
        raise RebuildError(f"{name}: no variant builds")
    return doc.add_feature(ftype, best, name=name)
