"""Tracing a picture into sketch outlines (sketch_from_image)."""
import math

import pytest

from openswx import session as S

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")


def picture(tmp_path, name="plate.png"):
    """400 x 200 px: a black 300 x 120 plate with a round hole of radius 30, on white."""
    im = Image.new("L", (400, 200), 255)
    d = ImageDraw.Draw(im)
    d.rectangle([50, 40, 349, 159], fill=0)
    d.ellipse([170, 70, 229, 129], fill=255)
    p = tmp_path / name
    im.save(p)
    return str(p)


def test_trace_plate_with_hole(tmp_path):
    path = picture(tmp_path)
    S.new_part("Traced")
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_from_image(sk, path, width=200)["result"]  # 0.5 mm per pixel
    assert r["scale_mm_per_pixel"] == 0.5 and r["size"] == [200, 100] and r["loops"] == 2
    assert r["lines"] < 80  # the rectangle is four lines; the circle a few dozen
    S.extrude(sk, 2)
    area = 150 * 60 - math.pi * 15 ** 2  # mm2: the plate less the hole
    assert S.get_mass_properties()["volume"] == pytest.approx(area * 2, rel=0.01)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][0] - bb["min"][0] == pytest.approx(150, abs=1.0)
    assert bb["min"][0] == pytest.approx(25, abs=1.0)  # 50 px from the left edge

    # invert: the hole alone is not dark, so tracing the light shapes gives the background frame and the disc
    S.new_part("Inverted")
    sk = S.create_sketch("Top")["sketch"]
    inv = S.sketch_from_image(sk, path, width=200, invert=True, position=[10, 5])["result"]
    assert inv["loops"] >= 2 and any(a == pytest.approx(math.pi * 225, rel=0.03) for a in inv["areas"])
    # a looser tolerance gives fewer lines
    S.new_part("Loose")
    sk = S.create_sketch("Top")["sketch"]
    assert S.sketch_from_image(sk, path, width=200, tolerance=2.0)["result"]["lines"] < r["lines"]


def test_trace_refusals(tmp_path):
    S.new_part("Refuse")
    sk = S.create_sketch("Top")["sketch"]
    white = tmp_path / "white.png"
    Image.new("L", (50, 50), 255).save(white)
    with pytest.raises(S.CommandError, match="nothing to trace"):
        S.sketch_from_image(sk, str(white), width=50)
    notpic = tmp_path / "x.png"
    notpic.write_text("not a picture")
    with pytest.raises(S.CommandError, match="cannot read the picture"):
        S.sketch_from_image(sk, str(notpic), width=50)
    with pytest.raises(S.CommandError):
        S.sketch_from_image(sk, picture(tmp_path), width=0)
    with pytest.raises(S.CommandError, match="limit"):
        S.sketch_from_image(sk, picture(tmp_path), width=200, max_segments=10)
