# Security policy

## Reporting a vulnerability

Please report it **privately**, through GitHub's
[private vulnerability reporting](https://github.com/DukeOfCheese/openswx/security/advisories/new)
(*Security → Report a vulnerability*). Do not open a public issue, discussion or pull request for it.

Include what an attacker can do, the steps or a proof of concept, and the version and platform. You will get
an acknowledgement within 7 days and a plan within 30. Fixes are released before the details are published,
and you are credited in the advisory unless you would rather not be.

## Supported versions

openswx is in alpha: only the latest release (and `main`) gets security fixes.

## What is in scope

openswx runs a local web server, accepts commands from AI agents and reads files from other programs, so these
are the areas we care most about:

- **The local server** (`openswx/security.py`): reaching the REST or MCP endpoints without the token, from
  another computer without `--allow-remote`, or from a web page in the browser (DNS rebinding, cross-site
  requests); a view-only `--share` link that can change anything.
- **Agent confinement**: an agent reading or writing files outside `--workspace` / `OPENSWX_WORKSPACE`.
- **File readers**: a crafted `.sldprt`, Parasolid, STEP, IGES, STL, 3MF, DXF or `.oswprt` file that runs code,
  writes files, or takes down the application (these readers run in a separate, time-limited process where
  that applies).
- **Packaging**: installers or update checks that could be made to run someone else's code.

Out of scope: anything that needs an attacker to already run code as your user; a plugin you installed
yourself doing what plugins can do (plugins are ordinary Python packages with full access, like any you
`pip install`); results that are only wrong, not unsafe (report those as bugs).
