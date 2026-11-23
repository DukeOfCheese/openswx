<!-- One topic per pull request. The checklist below is the review checklist. -->

## What and why

<!-- What changes, and why. Link the issue: "Fixes #123". -->

## How it was checked

<!-- The tests added or changed, and what they compare against (analytic value, closed form, a stored body).
     For UI changes, a screenshot. For SOLIDWORKS rebuild changes, the survey comparison (gained / lost). -->

## Checklist

- [ ] Follows the clean-room rule: no SOLIDWORKS code, artwork, sample files or user-saved SOLIDWORKS files
- [ ] Tests added or updated, checking against something independent
- [ ] `python -m pytest -q` passes locally
- [ ] New or changed commands: typed arguments, a docstring written for an agent, and `python tools/gen_command_docs.py` re-run
- [ ] New Python modules added to `hiddenimports` in `packaging/openswx.spec`
- [ ] Third-party code or assets: licence compatible with Apache-2.0 and listed in `NOTICE`
- [ ] User-visible change noted in `CHANGELOG.md` under *Unreleased*
