import json
import sys
from pathlib import Path

import pytest

from openswx import session as S

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import conformance  # noqa: E402

CASES = sorted((ROOT / "tests" / "conformance").glob("*.json"))


def test_there_are_cases():
    assert len(CASES) >= 7


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_case_conforms(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    assert case["kind"] == "part" and case["commands"] and case["description"]
    assert all(c["name"] in S.COMMANDS for c in case["commands"]), "a case uses a command that no longer exists"
    assert conformance.run_case(case, S) == []


def test_a_wrong_expectation_is_reported():
    case = json.loads(CASES[0].read_text(encoding="utf-8"))
    case["expect"]["volume"] *= 1.01
    case["expect"]["faces"] += 1
    problems = conformance.run_case(case, S)
    assert len(problems) == 2 and problems[0].startswith("volume") and problems[1].startswith("faces")
    case["commands"].append({"name": "extrude", "args": {"sketch": "Nope"}})
    assert conformance.run_case(case, S)[0].startswith(f"command {len(case['commands'])} (extrude) failed")
