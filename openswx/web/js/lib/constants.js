// Feature-table lookups shared by the App and its mixins. SPECS and these tables gain the
// entries that more.js adds for the surface, sheet metal, weldment and analysis features.
import { SPECS } from "../propman.js";
import { MORE_FEATURE_TO_SPEC, MORE_SPECS, MORE_TYPE_ICON } from "../more.js";

export const TYPE_ICON = { sketch: "sketch_item", extrude: "extrude", cut_extrude: "cut_extrude", revolve: "revolve", cut_revolve: "cut_revolve",
  fillet: "fillet", variable_fillet: "fillet", chamfer: "chamfer", shell: "shell", hole: "hole", linear_pattern: "linear_pattern", circular_pattern: "circular_pattern",
  sketch_pattern: "sketch_pattern", curve_pattern: "curve_pattern", table_pattern: "table_pattern", fill_pattern: "fill_pattern", mirror: "mirror", plane: "plane_item", ref_axis: "ref_axis", ref_point: "ref_point", csys: "csys", sketch3d: "sketch3d", helix: "helix", hem: "hem", rip: "rip", vent: "vent", unfold: "unfold", fold: "fold", sketched_bend: "sketched_bend",
  revolved_surface: "revolved_surface", swept_surface: "swept_surface", lofted_surface: "lofted_surface", boundary_surface: "boundary_surface", planar_surface: "planar_surface",
  filled_surface: "filled_surface", offset_surface: "offset_surface", knit_surface: "knit_surface", trim_surface: "trim_surface", end_cap: "end_cap", gusset: "gusset", vertex_chamfer: "vertex_chamfer", face_fillet: "face_fillet",
  full_round_fillet: "full_round_fillet", wrap: "wrap", jog: "jog", corner_relief: "corner_relief", dimple: "dimple", miter_flange: "miter_flange", louver: "louver", lance: "lance", extruded_flange: "extruded_flange", form_rib: "form_rib", auto_corner_relief: "corner_relief", import: "import", sweep: "sweep", cut_sweep: "cut_sweep", loft: "loft", cut_loft: "cut_loft",
  draft: "draft_feat", base_flange: "base_flange", edge_flange: "edge_flange", extruded_surface: "surface", thicken: "thicken",
  split: "cut_extrude", cut_with_surface: "cut_extrude", delete_face: "trash", move_face: "offset", replace_face: "surface",
  projected_curve: "intersection_curve", composite_curve: "sketch3d", xyz_curve: "spline", split_line: "intersection_curve", intersect: "knit_surface", indent: "dimple" };
export const FEATURE_TO_SPEC = { extrude: "extrude", cut_extrude: "cut_extrude", revolve: "revolve", cut_revolve: "cut_revolve", fillet: "fillet", variable_fillet: "fillet",
  chamfer: "chamfer", shell: "shell", hole: "hole", linear_pattern: "linear_pattern", circular_pattern: "circular_pattern", sketch_pattern: "sketch_pattern", curve_pattern: "curve_pattern", table_pattern: "table_pattern", fill_pattern: "fill_pattern", mirror: "mirror", plane: "plane", ref_axis: "ref_axis", ref_point: "ref_point", csys: "csys", helix: "helix",
  hem: "hem", rip: "rip", vent: "vent", unfold: "unfold", fold: "fold", sketched_bend: "sketched_bend", revolved_surface: "revolved_surface", swept_surface: "swept_surface", lofted_surface: "lofted_surface",
  planar_surface: "planar_surface", filled_surface: "filled_surface", offset_surface: "offset_surface", knit_surface: "knit_surface", trim_surface: "trim_surface",
  end_cap: "end_cap", gusset: "gusset", vertex_chamfer: "vertex_chamfer", face_fillet: "face_fillet", full_round_fillet: "full_round_fillet", wrap: "wrap",
  jog: "jog", corner_relief: "corner_relief", dimple: "dimple", miter_flange: "miter_flange", louver: "louver", lance: "lance",
  extruded_flange: "extruded_flange", form_rib: "form_rib",
  sweep: "sweep", cut_sweep: "cut_sweep", loft: "loft", cut_loft: "cut_loft", draft: "draft",
  base_flange: "base_flange", edge_flange: "edge_flange", extruded_surface: "extruded_surface", thicken: "thicken",
  rib: "rib", thread: "thread", dome: "dome", split: "split_body", cut_with_surface: "cut_with_surface", delete_face: "delete_face",
  move_face: "move_face", replace_face: "replace_face", projected_curve: "projected_curve", composite_curve: "composite_curve", xyz_curve: "xyz_curve", split_line: "split_line", intersect: "intersect", indent: "indent" };
export const CURVE_TYPES = ["sketch3d", "helix", "projected_curve", "composite_curve", "xyz_curve"];
// commands that can take minutes: started with start_job so the window stays responsive
export const LONG_JOBS = new Map([["cam_finish_3d", "3D finishing"], ["simulation_run", "Static study"], ["frequency_run", "Frequency study"], ["buckling_run", "Buckling study"],
  ["thermal_run", "Thermal study"], ["nonlinear_run", "Nonlinear study"], ["harmonic_run", "Harmonic study"], ["step_response_run", "Transient study"],
  ["drop_test_run", "Drop test"], ["cfd_external", "Flow simulation"], ["cfd_internal", "Flow simulation"], ["flow_simulation", "Flow simulation"],
  ["plastics_fill", "Fill analysis"], ["cam_generate", "CAM toolpaths"]]);

Object.assign(SPECS, MORE_SPECS);
Object.assign(TYPE_ICON, MORE_TYPE_ICON);
Object.assign(FEATURE_TO_SPEC, MORE_FEATURE_TO_SPEC);
