"""Equations, global variables and configuration targets (SOLIDWORKS-style naming).

Expressions use the SOLIDWORKS equation syntax: global variables by name or quoted ("width"), dimensions
quoted as "D1@Sketch1", feature dimensions as "D1@Boss-Extrude1". Trig functions take degrees.
"""
from __future__ import annotations

import ast
import math
import re

# Feature dimensions named D1, D2, ... like SOLIDWORKS (dotted paths address nested parameters)
FEATURE_DIMS = {
    "extrude": ["depth", "direction2.depth", "draft"],
    "cut_extrude": ["depth", "direction2.depth", "draft"],
    "revolve": ["angle", "angle2"],
    "cut_revolve": ["angle", "angle2"],
    "fillet": ["radius"],
    "chamfer": ["distance", "angle", "distance2"],
    "shell": ["thickness"],
    "hole": ["diameter", "depth", "cbore_diameter", "cbore_depth", "csk_diameter", "csk_angle"],
    "linear_pattern": ["spacing", "count", "direction2.spacing", "direction2.count"],
    "circular_pattern": ["angle", "count"],
    "plane": ["offset", "angle"],
    "draft": ["angle"],
}

_FUNCS = {
    "sin": lambda d: math.sin(math.radians(d)), "cos": lambda d: math.cos(math.radians(d)),
    "tan": lambda d: math.tan(math.radians(d)), "asin": lambda x: math.degrees(math.asin(x)),
    "acos": lambda x: math.degrees(math.acos(x)), "atan": lambda x: math.degrees(math.atan(x)),
    "sqr": math.sqrt, "sqrt": math.sqrt, "abs": abs, "min": min, "max": max, "round": round, "int": int,
}
_CONSTS = {"pi": math.pi}


class EquationError(ValueError):
    pass


def parse_target(target: str) -> tuple[str, str]:
    """'D1@Sketch1' -> ('Sketch1', 'D1');  'Boss-Extrude1.depth' -> ('Boss-Extrude1', 'depth')."""
    t = target.strip().strip('"')
    if "@" in t:
        dim, owner = t.split("@", 1)
        return owner, dim
    if "." in t:
        owner, param = t.split(".", 1)
        return owner, param
    raise EquationError(f"cannot interpret {target!r}; use \"D1@Sketch1\", \"D1@Boss-Extrude1\" or Feature.param")


def feature_param_path(ftype: str, key: str) -> str:
    """Map 'D2' to a feature parameter path, or pass a parameter name through."""
    m = re.fullmatch(r"D(\d+)", key)
    if m:
        dims = FEATURE_DIMS.get(ftype, [])
        i = int(m.group(1)) - 1
        if not 0 <= i < len(dims):
            raise EquationError(f"{ftype} has no dimension {key}")
        return dims[i]
    return key


def get_path(d: dict, path: str):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def set_path(d: dict, path: str, value) -> None:
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def is_expr(v) -> bool:
    return isinstance(v, str) and v.strip().startswith("=")


class Evaluator:
    """Evaluates expressions against global variables and dimension values known so far in the rebuild."""

    def __init__(self, variables: dict[str, str]):
        self.values: dict[str, float] = {}  # "D1@Sketch1" -> value
        self.globals: dict[str, float] = {}
        pending = dict(variables)
        for _ in range(len(pending) + 1):  # resolve variables that refer to other variables
            progressed = False
            for name, expr in list(pending.items()):
                try:
                    self.globals[name] = self.eval(expr)
                    del pending[name]
                    progressed = True
                except EquationError:
                    continue
            if not pending or not progressed:
                break
        if pending:
            raise EquationError(f"cannot evaluate global variable(s) {sorted(pending)} (unknown names or a cycle)")

    def eval(self, expr) -> float:
        if isinstance(expr, (int, float)):
            return float(expr)
        src = str(expr).strip()
        if src.startswith("="):
            src = src[1:]
        refs: dict[str, str] = {}

        def sub(m):
            key = f"__r{len(refs)}"
            refs[key] = m.group(1)
            return key
        src = re.sub(r'"([^"]+)"', sub, src).replace("^", "**")  # SOLIDWORKS uses ^ for powers
        try:
            tree = ast.parse(src, mode="eval")
        except SyntaxError as exc:
            raise EquationError(f"bad expression {expr!r}") from exc
        return float(self._node(tree.body, refs))

    def _lookup(self, name: str) -> float:
        if name in self.globals:
            return self.globals[name]
        if name in self.values:
            return self.values[name]
        if name in _CONSTS:
            return _CONSTS[name]
        raise EquationError(f"unknown name {name!r} in equation")

    def _node(self, n, refs):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name):
            return self._lookup(refs.get(n.id, n.id))
        if isinstance(n, ast.BinOp):
            a, b = self._node(n.left, refs), self._node(n.right, refs)
            ops = {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}
            if type(n.op) in ops:
                return ops[type(n.op)]
            if isinstance(n.op, ast.Div):
                if b == 0:
                    raise EquationError("division by zero in equation")
                return a / b
            if isinstance(n.op, ast.Pow):
                return a ** b
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = self._node(n.operand, refs)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[self._node(a, refs) for a in n.args])
        if isinstance(n, ast.IfExp):  # iif-like: a if cond else b
            return self._node(n.body if self._node(n.test, refs) else n.orelse, refs)
        if isinstance(n, ast.Compare) and len(n.ops) == 1:
            a, b = self._node(n.left, refs), self._node(n.comparators[0], refs)
            return {ast.Lt: a < b, ast.Gt: a > b, ast.LtE: a <= b, ast.GtE: a >= b, ast.Eq: a == b,
                    ast.NotEq: a != b}[type(n.ops[0])]
        raise EquationError("unsupported element in equation (allowed: numbers, names, + - * / ^, functions)")
