import { escapeHtml } from "../propman.js";
import { $ } from "../lib/dom.js";

const MIXIN = {
  sketchSel() { return this.selection.filter((s) => s.kind === "sketch").map((s) => s.id); },

  // a PropertyManager for a sketch tool: the entity list starts with the selected sketch entities
  sketchPM(spec) {
    if (!this.sk.active) return this.toast("Edit a sketch first.");
    const ents = this.sketchSel().map((id) => ({ kind: "skent", label: id, value: id }));
    this.sk.setTool("select"); this.renderCommandManager();
    this.openPM(spec, { initial: { entities: ents, ...(spec.initial ? spec.initial(this) : {}) } });
  },

  skSpec(o) {
    const S = () => this.sk.name, ids = (v, k = "entities") => (v[k] || []).map((i) => i.value);
    const ent = { title: "Entities", fields: [{ key: "entities", type: "selection", accepts: ["skent"], label: "Sketch entities" }] };
    const specs = {
      offset: { title: "Offset Entities", icon: "offset", command: "sketch_offset", groups: [
        { title: "Parameters", fields: [{ key: "distance", type: "number", label: "Offset distance", icon: "offset", value: 5, step: 0.5 },
          { key: "reverse", type: "check", label: "Reverse", value: false }, { key: "bidirectional", type: "check", label: "Bi-directional", value: false }] }, ent],
        args: (v) => ({ sketch: S(), entities: ids(v), distance: v.distance, reverse: !!v.reverse, bidirectional: !!v.bidirectional }),
        message: "Select the entities to offset (Ctrl+click, or box-select) and set the distance." },
      mirror: { title: "Mirror Entities", icon: "sk_mirror", command: "sketch_mirror", groups: [
        { title: "Options", fields: [{ key: "entities", type: "selection", accepts: ["skent"], label: "Entities to mirror" },
          { key: "copy", type: "check", label: "Copy", value: true },
          { key: "about", type: "selection", accepts: ["skline"], single: true, label: "Mirror about (a line or centerline)" }] }],
        args: (v) => ({ sketch: S(), entities: ids(v).filter((x) => x !== v.about?.[0]?.value), about: v.about?.[0]?.value, copy: v.copy !== false }),
        message: "Select the entities to mirror, then the line to mirror about." },
      linear: { title: "Linear Sketch Pattern", icon: "sk_linear_pattern", command: "sketch_linear_pattern", groups: [
        { title: "Direction 1", fields: [{ key: "spacing", type: "number", label: "Spacing", icon: "depth", value: 20, step: 1 },
          { key: "count", type: "number", label: "Instances", value: 3, step: 1 }, { key: "angle", type: "number", label: "Angle (°)", value: 0, step: 5 }] },
        { title: "Direction 2", check: "use_dir2", value: false, fields: [{ key: "spacing2", type: "number", label: "Spacing", icon: "depth", value: 20, step: 1 },
          { key: "count2", type: "number", label: "Instances", value: 2, step: 1 }] }, ent],
        args: (v) => ({ sketch: S(), entities: ids(v), spacing: v.spacing, count: Math.max(1, Math.round(v.count)), angle: v.angle || 0,
          spacing2: v.use_dir2 ? v.spacing2 : 0, count2: v.use_dir2 ? Math.max(1, Math.round(v.count2)) : 1, angle2: (v.angle || 0) + 90 }),
        message: "Select the entities to pattern and set the spacing and instances." },
      circular: { title: "Circular Sketch Pattern", icon: "sk_circular_pattern", command: "sketch_circular_pattern", groups: [
        { title: "Parameters", fields: [{ key: "center", type: "selection", accepts: ["skpoint"], single: true, label: "Center point (default: Origin)" },
          { key: "count", type: "number", label: "Instances", value: 6, step: 1 }, { key: "angle", type: "number", label: "Total angle (°)", value: 360, step: 15 }] }, ent],
        args: (v) => ({ sketch: S(), entities: ids(v), center: v.center?.[0]?.value || "ORIGIN", count: Math.max(2, Math.round(v.count)), angle: v.angle }),
        message: "Select the entities to pattern and the centre point." },
      move: { title: "Move / Copy Entities", icon: "sk_linear_pattern", command: "sketch_transform", groups: [ent,
        { title: "Parameters", fields: [{ key: "dx", type: "number", label: "Delta X", value: 10, step: 1 }, { key: "dy", type: "number", label: "Delta Y", value: 0, step: 1 },
          { key: "copy", type: "check", label: "Copy", value: false }] }],
        args: (v) => ({ sketch: S(), entities: ids(v), move: [v.dx || 0, v.dy || 0], copy: !!v.copy }), message: "Select entities and set the move." },
      rotate: { title: "Rotate Entities", icon: "sk_circular_pattern", command: "sketch_transform", groups: [ent,
        { title: "Parameters", fields: [{ key: "center", type: "selection", accepts: ["skpoint"], single: true, label: "Center of rotation (default: Origin)" },
          { key: "angle", type: "number", label: "Angle (°)", value: 90, step: 15 }, { key: "copy", type: "check", label: "Copy", value: false }] }],
        args: (v) => ({ sketch: S(), entities: ids(v), rotate: v.angle || 0, center: v.center?.[0]?.value || "ORIGIN", copy: !!v.copy }), message: "Select entities, the centre and the angle." },
      scale: { title: "Scale Entities", icon: "offset", command: "sketch_transform", groups: [ent,
        { title: "Parameters", fields: [{ key: "center", type: "selection", accepts: ["skpoint"], single: true, label: "Scale about (default: Origin)" },
          { key: "factor", type: "number", label: "Scale factor", value: 2, step: 0.1 }, { key: "copy", type: "check", label: "Copy", value: false }] }],
        args: (v) => ({ sketch: S(), entities: ids(v), scale: v.factor || 1, center: v.center?.[0]?.value || "ORIGIN", copy: !!v.copy }), message: "Select entities, the point and the factor." },
      convert: { title: "Convert Entities", icon: "convert", command: "sketch_convert_entities", groups: [
        { title: "Entities to Convert", fields: [{ key: "items", type: "selection", accepts: ["edge", "face"], label: "Model edges, faces" }] }],
        args: (v) => ({ sketch: S(), edges: (v.items || []).filter((i) => i.kind === "edge").map((i) => i.value), faces: (v.items || []).filter((i) => i.kind === "face").map((i) => i.value) }),
        initial: (app) => ({ items: app.selection.filter((x) => x.kind === "edge" || x.kind === "face").map((x) => ({ kind: x.kind, label: x.id, value: x.id })) }),
        message: "Select model edges or faces to project onto the sketch plane." },
    };
    return specs[o];
  },

  sketchTransformDialog(kind) { return this.sketchPM(this.skSpec(kind)); },

  sketchOffset() { return this.sketchPM(this.skSpec("offset")); },

  sketchMirror() { // the last selected line, if any, is what to mirror about
    if (!this.sk.active) return this.toast("Edit a sketch first.");
    const sel = this.sketchSel(), E = this.sk.detail.sketch.entities;
    const about = [...sel].reverse().find((id) => E[id]?.type === "line" && E[id]?.construction) || (sel.length > 1 ? [...sel].reverse().find((id) => E[id]?.type === "line") : null);
    const spec = this.skSpec("mirror");
    this.sk.setTool("select"); this.renderCommandManager();
    this.openPM(spec, { initial: { entities: sel.filter((id) => id !== about).map((id) => ({ kind: "skent", label: id, value: id })),
      ...(about ? { about: [{ kind: "skline", label: about, value: about }] } : {}) } });
  },

  sketch3dDialog(name = null) { // 3D Sketch: one chain of [x, y, z] points per segment, blank line between segments
    const segs = name ? (this.model.features.find((f) => f.name === name)?.params?.segments || []) : [];
    const text = segs.map((g) => g.points.map((q) => q.join(", ")).join("\n")).join("\n\n") || "0, 0, 0\n0, 0, 50\n0, 40, 50";
    const kind = segs[0]?.kind || "line";
    this.modal(name ? `Edit ${name}` : "3D Sketch", `<div class="field"><label>Points (x, y, z per line; blank line starts a new segment)</label>
      <textarea id="s3p" rows="9" style="width:100%;font:12px monospace">${escapeHtml(text)}</textarea></div>
      <div class="field"><label>Segment type</label><select id="s3k"><option value="line">Line chain</option><option value="spline">Spline</option></select></div>
      <label class="pm-check"><input id="s3c" type="checkbox"> Closed</label>`, async () => {
      const groups = $("#s3p").value.trim().split(/\n\s*\n/).map((g) => g.split("\n").map((l) => l.split(/[,\s]+/).filter(Boolean).map(Number)).filter((r) => r.length === 3 && r.every((x) => !isNaN(x))));
      const k = $("#s3k").value, closed = $("#s3c").checked;
      if (name) {
        await this.run("edit_feature", { name, params: { segments: groups.map((points) => ({ points, kind: k, closed })) } });
      } else {
        const r = await this.run("create_3d_sketch", { points: groups[0], kind: k, closed });
        const sk = r?.result?.sketch;
        for (const g of groups.slice(1)) await this.run("add_3d_sketch_segment", { sketch: sk, points: g, kind: k, closed });
      }
    }, "sketch3d");
    setTimeout(() => { const k = $("#s3k"); if (k) k.value = kind; });
  },

  sketchPattern(kind) { return this.sketchPM(this.skSpec(kind === "linear" ? "linear" : "circular")); },

  convertEntities() { return this.sketchPM(this.skSpec("convert")); },

  async intersectionCurve() {
    if (!this.sk.active) return this.toast("Edit a sketch whose plane cuts the body.");
    await this.run("sketch_intersection_curve", { sketch: this.sk.name });
  },

  // Sketch Fillet / Chamfer, as in SOLIDWORKS: set the size, then click corners (or two entities); the tool stays on
  sketchChamfer() { this.sketchCornerTool("sk_chamfer"); },

  sketchFillet() { this.sketchCornerTool("sk_fillet"); },

  sketchCornerTool(tool) {
    if (!this.sk.active) return this.toast("Edit a sketch first.");
    const fillet = tool === "sk_fillet", corner = this.selection.find((s) => s.kind === "skpoint");
    this.sk.openModify(this.vp.w / 2, this.vp.h / 2, fillet ? "Fillet radius" : "Chamfer distance", fillet ? this.skFilletR : this.skChamferD, async (v) => {
      if (fillet) this.skFilletR = v; else this.skChamferD = v;
      if (corner && corner.id !== "ORIGIN") await this.sketchCorner(tool, corner.id);
      this.setSketchTool(tool);
    });
  },

  sketchCorner(tool, point) {
    return tool === "sk_fillet" ? this.run("sketch_fillet", { sketch: this.sk.name, point, radius: this.skFilletR })
      : this.run("sketch_chamfer", { sketch: this.sk.name, point, distance: this.skChamferD });
  },
};

export function installSketchPm(App) {
  Object.assign(App.prototype, MIXIN);
}
