"""3D-print orientation advisor, sheet-metal gauge tables, and a fuzz check of the file readers."""
import pytest

from openswx import session as S


def test_print_orientation_prefers_the_way_up_without_overhangs():
    # a T: a 40 x 10 x 10 bar on top of a 10 x 10 x 30 stem (the stem stands along Y, the sketch plane's normal)
    S.new_part("Tee")
    stem = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(stem, [-5, -5], [5, 5])
    S.extrude(stem, 30)
    top = next(f for f in S.get_topology()["faces"] if f["normal"][1] > 0.99)
    bar = S.create_sketch(top["name"])["sketch"]
    S.sketch_rectangle(bar, [-20, -5], [20, 5])
    S.extrude(bar, 10)
    assert S.get_mass_properties()["volume"] == pytest.approx(10 * 10 * 30 + 40 * 10 * 10)
    r = S.print_orientation()
    by = {o["up"]: o for o in r["orientations"]}
    # stem down (+Y up): the bar's two 15 x 10 undersides overhang, 30 above the bed
    assert by["+Y"]["overhang_area"] == pytest.approx(2 * 15 * 10, rel=1e-6)
    assert by["+Y"]["support_volume"] == pytest.approx(2 * 15 * 10 * 30, rel=1e-6)
    assert by["+Y"]["bed_contact_area"] == pytest.approx(100, rel=1e-6) and by["+Y"]["height"] == pytest.approx(40)
    # upside down (-Y up): the bar lies flat on the bed, nothing overhangs
    assert by["-Y"]["overhang_area"] == pytest.approx(0, abs=1e-6) and by["-Y"]["bed_contact_area"] == pytest.approx(400, rel=1e-6)
    # lying on its side (Z up or down) is better still: no overhang either, lower, and more of it on the bed
    assert r["best"] in ("+Z", "-Z") and by["+Z"]["height"] == pytest.approx(10) and by["+Z"]["bed_contact_area"] == pytest.approx(700)
    narrow = S.print_orientation(bed=[12, 45, 45])  # too narrow to lie flat: standing on the bar is the best that fits
    fits = {o["up"]: o["fits"] for o in narrow["orientations"]}
    assert fits["+Z"] is False and fits["-Y"] is True and narrow["best"] == "-Y"
    with pytest.raises(S.CommandError):
        S.print_orientation(overhang_angle=120)


def test_sheet_metal_gauges():
    assert S.sheet_metal_gauge("steel", 16)["thickness"] == 1.519
    assert S.sheet_metal_gauge("aluminum", 16)["thickness"] == 1.291  # the same number, another thickness
    assert S.sheet_metal_gauge("stainless", 10)["thickness_in"] == pytest.approx(0.1406, abs=1e-4)
    assert len(S.sheet_metal_gauge("galvanized")["gauges"]) >= 10
    with pytest.raises(S.CommandError):
        S.sheet_metal_gauge("steel", 15)
    S.new_part("G16")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [50, 30])
    S.base_flange(sk, thickness=S.sheet_metal_gauge("steel", 16)["thickness"])
    assert S.sheet_metal_info()["thickness"] == pytest.approx(1.519)


def test_damaged_files_are_refused_not_fatal(tmp_path, monkeypatch):
    """Truncated and bit-flipped copies of files openswx wrote itself: every reader either opens them or
    raises an error; none takes the application down. Parasolid and IGES are read in the child process, as the
    application does (a damaged IGES file crashes the kernel's reader: that must cost only the file)."""
    monkeypatch.setenv("OPENSWX_ISOLATE", "on")
    S.new_part("Fuzz")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [20, 10])
    S.sketch_circle(sk, [10, 5], 2)
    S.extrude(sk, 4)
    good = {}
    for ext in ("oswprt", "step", "stl", "x_t", "igs"):
        p = tmp_path / f"good.{ext}"
        S.save_part(str(p)) if ext == "oswprt" else S.export_file(str(p))
        good[ext] = p.read_bytes()
    tried = refused = 0
    for ext, data in good.items():
        # (a child process per file for the isolated formats: fewer cases of those)
        fracs = (0.5, 0.9) if ext in ("x_t", "igs") else (0.0, 0.1, 0.5, 0.9, 0.99)
        for k, frac in enumerate(fracs):
            for mode in ("cut", "flip"):
                n = int(len(data) * frac)
                bad = data[:n] if mode == "cut" else data[:n] + bytes([data[n] ^ 0x5A]) + data[n + 1:]
                f = tmp_path / f"bad_{ext}_{k}_{mode}.{ext}"
                f.write_bytes(bad)
                tried += 1
                try:
                    S.open_part(str(f))
                    S.close_document(S.APP.active)
                except S.CommandError:
                    refused += 1
                except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError, AttributeError, OSError, RuntimeError):
                    refused += 1  # (not wrapped as a command error yet, but neither a crash nor a hang)
    assert tried == 38 and refused >= 10
    S.open_part(str(tmp_path / "good.oswprt"))  # the application still works afterwards
    assert S.get_mass_properties()["volume"] > 0
