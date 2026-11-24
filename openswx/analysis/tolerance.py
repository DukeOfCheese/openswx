from __future__ import annotations

import re

TYPES = ("none", "symmetric", "bilateral", "limit", "fit")

# upper bounds of the nominal size ranges (mm): "over the previous, up to and including this"
_SIZES = (3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500)
# standard tolerance grades, micrometres
_IT = {
    5: (4, 5, 6, 8, 9, 11, 13, 15, 18, 20, 23, 25, 27),
    6: (6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40),
    7: (10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63),
    8: (14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97),
    9: (25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155),
    10: (40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250),
    11: (60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400),
    12: (100, 120, 150, 180, 210, 250, 300, 350, 400, 460, 520, 570, 630),
}
# shafts whose fundamental deviation is the upper one (es), micrometres
_SHAFT_ES = {
    "d": (-20, -30, -40, -50, -65, -80, -100, -120, -145, -170, -190, -210, -230),
    "e": (-14, -20, -25, -32, -40, -50, -60, -72, -85, -100, -110, -125, -135),
    "f": (-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68),
    "g": (-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20),
    "h": (0,) * 13,
}
# shafts whose fundamental deviation is the lower one (ei), micrometres (k: grades up to IT7; above, 0)
_SHAFT_EI = {
    "k": (0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 5),
    "m": (2, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23),
    "n": (4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40),
    "p": (6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68),
}


class ToleranceError(ValueError):
    pass


def _range(size: float) -> int:
    if not 0 < size <= 500:
        raise ToleranceError(f"ISO 286 fits here cover sizes over 0 up to 500 mm (got {size:g})")
    return next(i for i, top in enumerate(_SIZES) if size <= top)


def fit_deviations(size: float, fit: str) -> tuple[float, float]:
    """(lower, upper) deviation in mm of an ISO 286 tolerance class at a nominal size: 'H7' (hole, capital
    letter) or 'g6' (shaft, small letter)."""
    m = re.fullmatch(r"\s*([A-Za-z]{1,2})\s*(\d{1,2})\s*", fit or "")
    if not m:
        raise ToleranceError(f"cannot read the fit {fit!r} (expected e.g. 'H7' or 'g6')")
    letter, grade = m.group(1), int(m.group(2))
    if grade not in _IT:
        raise ToleranceError(f"tolerance grade IT{grade} is not in the table (IT5 to IT12)")
    i = _range(float(size))
    it = _IT[grade][i]
    low = letter.lower()
    hole = letter[0].isupper()
    if letter not in (low, low.upper()):
        raise ToleranceError(f"cannot read the fit {fit!r}")
    if low == "js":
        lo, hi = -it / 2, it / 2
    elif low in _SHAFT_ES:
        es = _SHAFT_ES[low][i]
        lo, hi = (-es, -es + it) if hole else (es - it, es)  # hole: EI = -es of the same letter
    elif low in _SHAFT_EI:
        ei = _SHAFT_EI[low][i] if not (low == "k" and grade > 7) else 0
        if not hole:
            lo, hi = ei, ei + it
        else:
            # holes K, M, N (up to IT8) and P (up to IT7): ES = -ei + delta, delta = IT(n) - IT(n-1); none up to 3 mm
            top = 7 if low == "p" else 8
            if grade > top or grade < 6:
                raise ToleranceError(f"hole class {letter}{grade} is not in the table ({letter}6 to {letter}{top})")
            delta = 0 if i == 0 else it - _IT[grade - 1][i]
            hi = -_SHAFT_EI[low][i] + delta
            lo = hi - it
    else:
        known = sorted({*_SHAFT_ES, *_SHAFT_EI, "js"})
        raise ToleranceError(f"fit letter {letter!r} is not in the table (shafts {', '.join(known)}; holes in capitals)")
    return lo / 1000.0, hi / 1000.0


def normalize(tol: dict | None) -> dict | None:
    """A checked copy of a tolerance (None for 'none' / nothing). Raises ToleranceError on a bad one."""
    if not tol or tol.get("type", "none") == "none":
        return None
    t = tol.get("type")
    if t not in TYPES:
        raise ToleranceError(f"unknown tolerance type {t!r}; expected one of {TYPES}")
    if t == "fit":
        fit = str(tol.get("fit") or "").strip()
        fit_deviations(10.0, fit)  # (checks the class; the size comes later)
        return {"type": "fit", "fit": fit}
    try:
        plus = abs(float(tol.get("plus", 0.0) or 0.0))
        minus = plus if t == "symmetric" else abs(float(tol.get("minus", 0.0) or 0.0))
    except (TypeError, ValueError):
        raise ToleranceError("tolerance values must be numbers") from None
    if plus == 0 and minus == 0:
        raise ToleranceError("give the tolerance a value (plus and / or minus)")
    return {"type": t, "plus": plus} if t == "symmetric" else {"type": t, "plus": plus, "minus": minus}


def deviations(nominal: float, tol: dict | None) -> tuple[float, float] | None:
    """(lower, upper) deviation from the nominal, or None without a tolerance."""
    tol = normalize(tol)
    if tol is None:
        return None
    if tol["type"] == "fit":
        return fit_deviations(abs(nominal), tol["fit"])
    return -tol.get("minus", tol["plus"]), tol["plus"]


def limits(nominal: float, tol: dict | None) -> tuple[float, float] | None:
    d = deviations(nominal, tol)
    return None if d is None else (nominal + d[0], nominal + d[1])


def _num(v: float, decimals: int = 3) -> str:
    return f"{v:.{decimals}f}".rstrip("0").rstrip(".") or "0"


def _signed(v: float) -> str:
    return ("+" if v > 0 else "-" if v < 0 else "") + _num(abs(v))


def text(nominal_text: str, nominal: float, tol: dict | None, show_fit_deviations: bool = True) -> str:
    """The dimension text with its tolerance: '25 ±0.1', '25 +0.1/-0.05', '25.1 / 24.95', '25 H7 (+0.021/0)'."""
    tol = normalize(tol)
    if tol is None:
        return nominal_text
    lo, hi = deviations(nominal, tol)
    if tol["type"] == "symmetric":
        return f"{nominal_text} ±{_num(hi)}"
    if tol["type"] == "bilateral":
        return f"{nominal_text} {_signed(hi)}/{_signed(lo)}"
    if tol["type"] == "limit":
        return f"{_num(nominal + hi)} / {_num(nominal + lo)}"
    extra = f" ({_signed(hi)}/{_signed(lo)})" if show_fit_deviations else ""
    return f"{nominal_text} {tol['fit']}{extra}"


def fit_summary(size: float, hole: str, shaft: str) -> dict:
    """A hole / shaft pair at a nominal size: both sets of limits and the clearance range (negative =
    interference), with the kind of fit."""
    h_lo, h_hi = fit_deviations(size, hole)
    s_lo, s_hi = fit_deviations(size, shaft)
    if not hole.strip()[0].isupper() or not shaft.strip()[0].islower():
        raise ToleranceError("give the hole class in capitals and the shaft class in small letters (e.g. H7, g6)")
    cmin, cmax = h_lo - s_hi, h_hi - s_lo
    kind = "clearance" if cmin >= 0 else "interference" if cmax <= 0 else "transition"
    r = lambda v: round(v, 6)  # noqa: E731
    return {"size": size, "hole": {"class": hole.strip(), "lower": r(h_lo), "upper": r(h_hi), "min": r(size + h_lo), "max": r(size + h_hi)},
            "shaft": {"class": shaft.strip(), "lower": r(s_lo), "upper": r(s_hi), "min": r(size + s_lo), "max": r(size + s_hi)},
            "min_clearance": r(cmin), "max_clearance": r(cmax), "fit": kind}
