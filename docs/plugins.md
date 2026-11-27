# Writing an openswx plugin

A plugin is an ordinary Python package. openswx imports it at start-up, and while it is imported it can add

- **commands**, which become REST commands, Python calls and MCP tools like the built-in ones, and
- **file formats**, which open and export through every path the built-in formats use.

`examples/openswx-gears/` is a complete command plugin. This page covers both kinds; the code is in
`openswx/system/plugins.py` and `openswx/exchange/formats.py`.

## Packaging

Declare an entry point in the `openswx.plugins` group. Its value is the module openswx imports:

```toml
[project.entry-points."openswx.plugins"]
boxtext = "openswx_boxtext"
```

`pip install` the package into the same environment as openswx and restart openswx. `list_plugins` shows each
plugin with the commands and formats it added, or the error it raised while loading. A plugin that fails to
import is skipped; it cannot stop openswx starting. `OPENSWX_PLUGINS=off` starts openswx without any plugins.

## Commands

Decorate a function with `openswx.session.command`. Its docstring is the MCP tool description, so write it for
an agent that has never seen your code: what it does, the arguments and their units (millimetres, degrees), and
what it returns.

```python
from openswx.session import APP, command

@command(category="gears")
def spur_gear(module: float, teeth: int, width: float) -> dict:
    """Build an external involute spur gear as a new part ... Returns {"volume_mm3", "pitch_diameter"}."""
    ...
```

Plugin commands are in the `plugins` MCP toolset (`enable_toolset("plugins")`).

## File formats

```python
from openswx.system.plugins import register_format

register_format(extension, name, *, reader=None, writer=None, id=None)
```

| Argument | Meaning |
| --- | --- |
| `extension` | `".ncx"`, or a list of extensions (`[".ncx", ".ncxz"]`); the first is used for new files. Case does not matter. |
| `name` | What dialogs and menus call the format, e.g. `"Neutral CAD eXchange"`. |
| `reader(path)` | Reads the file. Returns a build123d `Shape` (or an OCP `TopoDS_Shape`) with at least one solid, or `(shape, notes)` where `notes` is a list of strings kept with the imported feature. |
| `writer(shape, path)` | Writes the active document's body (a build123d `Shape`, in millimetres) to `path`. |
| `id` | The name `export_file` takes as `format`. Default: the extension without the dot. |

Give a reader, a writer or both. With a **reader**, the extension:

- opens as a new part with `open_part`, File > Open (browser and desktop), a double-click on the file, and
  `openswx FILE` on the command line; the shape becomes the part's Imported feature;
- imports into the active part with `import_file`, and works as an assembly component;
- is offered in the Windows "Open with" list after `openswx register-file-types` (openswx does not take the
  extension over as the default program).

With a **writer**, the extension:

- exports with `export_file` (by `format`, or inferred from the path's extension);
- is listed under File > Export and in Save As (a Save As name ending in the extension exports);
- is a target of `openswx convert IN OUT`.

`list_file_formats` returns every format, built in or from a plugin, with what it can do; the web UI reads it to
build its Open filter and Export menu.

A plugin cannot take over a built-in extension, and two plugins cannot claim the same one: `register_format`
raises `openswx.exchange.formats.FormatError`, and `list_plugins` reports it as the plugin's load error. An
exception from your reader or writer is reported to the user as `<name> reader (plugin <plugin>) failed: ...`,
and a file that cannot be read leaves no empty part behind.

### A minimal format plugin

A made-up format, `.boxtxt`: one line, `box LENGTH WIDTH HEIGHT` in millimetres.

```
openswx-boxtext/
  pyproject.toml
  openswx_boxtext/__init__.py
```

`pyproject.toml`:

```toml
[project]
name = "openswx-boxtext"
version = "0.1.0"
dependencies = ["openswx"]

[project.entry-points."openswx.plugins"]
boxtext = "openswx_boxtext"
```

`openswx_boxtext/__init__.py`:

```python
"""Box text files (.boxtxt) for openswx: 'box L W H' in millimetres."""
from pathlib import Path

from build123d import Box

from openswx.system.plugins import register_format


def read_boxtxt(path):
    kind, *dims = Path(path).read_text().split()
    if kind != "box" or len(dims) != 3:
        raise ValueError("expected 'box LENGTH WIDTH HEIGHT'")
    return Box(*map(float, dims)), [f"box {' x '.join(dims)} mm"]


def write_boxtxt(shape, path):
    size = shape.bounding_box().size  # (a real format writes the geometry; this one keeps only the extents)
    Path(path).write_text(f"box {size.X} {size.Y} {size.Z}\n")


register_format(".boxtxt", "Box text", reader=read_boxtxt, writer=write_boxtxt)
```

Then:

```bash
pip install -e openswx-boxtext
openswx part.boxtxt                         # opens in the desktop app
openswx convert part.boxtxt part.step       # and converts like any other format
```

```python
from openswx import session as S

S.call("open_part", {"path": "part.boxtxt"})
S.call("export_file", {"path": "copy.boxtxt"})
```

`tests/test_plugin_formats.py` installs a plugin like this one and checks every path above.

### Limits

- A reader produces one shape, which becomes one Imported feature: there is no feature history, assembly
  structure or drawing from a plugin format yet.
- The macOS app's file types come from its `Info.plist`, which is fixed when the app is built, so in Finder a
  plugin's extension opens in openswx with "Open With > Other..." rather than being listed.
