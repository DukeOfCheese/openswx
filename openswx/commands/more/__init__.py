"""More commands (registered with the same @command registry as session.py, which re-exports them): Flex /
Deform / Freeform, fillet profiles and FilletXpert, mold tools, Insert / Mirror Part, cosmetic threads, the
remaining surface tools, library features and the Design Library, Hole Series."""
from __future__ import annotations

from .assembly_tools import *
from .drawing_tools import *
from .evaluate import *
from .features import *
from .library import *
from .macros import *
from .mold import *
from .pdm import *
from .render import *
from .routing import *
from .studies import *
from .surfaces import *
from .viewer import export_edrawing

from .assembly_tools import _component_infos  # noqa: F401
from .common import openswx_home  # noqa: F401
from .routing import _sweep_circle, _virtual_component  # noqa: F401
from .viewer import viewer_payload, write_viewer  # noqa: F401

__all__ = ["flex", "deform", "freeform", "fillet_profile", "fillet_xpert", "parting_line",
           "parting_surface", "shut_off_surfaces", "tooling_split", "insert_part", "mirror_part",
           "cosmetic_thread", "ruled_surface", "radiate_surface", "extend_surface",
           "untrim_surface", "flatten_surface", "filled_surface_edges", "save_library_feature",
           "list_design_library", "insert_library_feature", "delete_library_item",
           "add_part_to_design_library", "hole_series", "smart_mate", "connection_point",
           "mate_reference", "magnetic_mate", "magnetic_snap", "apply_mate_references",
           "save_alternate_position", "show_alternate_position", "delete_alternate_position",
           "set_envelope", "set_speedpak", "large_assembly_mode", "defeature", "pack_and_go",
           "route_pipe", "route_cable", "weld_bead", "list_pipe_standards", "route_pipe_standard",
           "insert_fitting", "drawing_add_surface_finish", "drawing_add_weld_symbol",
           "drawing_add_stacked_balloon", "drawing_add_layer", "drawing_set_layer",
           "drawing_set_sheet_format", "drawing_save_sheet_format", "list_sheet_formats",
           "drawing_view_alternate_position", "nonlinear_run", "harmonic_run", "step_response_run",
           "drop_test_run", "fatigue_run", "fatigue_results", "flow_simulation", "flow_results",
           "cfd_external", "cfd_internal", "cfd_results", "costing", "sustainability_report",
           "dimxpert_auto", "dimxpert_clear", "set_render_scene", "add_decal", "delete_decal",
           "pdm_set_vault", "pdm_list", "pdm_check_in", "pdm_check_out", "pdm_undo_check_out",
           "pdm_get_version", "pdm_change_state", "pdm_history", "pdm_search", "pdm_where_used",
           "export_edrawing", "macro_start", "macro_stop", "macro_status", "run_macro"]
