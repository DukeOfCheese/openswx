# Resource use: openswx and SOLIDWORKS

How much time, memory and CPU openswx and SOLIDWORKS need to do the same things on the same Windows laptop,
measured on 2026-11-25. The harness is [`tools/bench_vs_sw.py`](../../tools/bench_vs_sw.py), and every number
below comes from the raw results in [`resource-use-2026-11-25.jsonl`](resource-use-2026-11-25.jsonl). To
regenerate the tables, run `python tools/bench_vs_sw.py report docs/benchmarks/resource-use-2026-11-25.jsonl`.

## Summary

**Where openswx does better**

- **Start.** It is usable in 5.3 s; SOLIDWORKS takes 24.4 s. openswx also uses less CPU time getting there (1.3 s
  against 4.7 s).
- **Memory while working.**
  - With one empty part open, openswx's working set is 780 MB and SOLIDWORKS' is 1,636 MB. Private bytes are
    1,806 MB and 2,991 MB.
  - Opening the largest test part peaks at a 1,149 MB working set in openswx and 1,736 MB in SOLIDWORKS.
  - The modelling sequence below peaks at 862 MB and 1,750 MB.
- **Modelling.** The same scripted sketch, extrude, fillet, hole, pattern and shell sequence takes 0.60 s in
  openswx and 4.6 s in SOLIDWORKS. Both give the same volume, 26,542.123 mm³.
- **Files.** Native files are 4 to 24 times smaller: 8.0 KB against 189 KB for the largest test part. An
  openswx file stores the commands that built the model rather than its geometry, so the model is rebuilt when
  the file is opened.
- **Disk.**
  - The installed openswx desktop app takes 638 MB and the v0.4.1 Windows installer is a 256 MB download.
  - SOLIDWORKS' program folder takes 7,149 MB, and all the SOLIDWORKS products on this machine take 18,300 MB.

**Where SOLIDWORKS does better**

- **Opening SOLIDWORKS parts.**
  - SOLIDWORKS opens its own `.sldprt` files in 1.1 to 1.4 s. openswx takes 2.6 to 13.8 s, depending on the part.
    SOLIDWORKS also uses less CPU time on each open.
  - For openswx this is an import of a foreign format: it decodes the history, rebuilds it on its own kernel in
    a separate process and checks the result against the body stored in the file.
  - Opening an openswx `.oswprt` file was not measured here.
- **Private bytes right after start.** Before any part is open, SOLIDWORKS commits 926 MB and openswx 1,907 MB.
  SOLIDWORKS' figure rises to 2,991 MB once a part is open.
- **Private bytes while opening a part.** These are about equal: 2,992 to 3,088 MB in openswx and 3,034 to
  3,108 MB in SOLIDWORKS. openswx's rebuild process adds about 1,200 MB at its peak; SOLIDWORKS adds about
  1,000 to 1,100 MB.

Idle CPU is negligible in both programs (about 0.2 % of one core).

## Machine and versions

| | |
|---|---|
| CPU | Intel Core i7-11800H, 8 cores, 16 threads |
| RAM | 15.8 GB |
| GPU | NVIDIA GeForce RTX 3070 Laptop GPU (driver 32.0.16.1687) |
| Disk | NVMe SSD |
| OS | Windows 11 Education 10.0.22631, High performance power plan |
| openswx | 0.4.1, `main` at 72dad83, run from source in a virtual environment (Python 3.13.7) |
| SOLIDWORKS | SOLIDWORKS 2025 SP5 (revision 33.5.0) |

## Method

Each program was started 4 times. The first launch was a warm-up and is left out, so each figure is the median
of 3 launches, with the range (min-max) in brackets. The two programs never ran at the same time. In each
launch the harness:

1. **Starts the program** the way a user does: `python -m openswx desktop`, or `SLDWORKS.exe`. It records the
   time until the window is usable, then waits until the program is quiet, meaning it uses under 2 % of one
   core for 2 s. "Settled" figures are taken over the 5 s after that.
   - openswx is usable when its UI page has loaded the model state (`window.app.version >= 0`, read through the
     WebView2 DevTools port).
   - SOLIDWORKS is usable when its COM object answers, reports `StartupProcessCompleted` and has a main frame.
2. **Measures idle use** with one new, empty part open, sampled for 60 s.
3. **Opens four sample parts** that ship with SOLIDWORKS 2025, each copied to a temporary folder first. For each
   part it runs the rebuild command, reads the volume, saves the part in the program's own format, notes the
   file size and closes the part. In the survey of 2026-11-23 openswx rebuilt all four exactly and fully
   parametrically. They range from 3 features (`tutor1a`) to the slowest of the parametric parts to rebuild
   (`round cover plate`). `nozzle` is a surface part, so both programs report a volume of 0.
4. **Runs a scripted modelling sequence** on a new part:
   - a 100 × 60 mm rectangle on Top, extruded 20 mm;
   - 5 mm fillets on the four vertical edges;
   - a 10 mm hole cut through all;
   - the hole patterned 3 times at 15 mm;
   - a 2 mm shell with the top face removed.

   openswx is driven through its HTTP API, which takes the same commands as the UI. SOLIDWORKS is driven through
   its COM API, from [`tools/difftest/difftest.py`](../../tools/difftest/difftest.py). The harness times each
   step, then saves the part and reads its volume.

Throughout, [psutil](https://github.com/giampaolo/psutil) samples the program's whole process tree every
0.2 s. Every process the program starts is counted, and so is CPU time used by processes that have already
exited.

- **openswx's tree (at most):** 3 `python.exe` and 7 `msedgewebview2.exe`. The python processes are the
  virtual environment's launcher, the app itself, and the separate process that rebuilds a SOLIDWORKS file.
  The WebView2 processes are the browser that draws the window.
- **SOLIDWORKS' tree:** `SLDWORKS.exe`, `sldProcMon.exe`, 7 `swCefSubProc.exe` (its embedded browser),
  2 `mwdatabasebackupapp.exe` and 2 `conhost.exe`.

openswx ran with `OPENSWX_LOG=off OPENSWX_AUTOSAVE=off OPENSWX_PLUGINS=off OPENSWX_CACHE=off`. The cache was
off so that repeated opens of the same file are not served from openswx's disk cache. SOLIDWORKS ran with the
user's settings and add-ins as installed. The one setting the harness touches is "Input dimension value": it
turns that off, so that no Modify box opens for each new dimension, and puts the old value back when it closes
SOLIDWORKS.

## Results

Median of 3 launches of each program; the range (min-max) in brackets. MB is 2^20 bytes.

### Start

| | openswx | SOLIDWORKS |
|---|---|---|
| Time to a usable window (s) | 5.3 (5.2-5.9) | 24.4 (23.2-24.9) |
| ... then until quiet (s) | 0.03 (0.02-0.04) | 0.06 (0.04-0.08) |
| CPU time to usable (s) | 1.3 (1.1-1.4) | 4.7 (4.4-5.7) |
| Peak working set (MB) | 781 (778-785) | 1,209 (1,202-1,211) |
| Peak private bytes (MB) | 1,907 (1,903-1,907) | 926 (920-927) |
| Settled working set (MB) | 780 (777-784) | 1,204 (1,199-1,206) |
| Settled private bytes (MB) | 1,905 (1,903-1,905) | 926 (920-926) |
| Processes | 8.0 | 8.0 |
| Threads | 228 (227-228) | 238 (238-240) |

### Idle, one empty part open

| | openswx | SOLIDWORKS |
|---|---|---|
| Working set (MB) | 780 (778-782) | 1,636 (1,634-1,642) |
| Private bytes (MB) | 1,806 (1,797-1,848) | 2,991 (2,987-2,995) |
| CPU (% of one core) | 0.23 (0.18-0.32) | 0.18 (0.15-0.33) |
| Processes | 8.0 | 8.0 |
| Threads | 231 (231-232) | 238 (237-243) |

### Open `tutorial/lesson1/tutor1a.sldprt`

| | openswx | SOLIDWORKS |
|---|---|---|
| Open (s, until the call returns) | 2.9 (2.9-2.9) | 1.4 (1.3-1.5) |
| Open, until quiet (s) | 3.0 (2.9-3.0) | 1.4 (1.4-1.6) |
| Rebuild command, nothing changed (s) | 0.02 (0.01-0.03) | 0.19 (0.16-0.19) |
| CPU time to open (s) | 1.2 (0.5-1.6) | 0.50 (0.41-0.61) |
| Peak working set (MB) | 1,102 (1,098-1,104) | 1,689 (1,684-1,700) |
| Peak private bytes (MB) | 2,992 (2,972-3,017) | 3,070 (3,064-3,077) |
| Private bytes added at peak (MB) | 1,173 (1,140-1,206) | 1,090 (1,089-1,117) |
| Native file (KB) | 3.9 | 67.6 (67.1-67.9) |
| Volume (mm³) | 432,000 | 432,000 |

### Open `tutorial/lesson1/tutor1d.sldprt`

| | openswx | SOLIDWORKS |
|---|---|---|
| Open (s, until the call returns) | 7.9 (7.8-7.9) | 1.3 (1.2-1.3) |
| Open, until quiet (s) | 7.9 (7.9-8.1) | 1.3 (1.3-1.4) |
| Rebuild command, nothing changed (s) | 0.03 (0.00-0.03) | 0.22 (0.21-0.23) |
| CPU time to open (s) | 4.0 (3.9-4.2) | 0.38 (0.33-0.48) |
| Peak working set (MB) | 1,129 (1,127-1,130) | 1,708 (1,705-1,713) |
| Peak private bytes (MB) | 3,088 (3,056-3,112) | 3,034 (3,006-3,073) |
| Private bytes added at peak (MB) | 1,210 (1,206-1,224) | 1,010 (986-1,047) |
| Native file (KB) | 11.9 | 161 (161-162) |
| Volume (mm³) | 418,119 | 418,119 (418,119-418,119) |

### Open `tutorial/surfaces/nozzle.sldprt`

| | openswx | SOLIDWORKS |
|---|---|---|
| Open (s, until the call returns) | 2.6 (2.6-2.7) | 1.1 (1.0-1.2) |
| Open, until quiet (s) | 2.8 (2.6-2.8) | 1.1 (1.1-1.2) |
| Rebuild command, nothing changed (s) | 0.02 (0.02-0.03) | 0.17 (0.16-0.17) |
| CPU time to open (s) | 0.69 (0.66-1.39) | 0.23 (0.22-0.34) |
| Peak working set (MB) | 1,121 (1,120-1,130) | 1,708 (1,705-1,709) |
| Peak private bytes (MB) | 3,057 (3,050-3,098) | 3,078 (3,074-3,102) |
| Private bytes added at peak (MB) | 1,188 (1,170-1,195) | 1,032 (1,026-1,060) |
| Native file (KB) | 26.5 | 110 (109-110) |
| Volume (mm³) | 0.00 | 0.00 |

### Open `tutorial/tolanalyst/introduction/round cover plate.sldprt`

| | openswx | SOLIDWORKS |
|---|---|---|
| Open (s, until the call returns) | 13.8 (13.6-13.9) | 1.3 (1.1-1.3) |
| Open, until quiet (s) | 13.8 (13.6-14.0) | 1.3 (1.1-1.3) |
| Rebuild command, nothing changed (s) | 0.02 (0.02-0.09) | 0.32 (0.25-0.33) |
| CPU time to open (s) | 8.2 (7.9-8.6) | 0.33 (0.22-0.39) |
| Peak working set (MB) | 1,149 (1,146-1,152) | 1,736 (1,734-1,743) |
| Peak private bytes (MB) | 3,075 (3,061-3,122) | 3,108 (3,105-3,113) |
| Private bytes added at peak (MB) | 1,218 (1,215-1,231) | 1,033 (1,030-1,037) |
| Native file (KB) | 8.0 | 189 (189-189) |
| Volume (mm³) | 79,476 | 79,476 |

### Scripted modelling sequence

| | openswx | SOLIDWORKS |
|---|---|---|
| new part (s) | 0.03 (0.01-0.09) | 0.93 (0.90-0.95) |
| sketch (s) | 0.04 (0.02-0.08) | 1.2 (1.1-1.2) |
| extrude (s) | 0.03 (0.01-0.04) | 0.35 (0.29-0.44) |
| fillet (s) | 0.15 (0.12-0.16) | 0.35 (0.33-0.37) |
| hole (s) | 0.10 (0.08-0.13) | 1.2 (0.9-1.4) |
| pattern (s) | 0.13 (0.06-0.13) | 0.49 (0.29-0.60) |
| shell (s) | 0.08 (0.07-0.15) | 0.41 (0.32-0.43) |
| Total (s) | 0.60 (0.51-0.60) | 4.6 (4.6-5.3) |
| Total, until quiet (s) | 0.66 (0.66-2.06) | 4.7 (4.6-5.3) |
| CPU time (s) | 0.12 (0.02-0.28) | 0.44 (0.34-0.64) |
| Peak working set (MB) | 862 (827-866) | 1,750 (1,746-1,755) |
| Peak private bytes (MB) | 1,931 (1,861-1,941) | 3,170 (3,170-3,306) |
| Native file (KB) | 8.5 | 101 (101-102) |
| Volume (mm³) | 26,542 | 26,542 (26,542-26,542) |


### Install footprint

| | Size (MB) | Files |
|---|---|---|
| SOLIDWORKS 2025 SP5 program folder | 7,149 | 20,156 |
| All SOLIDWORKS products installed on this machine | 18,300 | 56,763 |
| openswx desktop app, installed (a local build older than v0.3.0) | 638 | 2,796 |
| openswx from source: virtual environment with development extras | 903 | 30,200 |
| openswx 0.4.1 Windows installer (`openswx-setup-0.4.1.exe`, release download) | 256 | 1 |
| openswx 0.4.1 Windows zip (`openswx-0.4.1-win64.zip`, release download) | 313 | 1 |

MB here means 2^20 bytes. The first four rows are measured on disk (`footprint`). The last two are the sizes of
the v0.4.1 release assets on GitHub.

## What is not comparable

- **Add-ins and services.**
  - SOLIDWORKS started with the add-ins this installation loads at start, and their memory and CPU count in its
    figures.
  - That includes the 3DEXPERIENCE Marketplace add-in. The first time the harness started SOLIDWORKS, it asked
    whether to keep loading it, and the harness answered "Yes, continue". The question did not come up again
    in the measured launches, so its wait is not in the start time. No add-in settings were changed.
  - `sldProcMon.exe` and the `mwdatabasebackupapp.exe` processes are also counted.
- **"Usable" differs.** openswx is usable when its page has loaded. SOLIDWORKS is usable when it reports that
  its start-up has finished. In both programs a user could click in the window at about that moment.
- **Working sets are summed** across each program's processes, so memory shared between processes is counted
  more than once. Both programs have eight or more processes. Private bytes do not double-count.
- **The rebuild row is not comparable.** SOLIDWORKS' `ForceRebuild3` recomputes every feature. openswx's
  `rebuild` reuses the stored result of each feature that has not changed, so with nothing changed it does
  almost no work. Neither number is used in the summary.
- **Opening a `.sldprt` is different work.**
  - SOLIDWORKS reads its own file; the samples were saved by an older version and may be converted on open.
  - openswx starts a separate Python process, decodes the feature history, rebuilds it on OpenCASCADE and
    checks the result against the stored body. Starting that process is part of every open.
  - Setting `OPENSWX_ISOLATE=off` rebuilds in the app's own process instead; it was not measured.
- **The files hold different things.** openswx's `.oswprt` holds the commands that built the part, while
  SOLIDWORKS' `.sldprt` also holds the solid, its tessellation and a preview image. Both were saved by the
  program's current version.
- **How openswx was run.** It ran from source through a virtual environment's launcher process, with the
  WebView2 DevTools port open and a WebView2 profile of its own. The installed desktop app was not measured.
- **The rest of the machine.** Both programs used the same GPU and driver, but they draw differently:
  SOLIDWORKS through OpenGL and openswx through WebGL in Edge WebView2. The OS file cache was warm for both,
  since a warm-up launch came first. A copy of openswx the user had open, other desktop applications and the
  Claude desktop app were running throughout, with 1.7 to 3 GB of RAM free.

## Repeating it

```
python tools/bench_vs_sw.py sysinfo -o results.jsonl
python tools/bench_vs_sw.py footprint -o results.jsonl
python tools/bench_vs_sw.py run openswx -o results.jsonl --corpus "<SOLIDWORKS samples folder>" --parts \
    "tutorial/lesson1/tutor1a.sldprt" "tutorial/lesson1/tutor1d.sldprt" "tutorial/surfaces/nozzle.sldprt" \
    "tutorial/tolanalyst/introduction/round cover plate.sldprt"
python tools/bench_vs_sw.py run solidworks -o results.jsonl --corpus ... --parts ... --sw-answer "Yes, continue"
python tools/bench_vs_sw.py report results.jsonl
```

Close the other program first. The harness refuses to start while SOLIDWORKS is already running.
`--sw-answer` only matters when SOLIDWORKS asks a question at start. The parts are copied to a temporary folder,
and everything either program saves is deleted at the end.
