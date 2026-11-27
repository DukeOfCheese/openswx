# openswx-gears

A small example of an openswx plugin. It adds one command, `spur_gear`, which builds an external involute
spur gear as a new part. The point is the packaging and the API, not gear design: the flanks are sampled and
interpolated with a closed spline, so the tooth tips are slightly rounded.

A plugin can also add file formats (a reader and/or a writer for an extension); see
[docs/plugins.md](../../docs/plugins.md) for the whole plugin API and a minimal format plugin.

## Install

From a checkout of openswx:

```bash
python -m venv .venv
.venv/bin/pip install -e . -e examples/openswx-gears    # Windows: .venv\Scripts\pip
```

From an installed openswx, drop the `-e .`:

```bash
pip install -e examples/openswx-gears
```

The package declares an entry point in the `openswx.plugins` group:

```toml
[project.entry-points."openswx.plugins"]
gears = "openswx_gears"
```

openswx imports every such module at start-up (`openswx/system/plugins.py`). The commands it registers are
ordinary commands: they show up in `POST /api/command`, in the Python API and as MCP tools in the `plugins`
toolset (`enable_toolset("plugins")`). `OPENSWX_PLUGINS=off` starts without plugins; a plugin that raises on
import is skipped and `list_plugins` reports why.

## Use

Python:

```python
from openswx import session as S

gear = S.call("spur_gear", {"module": 2.0, "teeth": 20, "width": 12.0, "bore": 10.0})
print(gear["volume_mm3"], gear["pitch_diameter"])
```

REST:

```bash
curl -X POST http://127.0.0.1:8765/api/command \
  -H "Authorization: Bearer $(python -m openswx token)" \
  -H "Content-Type: application/json" \
  -d '{"name": "spur_gear", "args": {"module": 2.0, "teeth": 20, "width": 12.0, "bore": 10.0}}'
```

MCP clients call it as the tool `spur_gear` after `enable_toolset("plugins")`.

## Uninstall

`pip uninstall openswx-gears`; openswx no longer sees the entry point and stops loading the command.
