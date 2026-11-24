"""CAM 3D parallel finishing: the tool rides on the part without cutting into it (ball, bull nose, flat)."""
import math

import numpy as np
import pytest

from openswx.manufacturing import cam
from openswx import session as S


def test_drop_cutter_on_known_surfaces():
    cell = 0.25
    flat = np.zeros((60, 60))
    for kind in ("flat", "ball", "bull"):
        assert np.allclose(cam.drop_cutter(flat, cell, kind, 3.0, 1.0), 0.0)  # on a flat floor the tip sits on it
    # a step of height 5 at x = 7.5: next to the wall a ball tool is held up by the edge
    step = np.zeros((60, 60))
    step[30:, :] = 5.0
    z = cam.drop_cutter(step, cell, "ball", 3.0)[:, 30]
    assert z[35] == pytest.approx(5.0) and z[10] == pytest.approx(0.0)
    d = (30 - 26) * cell  # the axis 1 mm before the edge: lifted so the ball just touches the corner
    assert z[26] == pytest.approx(5.0 - (3.0 - math.sqrt(9.0 - d * d)), abs=1e-9)
    # a flat tool there stands fully on top; a bull nose (corner 1) is in between
    assert cam.drop_cutter(step, cell, "flat", 3.0)[26, 30] == pytest.approx(5.0)
    assert z[26] < cam.drop_cutter(step, cell, "bull", 3.0, 1.0)[26, 30] <= 5.0
    assert float(cam.tool_profile("ball", 3.0, 0.0, np.array(3.0))) == pytest.approx(3.0)
    assert float(cam.tool_profile("bull", 3.0, 1.0, np.array(1.5))) == 0.0


def test_ball_finish_follows_a_cylinder_without_gouging(tmp_path):
    # a half cylinder lying along Z (radius 15, length 40) on a 40 x 5 base: the top is curved in X
    S.new_part("Saddle")
    sk = S.create_sketch("Front")["sketch"]
    arc = S.sketch_arc(sk, [0, 0], [15, 0], [-15, 0])["result"]["points"]
    S.sketch_line(sk, arc["end"], arc["start"])
    S.extrude(sk, 40)
    assert S.get_mass_properties()["volume"] == pytest.approx(math.pi * 225 / 2 * 40, rel=1e-6)
    S.cam_generate(setup="+Y", tool_diameter=6, tolerance=0.25, drill=False)
    out = S.cam_finish_3d(tool_diameter=6, tool="ball", stepover=1.0, direction="x")
    op = out["operation"]
    assert op["kind"] == "finish3d" and op["scallop_mm"] == pytest.approx(3 - math.sqrt(9 - 0.25), abs=1e-5)
    assert out["tool"]["type"] == "ball end mill" and [o["kind"] for o in out["operations"]][-1] == "finish3d"

    # every cutting point: the ball's centre is at least its radius from the cylinder (no gouge), and on the
    # crown the tip is on the surface
    r = S.APP.cam_result[2]
    grid, part_H, _ = r["_raster"]
    moves = r["operations"][-1]["moves"]
    feed = np.array([p for kind, p in moves if kind in ("feed", "plunge")])
    assert len(feed) > 200
    top = float(part_H[np.isfinite(part_H)].max())  # the crown, in CAM coordinates (z = 0 is the stock top)
    ii, jj = np.nonzero(part_H >= top - 1e-9)
    axis_a = float(np.ptp(grid.xs[ii]))  # the crown is a line: it runs along whichever axis it spans
    along_x = axis_a > 10
    centre = float(np.mean(grid.ys[jj])) if along_x else float(np.mean(grid.xs[ii]))
    across = feed[:, 1] - centre if along_x else feed[:, 0] - centre
    dist = np.hypot(across, feed[:, 2] + 3.0 - (top - 15.0))  # ball centre to the cylinder's axis
    along = feed[:, 0] if along_x else feed[:, 1]
    ends = (grid.xs[ii] if along_x else grid.ys[jj])
    over = (np.abs(across) < 15) & (along > ends.min() + 3.5) & (along < ends.max() - 3.5)  # passes over the cylinder itself
    assert dist[over].min() >= 15 + 3 - 0.3  # (the raster cell is the precision)
    on_crown = over & (np.abs(across) < 0.2)
    assert on_crown.any() and np.abs(feed[on_crown][:, 2] - top).max() < 0.05
    assert feed[:, 2].max() <= top + 1e-9

    # the operation is in the G-code, with its own tool
    g = S.cam_gcode("linuxcnc", str(tmp_path / "saddle.nc"))
    text = (tmp_path / "saddle.nc").read_text()
    assert "ball end mill D6" in text and g["lines"] > 300
    assert S.cam_toolpaths()
    with pytest.raises(S.CommandError):
        S.cam_finish_3d(tool="bull", corner_radius=5)  # larger than the tool radius
    with pytest.raises(S.CommandError):
        S.cam_finish_3d(stepover=20)
    S.new_part("NoCam")
    with pytest.raises(S.CommandError, match="generate the toolpaths first"):
        S.cam_finish_3d()
