"""Rebuild a SOLIDWORKS part's feature history in openswx, without SOLIDWORKS (docs/PLAN.md, milestone F5).

The feature tree, dimensions and end conditions come from sw_tree, the sketches from sw_sketch. What the
stream does not (yet) give away, such as the side an extrusion goes to or the edges a fillet rounds, is
settled against the exact Parasolid body stored in the same file: each candidate is scored by how well the
body it produces agrees with the stored one. The rebuilt part is only accepted when its volume and area
match the stored body.
"""
from __future__ import annotations

from openswx.core.document import DocumentError, PartDocument

from ._base import (FIXED_BASE, MATCH, NEUTRAL, SKIP, STD_PLANES, TOL, RebuildError, _AVOID, _BASE_TRIALS,
                    _DEADLINE, _LATER_SCALE, _LUMP, _MOVED_LATER, _Oracle, _PROGRESS, _REVOLVE_AXES, _TAGGED, _TRUST,
                    _body_key, _body_volume, _check_time, _exact, _faces_on_body, _import_delta, _inside_mesh, _log,
                    _lump, _mesh_volume, _plane, _tagged_faces, _tagged_ok, _trial, _triangles, _try, _try_kinds,
                    _volume)
from .features import (_draft_variants, _fillet_radii, _helix, _hole_variants, _off_boundary_edges, _scale_bytes,
                       _scale_factor)
from .features_weld import _path_lines, _weld_arcs, _weld_member
from .patterns import _try_pattern
from .rebuild import _rebuild, _rebuild_worker, rebuild_isolated, rebuild_part
from .sketches import (_REL_TYPES, _around_origin, _chords, _closing_arcs, _consume, _decoded_relations, _edges_sketch,
                       _half_sections, _loose_axes, _orient_arcs, _reach, _revolve_angles, _section_sketches,
                       _settle_arcs, _sketch_dict, _sketch_dimensions)

__all__ = [
    "STD_PLANES", "SKIP", "FIXED_BASE", "NEUTRAL", "TOL", "MATCH", "RebuildError",
    "DocumentError", "PartDocument",
    "_PROGRESS", "_AVOID", "_TRUST", "_REVOLVE_AXES", "_DEADLINE", "_LUMP", "_TAGGED", "_MOVED_LATER",
    "_LATER_SCALE", "_log", "_BASE_TRIALS", "_check_time",
    "_sketch_dict", "_REL_TYPES", "_decoded_relations", "_sketch_dimensions", "_mesh_volume", "_triangles",
    "_inside_mesh", "_Oracle", "_try", "_try_pattern", "_volume", "_exact", "_tagged_ok", "_trial", "_body_volume",
    "_try_kinds", "_orient_arcs", "_reach", "_loose_axes", "_revolve_angles", "_scale_bytes", "_scale_factor",
    "_edges_sketch", "_section_sketches", "_half_sections", "_consume", "_draft_variants", "_path_lines",
    "_weld_member", "_weld_arcs", "_around_origin", "_fillet_radii", "_closing_arcs", "_helix", "_chords",
    "_faces_on_body", "_tagged_faces", "_import_delta", "_body_key", "_lump", "_hole_variants", "_plane",
    "_settle_arcs", "_off_boundary_edges", "rebuild_part", "_rebuild", "_rebuild_worker", "rebuild_isolated",
]
