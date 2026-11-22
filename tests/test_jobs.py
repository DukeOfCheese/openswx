"""Background jobs: long commands run on a worker thread while other commands keep working."""
import math
import time

import pytest

from openswx import jobs
from openswx import session as S


def ball():
    S.new_part("Ball")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_centerline(sk, [0, -8], [0, 8])
    arc = S.sketch_arc(sk, [0, 0], [0, -5], [0, 5])["result"]["points"]
    S.sketch_line(sk, arc["end"], arc["start"])
    S.revolve(sk)


def wait(jid, seconds=120):
    t0 = time.time()
    while time.time() - t0 < seconds:
        st = S.job_status(jid)
        if st["state"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_quick_job_returns_its_result():
    S.new_part("J")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 10)
    S.extrude(sk, 5)
    j = S.start_job("get_mass_properties")
    st = wait(j["id"])
    assert st["state"] == "done" and st["result"]["volume"] == pytest.approx(math.pi * 100 * 5)
    bad = wait(S.start_job("extrude", {"sketch": "Nope", "depth": 1})["id"])
    assert bad["state"] == "error" and "unknown sketch" in bad["error"]
    assert {x["id"] for x in S.job_status()["jobs"]} >= {j["id"]}
    with pytest.raises(S.CommandError):
        S.start_job("no_such_command")
    with pytest.raises(S.CommandError):
        S.job_status("job999999")


def test_other_commands_run_while_a_flow_solve_is_in_progress():
    ball()
    nu = 1.81e-5 / 1.204
    j = S.start_job("cfd_external", {"speed": 20 * nu / 0.010, "cells_across": 10, "max_seconds": 120})
    answered, progressed = 0, False
    t0 = time.time()
    while time.time() - t0 < 120:
        st = S.job_status(j["id"])
        if st["state"] != "running":
            break
        t = time.time()
        assert S.get_mass_properties()["volume"] > 0  # the lock is free: this answers at once
        assert time.time() - t < 5
        answered += 1
        progressed = progressed or (st["progress"] or 0) > 0
        time.sleep(0.1)
    st = S.job_status(j["id"])
    assert st["state"] == "done" and st["result"]["drag_coefficient"] > 1.5
    assert answered >= 2 and progressed
    assert S.APP.busy is None


def test_cancel_stops_a_flow_solve():
    ball()
    nu = 1.81e-5 / 1.204
    j = S.start_job("cfd_external", {"speed": 20 * nu / 0.010, "cells_across": 14, "max_seconds": 300})
    t0 = time.time()
    while time.time() - t0 < 60 and not (S.job_status(j["id"])["progress"] or 0) > 0:
        time.sleep(0.05)
    assert S.cancel_job(j["id"])["cancel_requested"]
    st = wait(j["id"], 60)
    assert st["state"] == "cancelled" and "result" not in st
    assert not S.cancel_job(j["id"])["cancel_requested"]  # already over
    assert jobs.get(j["id"])["result"] is None
