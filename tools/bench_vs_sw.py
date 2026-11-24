"""Resource use of openswx against SOLIDWORKS on the same tasks, on one Windows machine.

    python tools/bench_vs_sw.py run openswx    -o results.jsonl [--launches 3] [--warmup 1] [--parts A B ...]
    python tools/bench_vs_sw.py run solidworks -o results.jsonl [--launches 3] [--warmup 1] [--parts A B ...]
    python tools/bench_vs_sw.py footprint -o results.jsonl [--sw-dir DIR] [--openswx-dir DIR] [--file NAME=PATH ...]
    python tools/bench_vs_sw.py sysinfo -o results.jsonl
    python tools/bench_vs_sw.py report results.jsonl > docs/benchmarks/resource-use-tables.md

`run` starts the program the way a user does (openswx: the desktop app, `python -m openswx desktop`; SOLIDWORKS:
SLDWORKS.exe), then, in each launch:

1. start: time until the window is usable, peak and settled memory, CPU time, processes and threads;
2. idle: one empty part open, sampled for --idle seconds;
3. parts: opens each --parts file (copied to a temporary folder first), runs the rebuild command (SOLIDWORKS:
   ForceRebuild3; openswx: `rebuild`, which reuses per-feature results when nothing changed, so the two are not
   comparable), saves it in the program's own format (size on disk) and closes it;
4. model: the same sketch / extrude / fillet / hole / pattern / shell steps in both programs (openswx through its
   HTTP API - the same commands the UI sends - and SOLIDWORKS through its COM API), timed per step;

and closes the program. The first --warmup launches are recorded but marked, and `report` leaves them out.

Memory and CPU are sampled with psutil every --period seconds over the program's whole process tree (openswx's
window is a WebView2 browser: its msedgewebview2 processes are children of the app and are counted). Working sets
are summed, so pages shared between processes count more than once; private bytes do not have that problem.

"Usable" means: openswx - the UI page has loaded its model state (`window.app.version >= 0`, read through the
WebView2 DevTools port); SOLIDWORKS - its COM object answers and reports StartupProcessCompleted. "Quiet" is the
first moment after that when the process tree uses under 2 % of one core for two seconds.

SOLIDWORKS is driven only through its public automation interface, from tools/difftest/difftest.py. Nothing it
writes is kept: saved files go to a temporary folder that is deleted at the end. The results file holds part
names (relative to the corpus) and numbers only - no user names or absolute paths.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "difftest"))

import psutil  # noqa: E402

QUIET_CPU = 0.02      # share of one core below which the program counts as idle
QUIET_FOR = 2.0       # seconds it has to stay there

# The scripted modelling sequence (the vocabulary of tools/difftest/difftest.py: model millimetres, Top = XZ,
# faces and edges picked by a point on them). Each step is one or more operations, timed together.
MODEL_STEPS = [
    ("sketch", [("sketch", "Top"), ("rect", 0, 0, 100, 60)]),
    ("extrude", [("extrude", 20, "Blind")]),
    ("fillet", [("fillet", 5, [(50, 10, 30), (50, 10, -30), (-50, 10, 30), (-50, 10, -30)])]),
    ("hole", [("sketch_face", (10, 20, 10)), ("circle", 0, 0, 5), ("cut", 0, "ThroughAll")]),
    ("pattern", [("pattern", 15, 3, (0, 20, 30))]),
    ("shell", [("shell", 2, (10, 20, 10))]),
]


# ---------------------------------------------------------------------------------------------- sampling
class Sampler(threading.Thread):
    """Samples a process tree: summed working set, private bytes, threads, process count and CPU seconds. CPU
    time of processes that have exited is kept (a child that ran a rebuild and left still counts)."""

    def __init__(self, root_pid: int, period: float):
        super().__init__(daemon=True)
        self.root = psutil.Process(root_pid)
        self.period = period
        self.samples: list[tuple] = []  # (t, ws, private, threads, procs, cpu_s)
        self.cpu: dict[tuple, float] = {}
        self.stop_flag = threading.Event()
        self.pids: set[int] = set()
        self.names: dict[str, int] = {}  # most processes of each executable seen at once

    def tree(self) -> list[psutil.Process]:
        try:
            return [self.root, *self.root.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []

    def run(self):
        while not self.stop_flag.is_set():
            t = time.perf_counter()
            ws = priv = threads = n = 0
            pids = set()
            for p in self.tree():
                try:
                    with p.oneshot():
                        mi, ct, nt = p.memory_info(), p.cpu_times(), p.num_threads()
                        key = (p.pid, p.create_time())
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                ws += mi.rss
                priv += getattr(mi, "private", 0)
                threads += nt
                n += 1
                pids.add(p.pid)
                self.cpu[key] = ct.user + ct.system
            self.pids = pids
            counts: dict[str, int] = {}
            for p in self.tree():
                try:
                    counts[p.name()] = counts.get(p.name(), 0) + 1
                except psutil.Error:
                    pass
            for k, v in counts.items():
                self.names[k] = max(self.names.get(k, 0), v)
            self.samples.append((t, ws, priv, threads, n, sum(self.cpu.values())))
            self.stop_flag.wait(max(0.0, self.period - (time.perf_counter() - t)))

    def stop(self):
        self.stop_flag.set()
        self.join(5)

    def window(self, t0: float, t1: float) -> list[tuple]:
        return [s for s in self.samples if t0 <= s[0] <= t1]

    def cpu_at(self, t: float) -> float:
        before = [s for s in self.samples if s[0] <= t]
        return before[-1][5] if before else 0.0

    def stats(self, t0: float, t1: float) -> dict:
        w = self.window(t0, t1)
        if not w:
            return {}
        mb = 1024 * 1024
        return {"peak_ws_mb": round(max(s[1] for s in w) / mb, 1), "peak_private_mb": round(max(s[2] for s in w) / mb, 1),
                "median_ws_mb": round(statistics.median(s[1] for s in w) / mb, 1),
                "median_private_mb": round(statistics.median(s[2] for s in w) / mb, 1),
                "max_threads": max(s[3] for s in w), "max_processes": max(s[4] for s in w),
                "median_threads": round(statistics.median(s[3] for s in w)),
                "median_processes": round(statistics.median(s[4] for s in w)),
                "cpu_s": round(self.cpu_at(t1) - self.cpu_at(t0), 2)}

    def wait_quiet(self, since: float, limit: float = 120.0) -> float:
        """Seconds from `since` until the tree has used under QUIET_CPU of a core for QUIET_FOR seconds."""
        end = since + limit
        while time.perf_counter() < end:
            now = time.perf_counter()
            if now - since >= QUIET_FOR:
                w = self.window(now - QUIET_FOR, now)
                if len(w) >= 2 and (w[-1][5] - w[0][5]) / max(w[-1][0] - w[0][0], 1e-6) < QUIET_CPU:
                    return round(now - QUIET_FOR - since, 2)
            time.sleep(0.1)
        return round(limit, 2)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def kill_tree(pid: int, timeout: float = 15.0) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = [root, *root.children(recursive=True)]
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=timeout)


# ---------------------------------------------------------------------------------------------- openswx
class OpenSwxApp:
    name = "openswx"

    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.port, self.devtools = free_port(), free_port()
        self.token = "bench-" + os.urandom(16).hex()
        self.proc = None

    def version(self) -> str:
        import openswx
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return f"{openswx.__version__} (main {rev}, from source, Python {platform.python_version()})"

    def launch(self) -> int:
        home = self.scratch / "openswx-home"
        home.mkdir(exist_ok=True)
        env = dict(os.environ, OPENSWX_LOG="off", OPENSWX_AUTOSAVE="off", OPENSWX_PLUGINS="off", OPENSWX_CACHE="off",
                   OPENSWX_TOKEN=self.token, OPENSWX_HOME=str(home),
                   # its own WebView2 profile: with the default one an openswx already open would host the page in
                   # its browser processes (not counted here), and the DevTools port could not be added
                   WEBVIEW2_USER_DATA_FOLDER=str(self.scratch / "webview2"),
                   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={self.devtools}")
        self.proc = subprocess.Popen([sys.executable, "-m", "openswx", "desktop", "--port", str(self.port)], cwd=ROOT,
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.proc.pid

    def _evaluate(self, expr: str):
        from websockets.sync.client import connect
        with urllib.request.urlopen(f"http://127.0.0.1:{self.devtools}/json", timeout=2) as r:
            targets = json.load(r)
        page = next((t for t in targets if t.get("type") == "page" and f":{self.port}/" in t.get("url", "")), None)
        if page is None:
            return None
        with connect(page["webSocketDebuggerUrl"], open_timeout=2, max_size=None) as ws:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv(timeout=5))
                if msg.get("id") == 1:
                    return msg.get("result", {}).get("result", {}).get("value")

    def ready(self) -> bool:
        try:
            return bool(self._evaluate("!!(window.app && window.app.version >= 0)"))
        except Exception:  # noqa: BLE001 - not up yet
            return False

    def call(self, command: str, **args):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/command", method="POST",
                                     data=json.dumps({"name": command, "args": args}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                body = json.load(r)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read() or b"{}")
        if not body.get("ok"):
            raise RuntimeError(f"{command}: {body.get('error')}")
        return body["result"]

    def new_part(self):
        self.call("new_part", name="Part1")

    def close_all(self):
        for d in self.call("list_documents").get("documents", []):
            self.call("close_document", name=d["name"] if isinstance(d, dict) else d)

    def open_part(self, path: Path) -> dict:
        r = self.call("open_part", path=str(path))
        return {"result_keys": sorted(r)[:12]}

    def rebuild(self):
        self.call("rebuild")

    def volume(self) -> float:
        return self.call("get_mass_properties")["volume"]

    def save(self, stem: str) -> int:
        out = self.scratch / f"{stem}.oswprt"
        self.call("save_part", path=str(out))
        return out.stat().st_size

    def close_doc(self):
        self.call("close_document")

    # modelling sequence: the operations of MODEL_STEPS as openswx commands (like difftest.OpenSwx)
    def _face_at(self, p):
        faces = self.call("get_topology")["faces"]
        return min(faces, key=lambda f: sum((a - b) ** 2 for a, b in zip(f["point"], p)) if abs(f["normal"][1]) > 0.99 else 1e18)["id"]

    def _edge_at(self, p):
        return min(self.call("get_topology")["edges"], key=lambda e: sum((a - b) ** 2 for a, b in zip(e["point"], p)))["id"]

    def model_begin(self):
        self.call("new_part", name="Bench")
        self.sk = None

    def model_op(self, op):
        k = op[0]
        if k == "sketch":
            self.sk = self.call("create_sketch", plane=op[1])["sketch"]
        elif k == "sketch_face":
            self.sk = self.call("create_sketch", plane=self._face_at(op[1]))["sketch"]
        elif k == "rect":
            _, cx, cy, w, h = op
            self.call("sketch_rectangle", sketch=self.sk, corner1=[cx, cy], corner2=[cx + w / 2, cy + h / 2], centered=True)
        elif k == "circle":
            self.call("sketch_circle", sketch=self.sk, center=[op[1], op[2]], radius=op[3])
        elif k == "extrude":
            self.call("extrude", sketch=self.sk, depth=op[1], end_condition=op[2])
        elif k == "cut":
            self.call("cut_extrude", sketch=self.sk, depth=op[1] or 10, end_condition=op[2])
        elif k == "fillet":
            self.call("fillet", radius=op[1], edges=[self._edge_at(p) for p in op[2]])
        elif k == "pattern":
            last = [f["name"] for f in self.call("get_model")["features"] if f["type"] != "sketch"][-1]
            self.call("linear_pattern", features=[last], direction=self._edge_at(op[3]), spacing=op[1], count=op[2])
        elif k == "shell":
            self.call("shell", thickness=op[1], faces_to_remove=[self._face_at(op[2])])
        else:
            raise ValueError(k)

    def quit(self):
        if self.proc is not None:
            kill_tree(self.proc.pid)
            self.proc = None


# ---------------------------------------------------------------------------------------------- SOLIDWORKS
class SolidWorksApp:
    name = "SOLIDWORKS"

    def __init__(self, scratch: Path, answer: str | None = None):
        import pythoncom
        pythoncom.CoInitialize()
        self.scratch = scratch
        self.answer = answer  # a start-up question's button to press (its text starts with this), or None
        self.answered: list[str] = []
        self.dim_toggle: bool | None = None
        self.proc = None
        self.sw = None
        self.m = None
        self.exe = Path(self._install_dir()) / "SLDWORKS.exe"

    @staticmethod
    def _install_dir() -> str:
        import winreg
        for ver in range(2030, 2015, -1):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\SolidWorks\SOLIDWORKS {ver}\Setup") as k:
                    return winreg.QueryValueEx(k, "SolidWorks Folder")[0]
            except OSError:
                continue
        raise SystemExit("no SOLIDWORKS installation found in the registry")

    def version(self) -> str:
        from difftest import call0
        return f"SOLIDWORKS {call0(self.sw.app, 'RevisionNumber')}" if self.sw else "?"

    def launch(self) -> int:
        self.proc = subprocess.Popen([str(self.exe)])
        return self.proc.pid

    def _answer_dialog(self) -> None:
        """Press --sw-answer's button in a question SOLIDWORKS asks while it starts (it waits for one otherwise)."""
        import win32con
        import win32gui
        import win32process

        def each(h, _):
            if not win32gui.IsWindowVisible(h) or win32gui.GetClassName(h) != "#32770":
                return
            if win32process.GetWindowThreadProcessId(h)[1] != self.proc.pid:
                return
            buttons = []
            win32gui.EnumChildWindows(h, lambda c, _: buttons.append(c) if win32gui.GetClassName(c) == "Button" else None, None)
            for b in buttons:
                text = win32gui.GetWindowText(b).replace("&", "")
                if self.answer and text.lower().startswith(self.answer.lower()):
                    win32gui.PostMessage(b, win32con.BM_CLICK, 0, 0)
                    self.answered.append(text)
                    return
        win32gui.EnumWindows(each, None)

    def ready(self) -> bool:
        import win32com.client
        from difftest import call0
        if self.answer:
            self._answer_dialog()
        try:
            app = win32com.client.GetActiveObject("SldWorks.Application")
            if not app.StartupProcessCompleted:
                return False
            frame = call0(app, "Frame")
        except Exception:  # noqa: BLE001 - not up yet
            return False
        if frame is None:
            return False
        from difftest import SolidWorks
        self.sw = SolidWorks.__new__(SolidWorks)  # (attach to the running instance instead of Dispatch)
        import pythoncom
        self.sw.VT = (pythoncom, win32com.client)
        self.sw.app = app
        self.sw.template = app.GetUserPreferenceStringValue(8)  # swDefaultTemplatePart
        # swInputDimValOnCreate: no Modify box for new dimensions. It is a user setting, so quit() puts it back.
        self.dim_toggle = bool(app.GetUserPreferenceToggle(10))
        app.SetUserPreferenceToggle(10, False)
        return True

    def new_part(self):
        self.m = self.sw.new_doc()

    def close_all(self):
        self.sw.app.CloseAllDocuments(True)
        self.m = None

    def open_part(self, path: Path) -> dict:
        import pythoncom
        import win32com.client as w
        err = w.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warn = w.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        self.m = self.sw.app.OpenDoc6(str(path), 1, 1, "", err, warn)  # swDocPART, swOpenDocOptions_Silent
        if self.m is None:
            raise RuntimeError(f"OpenDoc6 failed (error {err.value})")
        return {"errors": err.value, "warnings": warn.value}

    def rebuild(self):
        self.m.ForceRebuild3(False)

    def volume(self) -> float:
        from difftest import call0
        return call0(self.m.Extension, "CreateMassProperty").Volume * 1e9

    def save(self, stem: str) -> int:
        out = self.scratch / f"{stem}.SLDPRT"
        self.m.SaveAs3(str(out), 0, 3)  # swSaveAsCurrentVersion; swSaveAsOptions_Silent | _Copy
        return out.stat().st_size

    def close_doc(self):
        from difftest import call0
        self.sw.app.CloseDoc(call0(self.m, "GetTitle"))
        self.m = None

    def model_begin(self):
        self.m = self.sw.new_doc()

    def model_op(self, op):
        self.sw.do(self.m, op)

    def quit(self):
        if self.sw is not None:
            try:
                from difftest import call0
                self.sw.app.CloseAllDocuments(True)
                if self.dim_toggle is not None:
                    self.sw.app.SetUserPreferenceToggle(10, self.dim_toggle)
                call0(self.sw.app, "ExitApp")
            except Exception:  # noqa: BLE001
                pass
            self.sw = None
        if self.proc is not None:
            try:
                self.proc.wait(60)
            except subprocess.TimeoutExpired:
                kill_tree(self.proc.pid)
            self.proc = None


# ---------------------------------------------------------------------------------------------- one launch
def one_launch(prog, parts: list[tuple[str, Path]], args, warmup: bool) -> dict:
    rec = {"kind": "launch", "program": prog.name, "warmup": warmup, "date": time.strftime("%Y-%m-%d %H:%M")}
    t_launch = time.perf_counter()
    pid = prog.launch()
    smp = Sampler(pid, args.period)
    smp.start()
    try:
        deadline = t_launch + args.start_timeout
        while not prog.ready():
            if time.perf_counter() > deadline:
                raise RuntimeError("the program did not become usable in time")
            time.sleep(0.1)
        t_ready = time.perf_counter()
        quiet = smp.wait_quiet(t_ready, limit=120)
        time.sleep(max(0.0, t_ready + quiet + 5 - time.perf_counter()))
        t_settled = time.perf_counter()
        rec["version"] = prog.version()
        if getattr(prog, "answered", None):
            rec["answered_at_start"] = prog.answered
        rec["start"] = {"ready_s": round(t_ready - t_launch, 2), "quiet_after_ready_s": quiet,
                        "peak": smp.stats(t_launch, t_settled),
                        "settled": smp.stats(t_settled - 5, t_settled),
                        "cpu_to_ready_s": round(smp.cpu_at(t_ready), 2)}
        print(f"  start: usable after {rec['start']['ready_s']} s", flush=True)

        prog.new_part()
        smp.wait_quiet(time.perf_counter(), limit=30)
        t0 = time.perf_counter()
        time.sleep(args.idle)
        t1 = time.perf_counter()
        st = smp.stats(t0, t1)
        st["cpu_percent_of_core"] = round(100 * st["cpu_s"] / (t1 - t0), 2)
        rec["idle"] = st
        prog.close_all()
        print(f"  idle: {st['median_private_mb']} MB private, {st['cpu_percent_of_core']} % CPU", flush=True)

        rec["parts"] = {}
        for label, path in parts:
            smp.wait_quiet(time.perf_counter(), limit=30)
            base = smp.stats(time.perf_counter() - 1.0, time.perf_counter())
            t0 = time.perf_counter()
            info = prog.open_part(path)
            t1 = time.perf_counter()
            quiet = smp.wait_quiet(t1, limit=60)
            t2 = time.perf_counter()
            prog.rebuild()
            t3 = time.perf_counter()
            vol = prog.volume()
            size = prog.save(f"part{len(rec['parts'])}")
            peak = smp.stats(t0, t3)
            prog.close_doc()
            rec["parts"][label] = {"open_s": round(t1 - t0, 2), "open_to_quiet_s": round(t1 - t0 + quiet, 2),
                                   "rebuild_s": round(t3 - t2, 3), "cpu_open_s": round(smp.cpu_at(t1 + quiet) - smp.cpu_at(t0), 2),
                                   "peak_ws_mb": peak.get("peak_ws_mb"), "peak_private_mb": peak.get("peak_private_mb"),
                                   "added_private_mb": round(peak.get("peak_private_mb", 0) - base.get("median_private_mb", 0), 1),
                                   "volume_mm3": vol, "native_file_bytes": size, "info": info}
            print(f"  open {label}: {rec['parts'][label]['open_s']} s, rebuild {rec['parts'][label]['rebuild_s']} s", flush=True)

        smp.wait_quiet(time.perf_counter(), limit=30)
        steps = {}
        t_m0 = time.perf_counter()
        prog.model_begin()
        steps["new part"] = round(time.perf_counter() - t_m0, 3)
        for step, ops in MODEL_STEPS:
            t = time.perf_counter()
            for op in ops:
                prog.model_op(op)
            steps[step] = round(time.perf_counter() - t, 3)
        t_m1 = time.perf_counter()
        quiet = smp.wait_quiet(t_m1, limit=60)
        vol = prog.volume()
        size = prog.save("model")
        prog.close_doc()
        rec["model"] = {"steps_s": steps, "total_s": round(t_m1 - t_m0, 3), "to_quiet_s": round(t_m1 - t_m0 + quiet, 2),
                        "peak": smp.stats(t_m0, t_m1 + quiet), "volume_mm3": vol, "native_file_bytes": size}
        print(f"  model: {rec['model']['total_s']} s, volume {vol:.3f} mm3", flush=True)
    finally:
        prog.quit()
        smp.stop()
    rec["process_names"] = smp.names
    return rec


def cmd_run(args) -> int:
    scratch = Path(tempfile.mkdtemp(prefix="bench_vs_sw_"))
    try:
        parts = []
        corpus = Path(args.corpus) if args.corpus else None
        for i, p in enumerate(args.parts or []):
            src = (corpus / p) if corpus else Path(p)
            dst = scratch / f"in{i}{src.suffix}"
            shutil.copyfile(src, dst)
            parts.append((p.replace("\\", "/"), dst))
        running = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        if "sldworks.exe" in running:  # (also for a SOLIDWORKS run: it must be the instance this script starts)
            raise SystemExit("close SOLIDWORKS first")
        if args.program == "solidworks" and "openswx.exe" in running:
            print("note: an openswx.exe is running; close it for a clean measurement", file=sys.stderr)
        for i in range(args.warmup + args.launches):
            warm = i < args.warmup
            print(f"{args.program} launch {i + 1}{' (warm-up)' if warm else ''}", flush=True)
            prog = OpenSwxApp(scratch) if args.program == "openswx" else SolidWorksApp(scratch, args.sw_answer)
            rec = one_launch(prog, parts, args, warm)
            with open(args.output, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            time.sleep(5)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


# ---------------------------------------------------------------------------------------------- footprint / sysinfo
def dir_size(path: Path) -> tuple[int, int]:
    total = files = 0
    for dirpath, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
                files += 1
            except OSError:
                pass
    return total, files


def cmd_footprint(args) -> int:
    rec = {"kind": "footprint", "items": {}}
    if args.sw_dir:
        for label, d in [("SOLIDWORKS (program folder)", Path(args.sw_dir)),
                         ("SOLIDWORKS Corp (all installed products)", Path(args.sw_dir).parent)]:
            b, n = dir_size(d)
            rec["items"][label] = {"bytes": b, "files": n}
    if args.openswx_dir:
        b, n = dir_size(Path(args.openswx_dir))
        rec["items"]["openswx desktop build (installed)"] = {"bytes": b, "files": n}
    site = ROOT / ".venv" / "Lib" / "site-packages"
    if site.exists():
        b, n = dir_size(site)
        rec["items"]["openswx from source (virtual environment site-packages, dev extras)"] = {"bytes": b, "files": n}
    for spec in args.file or []:
        label, path = spec.split("=", 1)
        rec["items"][label] = {"bytes": Path(path).stat().st_size, "files": 1}
    with open(args.output, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=1))
    return 0


def _ps(cmd: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()


def cmd_sysinfo(args) -> int:
    rec = {"kind": "sysinfo",
           "cpu": _ps("(Get-CimInstance Win32_Processor).Name"),
           "cores": psutil.cpu_count(logical=False), "logical_cpus": psutil.cpu_count(),
           "ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1),
           "gpu": _ps("(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' (driver ' + $_.DriverVersion + ')' }) -join '; '"),
           "os": _ps("$o = Get-CimInstance Win32_OperatingSystem; $o.Caption + ' ' + $o.Version"),
           "disk": _ps("(Get-PhysicalDisk | ForEach-Object { $_.MediaType + ' ' + $_.BusType }) -join '; '"),
           "power": _ps("(powercfg /getactivescheme) -replace '.*\\((.*)\\).*', '$1'"),
           "python": platform.python_version()}
    with open(args.output, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=1))
    return 0


# ---------------------------------------------------------------------------------------------- report
def _load(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _med(vals) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "-"
    m = statistics.median(vals)
    fmt = (lambda x: f"{x:,.0f}") if abs(m) >= 100 else (lambda x: f"{x:.1f}") if abs(m) >= 1 else (lambda x: f"{x:.2f}")
    return fmt(m) if len(vals) == 1 or min(vals) == max(vals) else f"{fmt(m)} ({fmt(min(vals))}-{fmt(max(vals))})"


def cmd_report(args) -> int:
    recs = _load(args.results)
    runs = {}
    for r in recs:
        if r["kind"] == "launch" and not r.get("warmup") and "model" in r:
            runs.setdefault(r["program"], []).append(r)
    progs = [p for p in ("openswx", "SOLIDWORKS") if p in runs]
    out = []
    head = "| | " + " | ".join(progs) + " |\n|---|" + "---|" * len(progs)

    def row(label, get):
        cells = []
        for p in progs:
            vals = []
            for r in runs[p]:
                try:
                    vals.append(get(r))
                except (KeyError, TypeError):
                    pass
            cells.append(_med(vals))
        out.append(f"| {label} | " + " | ".join(cells) + " |")

    out.append(f"Median of {', '.join(f'{len(runs[p])} {p}' for p in progs)} launches; the range (min-max) in brackets.\n")
    out.append("### Start\n")
    out.append(head)
    row("Time to a usable window (s)", lambda r: r["start"]["ready_s"])
    row("... then until quiet (s)", lambda r: r["start"]["quiet_after_ready_s"])
    row("CPU time to usable (s)", lambda r: r["start"]["cpu_to_ready_s"])
    row("Peak working set (MB)", lambda r: r["start"]["peak"]["peak_ws_mb"])
    row("Peak private bytes (MB)", lambda r: r["start"]["peak"]["peak_private_mb"])
    row("Settled working set (MB)", lambda r: r["start"]["settled"]["median_ws_mb"])
    row("Settled private bytes (MB)", lambda r: r["start"]["settled"]["median_private_mb"])
    row("Processes", lambda r: r["start"]["settled"]["median_processes"])
    row("Threads", lambda r: r["start"]["settled"]["median_threads"])
    out.append("\n### Idle, one empty part open\n")
    out.append(head)
    row("Working set (MB)", lambda r: r["idle"]["median_ws_mb"])
    row("Private bytes (MB)", lambda r: r["idle"]["median_private_mb"])
    row("CPU (% of one core)", lambda r: r["idle"]["cpu_percent_of_core"])
    row("Processes", lambda r: r["idle"]["median_processes"])
    row("Threads", lambda r: r["idle"]["median_threads"])
    labels = []
    for p in progs:
        for r in runs[p]:
            labels += [k for k in r.get("parts", {}) if k not in labels]
    for lab in labels:
        out.append(f"\n### Open `{lab}`\n")
        out.append(head)
        row("Open (s, until the call returns)", lambda r, lab=lab: r["parts"][lab]["open_s"])
        row("Open, until quiet (s)", lambda r, lab=lab: r["parts"][lab]["open_to_quiet_s"])
        row("Rebuild command, nothing changed (s)", lambda r, lab=lab: r["parts"][lab]["rebuild_s"])
        row("CPU time to open (s)", lambda r, lab=lab: r["parts"][lab]["cpu_open_s"])
        row("Peak working set (MB)", lambda r, lab=lab: r["parts"][lab]["peak_ws_mb"])
        row("Peak private bytes (MB)", lambda r, lab=lab: r["parts"][lab]["peak_private_mb"])
        row("Private bytes added at peak (MB)", lambda r, lab=lab: r["parts"][lab]["added_private_mb"])
        row("Native file (KB)", lambda r, lab=lab: r["parts"][lab]["native_file_bytes"] / 1024)
        row("Volume (mm³)", lambda r, lab=lab: r["parts"][lab]["volume_mm3"])
    out.append("\n### Scripted modelling sequence\n")
    out.append(head)
    for step in ["new part", *[s for s, _ in MODEL_STEPS]]:
        row(f"{step} (s)", lambda r, step=step: r["model"]["steps_s"][step])
    row("Total (s)", lambda r: r["model"]["total_s"])
    row("Total, until quiet (s)", lambda r: r["model"]["to_quiet_s"])
    row("CPU time (s)", lambda r: r["model"]["peak"]["cpu_s"])
    row("Peak working set (MB)", lambda r: r["model"]["peak"]["peak_ws_mb"])
    row("Peak private bytes (MB)", lambda r: r["model"]["peak"]["peak_private_mb"])
    row("Native file (KB)", lambda r: r["model"]["native_file_bytes"] / 1024)
    row("Volume (mm³)", lambda r: r["model"]["volume_mm3"])
    for r in recs:
        if r["kind"] == "footprint":
            out.append("\n### Install footprint\n")
            out.append("| | Size (MB) | Files |\n|---|---|---|")
            for k, v in r["items"].items():
                out.append(f"| {k} | {v['bytes'] / 2 ** 20:,.0f} | {v['files']:,} |")
    print("\n".join(out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("program", choices=["openswx", "solidworks"])
    r.add_argument("-o", "--output", required=True)
    r.add_argument("--launches", type=int, default=3)
    r.add_argument("--warmup", type=int, default=1)
    r.add_argument("--parts", nargs="*", help="part files to open (paths relative to --corpus)")
    r.add_argument("--corpus", help="folder the --parts paths are relative to")
    r.add_argument("--idle", type=float, default=60.0, help="seconds to sample with an empty part open")
    r.add_argument("--period", type=float, default=0.2, help="sampling period in seconds")
    r.add_argument("--start-timeout", type=float, default=300.0)
    r.add_argument("--sw-answer", help="SOLIDWORKS: press the button whose text starts with this in a question it asks at start")
    f = sub.add_parser("footprint")
    f.add_argument("-o", "--output", required=True)
    f.add_argument("--sw-dir")
    f.add_argument("--openswx-dir")
    f.add_argument("--file", nargs="*", help="LABEL=PATH: also list one file's size (an installer, say)")
    s = sub.add_parser("sysinfo")
    s.add_argument("-o", "--output", required=True)
    p = sub.add_parser("report")
    p.add_argument("results")
    a = ap.parse_args()
    return {"run": cmd_run, "footprint": cmd_footprint, "sysinfo": cmd_sysinfo, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
