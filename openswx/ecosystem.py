"""Ecosystem tools: a file vault with check-in / check-out, versions, revisions and a release workflow (PDM),
a self-contained HTML viewer with markup for sharing models (eDrawings-style), and macro files."""
from __future__ import annotations

import datetime as _dt
import getpass
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

WORKFLOW = {"Work in Progress": ["Pending Approval"], "Pending Approval": ["Released", "Work in Progress"],
            "Released": ["Work in Progress"]}


class VaultError(ValueError):
    pass


# ====================================================================== PDM vault
def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name (service accounts)
        return "unknown"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _next_rev(rev: str | None) -> str:
    """A, B, ... Z, AA, AB ..."""
    if not rev:
        return "A"
    letters = list(rev)
    i = len(letters) - 1
    while i >= 0:
        if letters[i] != "Z":
            letters[i] = chr(ord(letters[i]) + 1)
            return "".join(letters)
        letters[i] = "A"
        i -= 1
    return "A" + "".join(letters)


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise VaultError(f"bad file name {name!r}")
        return self.root / name

    def meta(self, name: str) -> dict:
        p = self._dir(name) / "meta.json"
        if not p.is_file():
            raise VaultError(f"{name!r} is not in the vault")
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, m: dict) -> None:
        (self._dir(m["name"]) / "meta.json").write_text(json.dumps(m, indent=1), encoding="utf-8")

    def files(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            if (d / "meta.json").is_file():
                m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                last = m["versions"][-1] if m["versions"] else {}
                out.append({"name": m["name"], "version": last.get("version"), "revision": m.get("revision"),
                            "state": m["state"], "checked_out": m.get("checked_out"), "modified": last.get("time")})
        return out

    def check_in(self, src: Path, comment: str = "", keep_checked_out: bool = False) -> dict:
        src = Path(src)
        if not src.is_file():
            raise VaultError(f"no file {str(src)!r}")
        name = src.name
        d = self._dir(name)
        if (d / "meta.json").is_file():
            m = self.meta(name)
            co = m.get("checked_out")
            if not co:
                raise VaultError(f"check {name} out before checking in a change")
            if co["user"] != _user():
                raise VaultError(f"{name} is checked out by {co['user']}")
            if m["state"] == "Released":
                raise VaultError(f"{name} is released: change its state to Work in Progress first")
        else:
            d.mkdir(parents=True)
            m = {"name": name, "versions": [], "checked_out": None, "state": "Work in Progress", "revision": None,
                 "history": []}
        sha = _sha(src)
        if m["versions"] and m["versions"][-1]["sha256"] == sha:
            added = None  # unchanged: nothing new to store
        else:
            v = len(m["versions"]) + 1
            stored = d / f"v{v}{src.suffix}"
            shutil.copy2(src, stored)
            added = {"version": v, "file": stored.name, "comment": comment, "user": _user(), "time": time.time(),
                     "sha256": sha, "size": stored.stat().st_size}
            m["versions"].append(added)
            m["history"].append({"event": "check in", "version": v, "user": _user(), "time": time.time(), "comment": comment})
        m["checked_out"] = {"user": _user(), "path": str(src.resolve()), "time": time.time()} if keep_checked_out else None
        self._save(m)
        return {"name": name, "version": m["versions"][-1]["version"], "stored": added is not None, "state": m["state"]}

    def check_out(self, name: str, dest_dir: Path) -> dict:
        m = self.meta(name)
        co = m.get("checked_out")
        if co and co["user"] != _user():
            raise VaultError(f"{name} is checked out by {co['user']}")
        if m["state"] == "Released":
            raise VaultError(f"{name} is released: change its state to Work in Progress first")
        dest = self.get(name, None, dest_dir)
        m = self.meta(name)
        m["checked_out"] = {"user": _user(), "path": str(dest), "time": time.time()}
        m["history"].append({"event": "check out", "user": _user(), "time": time.time()})
        self._save(m)
        return {"name": name, "path": str(dest), "version": m["versions"][-1]["version"]}

    def undo_check_out(self, name: str) -> dict:
        m = self.meta(name)
        co = m.get("checked_out")
        if not co:
            raise VaultError(f"{name} is not checked out")
        if co["user"] != _user():
            raise VaultError(f"{name} is checked out by {co['user']}")
        m["checked_out"] = None
        m["history"].append({"event": "undo check out", "user": _user(), "time": time.time()})
        self._save(m)
        return {"name": name}

    def get(self, name: str, version: int | None, dest_dir: Path) -> Path:
        """Copy a version (default the latest) out to dest_dir under the file's name."""
        m = self.meta(name)
        if not m["versions"]:
            raise VaultError(f"{name} has no versions")
        vs = {v["version"]: v for v in m["versions"]}
        v = vs.get(version) if version is not None else m["versions"][-1]
        if v is None:
            raise VaultError(f"{name} has versions 1..{len(vs)}")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        shutil.copy2(self._dir(name) / v["file"], dest)
        return dest.resolve()

    def change_state(self, name: str, state: str, comment: str = "") -> dict:
        m = self.meta(name)
        if state not in WORKFLOW.get(m["state"], []):
            raise VaultError(f"{name} is {m['state']}: it can go to {', '.join(WORKFLOW[m['state']])}")
        if m.get("checked_out") and state != "Work in Progress":
            raise VaultError(f"{name} is checked out: check it in first")
        m["state"] = state
        if state == "Released":
            m["revision"] = _next_rev(m.get("revision"))
            m["versions"][-1]["revision"] = m["revision"]
        m["history"].append({"event": f"state: {state}", "user": _user(), "time": time.time(), "comment": comment})
        self._save(m)
        return {"name": name, "state": state, "revision": m.get("revision")}

    def history(self, name: str) -> dict:
        m = self.meta(name)
        return {"name": name, "state": m["state"], "revision": m.get("revision"), "checked_out": m.get("checked_out"),
                "versions": m["versions"], "history": m["history"]}

    def search(self, text: str) -> list[dict]:
        """Files whose name, check-in comments or (openswx documents) custom properties contain `text`."""
        t = text.lower()
        out = []
        for f in self.files():
            m = self.meta(f["name"])
            hay = [m["name"]] + [v.get("comment", "") for v in m["versions"]]
            latest = self._dir(m["name"]) / m["versions"][-1]["file"] if m["versions"] else None
            if latest and latest.suffix in (".oswprt", ".oswasm"):
                try:
                    props = json.loads(latest.read_text(encoding="utf-8")).get("properties", {})
                    hay += [f"{k} {v}" for k, v in props.items()]
                except (OSError, ValueError):
                    pass
            if any(t in h.lower() for h in hay):
                out.append(f)
        return out

    def where_used(self, name: str) -> list[str]:
        """Assemblies in the vault whose latest version refers to the file."""
        self.meta(name)
        out = []
        for f in self.files():
            if not f["name"].endswith(".oswasm") or f["name"] == name:
                continue
            m = self.meta(f["name"])
            data = (self._dir(f["name"]) / m["versions"][-1]["file"]).read_text(encoding="utf-8")
            if re.search(r'"link":\s*"[^"]*[\\/]?' + re.escape(name) + '"', data) or f'"{name}"' in data:
                out.append(f["name"])
        return out


# ====================================================================== macros
def macro_source(entries: list[dict]) -> str:
    """A Python macro replaying recorded commands through the openswx API."""
    lines = [f'"""openswx macro, recorded {_dt.datetime.now():%Y-%m-%d %H:%M}. Run it with Tools > Macro > Run (or',
             'headless with `python <file>`); edit it like any Python script: S is the openswx API."""',
             "from openswx import session as S", ""]
    for e in entries:
        args = ", ".join(f"{k}={v!r}" for k, v in e["args"].items())
        lines.append(f"S.{e['command']}({args})")
    return "\n".join(lines) + "\n"


# ====================================================================== eDrawings-style HTML
def viewer_html(title: str, payload: dict, three_js: str) -> str:
    """A single-file HTML viewer (three.js inlined): orbit / pan / zoom, standard views, component visibility,
    section plane, measure, and markup (pen, notes) that can be saved as a PNG and JSON."""
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    three = three_js.replace("</script", "<\\/script")
    esc = title.replace("&", "&amp;").replace("<", "&lt;")
    return VIEWER.replace("__TITLE__", esc).replace("__DATA__", data).replace("__THREE__", three)


VIEWER = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f4f5f7;--panel:#fff;--ink:#1d2330;--muted:#5d6675;--line:#d8dce3;--accent:#2563eb;--mark:#e11d48}
@media (prefers-color-scheme:dark){:root{--bg:#16181d;--panel:#1f232b;--ink:#e6e8ec;--muted:#9aa3b2;--line:#323844;--accent:#60a5fa;--mark:#fb7185}}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:13px system-ui,sans-serif}
#bar{position:fixed;top:0;left:0;right:0;height:44px;display:flex;gap:4px;align-items:center;padding:0 10px;background:var(--panel);border-bottom:1px solid var(--line);z-index:3;overflow-x:auto}
#bar b{margin-right:10px;white-space:nowrap}#bar button{border:1px solid var(--line);background:transparent;color:var(--ink);padding:5px 9px;border-radius:5px;cursor:pointer;white-space:nowrap}
#bar button.on{background:var(--accent);color:#fff;border-color:var(--accent)}#bar .sep{width:1px;height:24px;background:var(--line);margin:0 4px}
#side{position:fixed;top:44px;left:0;bottom:0;width:230px;background:var(--panel);border-right:1px solid var(--line);overflow:auto;padding:10px;z-index:2}
#side h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:12px 0 6px}#side label{display:flex;gap:6px;align-items:center;padding:2px 0}
#side table{width:100%;border-collapse:collapse}#side td{padding:2px 0;vertical-align:top}#side td:first-child{color:var(--muted);padding-right:6px}
#view{position:fixed;top:44px;left:230px;right:0;bottom:0}#view canvas{display:block}#ink{position:absolute;inset:0;pointer-events:none}
#status{position:fixed;right:10px;bottom:10px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 10px;z-index:3}
.note{position:absolute;background:#fff8c5;color:#1d2330;border:1px solid #d4a72c;border-radius:4px;padding:3px 6px;font-size:12px;transform:translate(-50%,-100%);white-space:pre}
.pmi{position:absolute;background:var(--panel);border:1px solid var(--accent);color:var(--accent);border-radius:3px;padding:1px 4px;font-size:11px;transform:translate(-50%,-50%);white-space:nowrap;pointer-events:none}
@media (max-width:700px){#side{display:none}#view{left:0}}
</style></head><body>
<div id="bar"><b>__TITLE__</b>
<button data-v="iso">Iso</button><button data-v="front">Front</button><button data-v="top">Top</button><button data-v="right">Right</button><button id="fit">Fit</button>
<span class="sep"></span><button id="sec">Section</button><button id="meas">Measure</button>
<span id="stepbar" hidden><span class="sep"></span><button id="sprev">&#9664;</button><button id="splay">Play</button><button id="snext">&#9654;</button></span>
<span class="sep"></span><button id="pen">Pen</button><button id="txt">Note</button><button id="clr">Clear markup</button><button id="png">Save image</button><button id="js">Save markup</button></div>
<div id="side"><div id="steps"></div><div id="props"></div><div id="comps"></div><div id="secbox" hidden><h3>Section</h3>
<label><select id="secax"><option>X</option><option>Y</option><option selected>Z</option></select><input id="secpos" type="range" min="0" max="1000" value="500" style="flex:1"></label></div></div>
<div id="view"><canvas id="ink"></canvas></div><div id="status">Drag: rotate · Right-drag: pan · Wheel: zoom</div>
<script type="text/plain" id="three-src">__THREE__</script>
<script type="application/json" id="model">__DATA__</script>
<script type="module">
const THREE = await import(URL.createObjectURL(new Blob([document.getElementById("three-src").textContent], {type: "text/javascript"})));
const M = JSON.parse(document.getElementById("model").textContent);
const view = document.getElementById("view"), ink = document.getElementById("ink"), status = document.getElementById("status");
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
renderer.localClippingEnabled = true;
view.prepend(renderer.domElement);
const scene = new THREE.Scene(); scene.background = new THREE.Color(dark ? 0x16181d : 0xf4f5f7);
scene.add(new THREE.HemisphereLight(0xffffff, 0x667080, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6); scene.add(sun);
const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 1e6);
const clip = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0), planes = [];
const groups = {}, meshes = [];
const box = new THREE.Box3();
for (const f of M.faces) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3)); g.setIndex(f.t); g.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({color: f.color || M.color || "#9aa3ad", metalness: 0.15, roughness: 0.55,
    side: THREE.DoubleSide, clippingPlanes: planes, transparent: !!f.envelope, opacity: f.envelope ? 0.25 : 1});
  const mesh = new THREE.Mesh(g, mat); mesh.userData.component = f.component || null;
  const key = f.component || "_"; (groups[key] ||= new THREE.Group()).add(mesh); meshes.push(mesh);
  g.computeBoundingBox(); box.union(g.boundingBox);
}
for (const e of M.edges || []) {
  const g = new THREE.BufferGeometry(); const p = e.p, seg = [];
  for (let i = 0; i + 5 < p.length; i += 3) seg.push(p[i], p[i + 1], p[i + 2], p[i + 3], p[i + 4], p[i + 5]);
  g.setAttribute("position", new THREE.Float32BufferAttribute(seg, 3));
  const key = e.component || "_";
  (groups[key] ||= new THREE.Group()).add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({color: dark ? 0x0b0d10 : 0x252a33, clippingPlanes: planes})));
}
Object.values(groups).forEach((g) => scene.add(g));
const centre = box.getCenter(new THREE.Vector3()), radius = Math.max(1, box.getSize(new THREE.Vector3()).length() / 2);
// orbit camera about the target
let target = centre.clone(), dist = radius * 3, rot = new THREE.Quaternion();
const VIEWS = {iso: [-Math.PI / 4, Math.atan(1 / Math.SQRT2)], front: [0, 0], top: [0, Math.PI / 2 - 1e-4], right: [Math.PI / 2, 0]};
function setView(n) { const [az, el] = VIEWS[n]; rot.setFromEuler(new THREE.Euler(-el, az, 0, "YXZ")); target = centre.clone(); dist = radius * 3; draw(); }
function draw() {
  const w = view.clientWidth, h = view.clientHeight; renderer.setSize(w, h, false); renderer.domElement.style.width = w + "px"; renderer.domElement.style.height = h + "px";
  renderer.setPixelRatio(devicePixelRatio); camera.aspect = w / h; camera.near = dist / 100; camera.far = dist * 100; camera.updateProjectionMatrix();
  camera.position.copy(new THREE.Vector3(0, 0, dist).applyQuaternion(rot).add(target)); camera.quaternion.copy(rot);
  sun.position.copy(camera.position).add(new THREE.Vector3(radius, radius * 2, 0));
  renderer.render(scene, camera); place();
}
let drag = null, mode = null;
renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
renderer.domElement.addEventListener("pointerdown", (e) => { if (mode === "pen") return penStart(e); if (mode === "meas" && e.button === 0) return measure(e); if (mode === "txt" && e.button === 0) return addNote(e); drag = {x: e.clientX, y: e.clientY, b: e.button}; });
addEventListener("pointermove", (e) => { if (pen) return penMove(e); if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
  if (drag.b === 2 || e.shiftKey) { const s = dist * 0.0015; target.add(new THREE.Vector3(-dx * s, dy * s, 0).applyQuaternion(rot)); }
  else { const qy = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -dx * 0.008); const qx = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -dy * 0.008); rot.premultiply(qy).multiply(qx); }
  draw(); });
addEventListener("pointerup", () => { drag = null; pen = null; });
renderer.domElement.addEventListener("wheel", (e) => { e.preventDefault(); dist *= Math.exp(e.deltaY * 0.001); draw(); }, {passive: false});
addEventListener("resize", () => { sizeInk(); draw(); redrawInk(); });
document.querySelectorAll("[data-v]").forEach((b) => b.onclick = () => setView(b.dataset.v));
document.getElementById("fit").onclick = () => { target = centre.clone(); dist = radius * 3; draw(); };
// section plane
const secbox = document.getElementById("secbox");
function section() { const ax = document.getElementById("secax").value, t = document.getElementById("secpos").value / 1000;
  const n = {X: [-1, 0, 0], Y: [0, -1, 0], Z: [0, 0, -1]}[ax], i = "XYZ".indexOf(ax); const lo = box.min.getComponent(i), hi = box.max.getComponent(i);
  clip.normal.set(...n); clip.constant = lo + (hi - lo) * t; draw(); }
document.getElementById("sec").onclick = (e) => { const on = !planes.length; planes.length = 0; if (on) planes.push(clip); e.target.classList.toggle("on", on); secbox.hidden = !on; meshes.forEach((m) => m.material.needsUpdate = true); section(); };
document.getElementById("secax").onchange = section; document.getElementById("secpos").oninput = section;
// measure: distance between two picked points
const ray = new THREE.Raycaster(); let picks = [];
function hit(e) { const r = renderer.domElement.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), camera);
  return ray.intersectObjects(meshes.filter((m) => m.parent.visible))[0]; }
function measure(e) { const h = hit(e); if (!h) return; picks.push(h.point.clone()); if (picks.length === 2) { const d = picks[0].distanceTo(picks[1]); const v = picks[1].clone().sub(picks[0]);
  status.textContent = `Distance ${d.toFixed(3)} ${M.units}  (dX ${v.x.toFixed(3)}, dY ${v.y.toFixed(3)}, dZ ${v.z.toFixed(3)})`; picks = []; } else status.textContent = "Pick the second point"; }
// markup: pen strokes and notes, both pinned to the screen; kept in this browser
const ctx = ink.getContext("2d"); let strokes = [], notes = [], pen = null;
const KEY = "osw-markup:" + document.title;
try { const s = JSON.parse(localStorage.getItem(KEY) || "null"); if (s) { strokes = s.strokes || []; notes = s.notes || []; } } catch (_) {}
function persist() { try { localStorage.setItem(KEY, JSON.stringify({strokes, notes})); } catch (_) {} }
function sizeInk() { ink.width = view.clientWidth * devicePixelRatio; ink.height = view.clientHeight * devicePixelRatio; ink.style.width = view.clientWidth + "px"; ink.style.height = view.clientHeight + "px"; }
function redrawInk() { ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0); ctx.clearRect(0, 0, ink.width, ink.height);
  ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--mark"); ctx.lineWidth = 2.5; ctx.lineJoin = ctx.lineCap = "round";
  for (const s of strokes) { ctx.beginPath(); s.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke(); }
  view.querySelectorAll(".note").forEach((n) => n.remove());
  for (const n of notes) { const d = document.createElement("div"); d.className = "note"; d.textContent = n.text; d.style.left = n.x + "px"; d.style.top = n.y + "px"; view.append(d); } }
function local(e) { const r = view.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
function penStart(e) { pen = [local(e)]; strokes.push(pen); }
function penMove(e) { pen.push(local(e)); redrawInk(); }
addEventListener("pointerup", () => persist());
function addNote(e) { const text = prompt("Note"); if (text) { const [x, y] = local(e); notes.push({x, y, text}); redrawInk(); persist(); } }
for (const [id, m] of [["pen", "pen"], ["txt", "txt"], ["meas", "meas"]]) document.getElementById(id).onclick = (e) => {
  mode = mode === m ? null : m; document.querySelectorAll("#pen,#txt,#meas").forEach((b) => b.classList.toggle("on", b.id === id && mode === m));
  status.textContent = {pen: "Drag to draw", txt: "Click to place a note", meas: "Pick two points"}[mode] || "Drag: rotate · Right-drag: pan · Wheel: zoom"; picks = []; };
document.getElementById("clr").onclick = () => { strokes = []; notes = []; redrawInk(); persist(); };
function download(name, url) { const a = document.createElement("a"); a.href = url; a.download = name; a.click(); }
document.getElementById("png").onclick = () => { draw(); const c = document.createElement("canvas"); c.width = ink.width; c.height = ink.height; const x = c.getContext("2d");
  x.drawImage(renderer.domElement, 0, 0, c.width, c.height); x.drawImage(ink, 0, 0); x.scale(devicePixelRatio, devicePixelRatio); x.font = "12px system-ui";
  for (const n of notes) { const w = x.measureText(n.text).width + 12; x.fillStyle = "#fff8c5"; x.fillRect(n.x - w / 2, n.y - 22, w, 20); x.fillStyle = "#1d2330"; x.fillText(n.text, n.x - w / 2 + 6, n.y - 8); }
  download(document.title + "-markup.png", c.toDataURL("image/png")); };
document.getElementById("js").onclick = () => download(document.title + "-markup.json", URL.createObjectURL(new Blob([JSON.stringify({model: M.name, strokes, notes}, null, 1)], {type: "application/json"})));
// properties, components, PMI
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
document.getElementById("props").innerHTML = "<h3>Properties</h3><table>" + Object.entries(M.properties).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("") + "</table>";
if (M.components && M.components.length) document.getElementById("comps").innerHTML = "<h3>Components</h3>" + M.components.map((c) => `<label><input type="checkbox" checked data-c="${esc(c)}">${esc(c)}</label>`).join("");
document.querySelectorAll("[data-c]").forEach((cb) => cb.onchange = () => { if (groups[cb.dataset.c]) groups[cb.dataset.c].visible = cb.checked; draw(); });
const pmiEls = (M.pmi || []).map((p) => { const d = document.createElement("div"); d.className = "pmi"; d.textContent = p.text + (p.position ? "  " + p.position : ""); view.append(d); return [d, new THREE.Vector3(...p.anchor)]; });
function place() { const w = view.clientWidth, h = view.clientHeight; for (const [d, p] of pmiEls) { const q = p.clone().project(camera); d.style.left = (q.x + 1) / 2 * w + "px"; d.style.top = (1 - q.y) / 2 * h + "px"; d.hidden = q.z > 1; } }
sizeInk(); setView("iso"); redrawInk();
// Composer: steps move components (offsets from the assembled position), hide them or make them see-through
if (M.steps && M.steps.length) {
  document.getElementById("stepbar").hidden = false;
  const stepsEl = document.getElementById("steps"); let cur = 0, anim = 0, playing = null;
  meshes.forEach((m) => m.userData.opacity = m.material.opacity);
  stepsEl.innerHTML = "<h3>Steps</h3>" + M.steps.map((s, i) => `<label data-s="${i}" style="cursor:pointer"><b>${i + 1}.</b>&nbsp;${esc(s.name)}</label>`).join("") + '<p id="snote" style="color:var(--muted);white-space:pre-wrap"></p>';
  function go(i) {
    cur = Math.max(0, Math.min(M.steps.length - 1, i)); const s = M.steps[cur], from = {}, to = {};
    for (const [k, g] of Object.entries(groups)) { from[k] = g.position.clone(); to[k] = new THREE.Vector3(...((s.offsets || {})[k] || [0, 0, 0])); g.visible = !(s.hidden || []).includes(k); }
    for (const m of meshes) { const see = (s.transparent || []).includes(m.userData.component); m.material.transparent = see || m.userData.opacity < 1; m.material.opacity = see ? 0.3 : m.userData.opacity; m.material.needsUpdate = true; }
    if (s.view && VIEWS[s.view]) { const [az, el] = VIEWS[s.view]; rot.setFromEuler(new THREE.Euler(-el, az, 0, "YXZ")); }
    stepsEl.querySelectorAll("[data-s]").forEach((l) => l.style.color = +l.dataset.s === cur ? "var(--accent)" : "");
    document.getElementById("snote").textContent = s.note || "";
    const t0 = performance.now(); cancelAnimationFrame(anim);
    const tick = (t) => { const k = Math.min(1, (t - t0) / 700), e = k * k * (3 - 2 * k);
      for (const [n, g] of Object.entries(groups)) g.position.lerpVectors(from[n], to[n], e);
      draw(); if (k < 1) anim = requestAnimationFrame(tick); };
    anim = requestAnimationFrame(tick);
  }
  stepsEl.querySelectorAll("[data-s]").forEach((l) => l.onclick = () => go(+l.dataset.s));
  document.getElementById("sprev").onclick = () => go(cur - 1);
  document.getElementById("snext").onclick = () => go(cur + 1);
  document.getElementById("splay").onclick = (e) => {
    if (playing) { clearInterval(playing); playing = null; e.target.textContent = "Play"; return; }
    e.target.textContent = "Pause"; if (cur >= M.steps.length - 1) go(0);
    playing = setInterval(() => { if (cur >= M.steps.length - 1) { clearInterval(playing); playing = null; e.target.textContent = "Play"; } else go(cur + 1); }, 1800); };
  go(0);
}
</script></body></html>
"""
