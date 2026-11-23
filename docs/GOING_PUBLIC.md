# Going public: checklist

What to do, in order, when the repository is switched from private to public. The files a public project
needs are already in the tree (README, CONTRIBUTING, AGENTS, SECURITY, CODE_OF_CONDUCT, CHANGELOG, issue
forms, pull request template, CODEOWNERS, Dependabot, labels).

## Before switching

- [x] **History scan (2026-11-23).** No `.sldprt` / `.sldasm` / `.slddrw` ever committed. The Parasolid test
      files in `tests/data/xt/` are simple shapes modelled for the tests, with `USER=unknown` and no site.
      No local user names, absolute home paths or credential patterns anywhere in history. Every commit
      uses the GitHub no-reply address.
- [ ] Run the scan again just before switching, in case something was added since:
      `git log --all --name-only --format= | grep -iE "\.(sldprt|sldasm|slddrw)$"` should print nothing.
- [ ] Decide on the name. "openswx" carries "SW"; the README and NOTICE disclaim any affiliation and describe
      the SOLIDWORKS link as compatibility only, which is the usual practice, but a name with no reference to
      another company's product removes the question entirely. Renaming later is costlier (package name, file
      extensions `.oswprt`, URLs).
- [ ] Tag the first release (`git tag v0.1.0 && git push origin v0.1.0`): the release workflow builds the
      Windows and macOS installers and attaches them, so the README's install table has something to point at.

## Repository settings (GitHub → Settings)

- [ ] **General**: description ("Open-source parametric CAD with a familiar layout and a built-in MCP server
      for AI agents"), website, topics: `cad`, `parametric-cad`, `mcp`, `model-context-protocol`,
      `opencascade`, `step`, `mechanical-engineering`, `ai-agents`. Turn on *Discussions* (the issue chooser
      links to it); turn off *Wiki* (docs live in the repository). Merge button: allow *squash merging* only,
      with the PR title as the commit message, and *automatically delete head branches*.
- [ ] **Social preview** image (Settings → General): a 1280×640 screenshot of the UI with a model.
- [ ] **Branch protection** for `main` (Settings → Rules → Rulesets): require a pull request, require the
      `test` workflow's checks to pass, block force pushes and deletion. Allow yourself to bypass if you work
      alone.
- [ ] **Security**: turn on *Private vulnerability reporting* (SECURITY.md and the issue chooser link to it),
      *Dependabot alerts* and *security updates*, *secret scanning* with *push protection*, and *CodeQL*
      default setup (Python and JavaScript). All free for public repositories.
- [ ] **Actions**: Settings → Actions → General → *Fork pull request workflows*: require approval for first-time
      contributors. Workflow permissions: read-only by default (the release workflow asks for `contents: write`
      itself).
- [ ] **Labels**: `python tools/sync_labels.py` (creates the labels in `.github/labels.yml`; needs `gh`).

## Worth doing soon after

- [ ] **Code signing** (docs/GAPS.md I2): a Windows code-signing certificate (or Azure Trusted Signing) and an
      Apple Developer ID, so installers open without warnings. Set `OPENSWX_CODESIGN_IDENTITY` and
      `OPENSWX_INSTALLER_IDENTITY` as described in the README, and add the secrets to the release workflow.
- [ ] **PyPI**: publish `openswx` so `pip install openswx` works (check the name is free first), with a
      trusted-publishing workflow rather than a stored token.
- [ ] **MCP registry**: list the server in the MCP registry and client directories so agent users find it.
- [ ] **Screenshots and a short GIF** at the top of the README: a part being built by an agent, live.
- [ ] A handful of `good first issue`s from `docs/GAPS.md`, written with the feature-request form, so new
      contributors have somewhere to start.
- [ ] **CITATION.cff** if people may cite it in papers (the simulation and SOLIDWORKS-format work).
