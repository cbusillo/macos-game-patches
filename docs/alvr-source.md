# ALVR Source Of Truth

This repository does not vendor ALVR source code.

ALVR runtime code lives in a dedicated fork checkout at:

- `~/Developer/ALVR`

## Why

- Keep this repo focused on macOS VR integration and patch workflows.
- Keep ALVR runtime history in its own fork for clean rebases and PRs.
- Avoid bloating this repo with third-party upstream source.

## Expected Remotes In `~/Developer/ALVR`

- `origin`: `git@github.com:cbusillo/ALVR.git`
- `upstream`: `git@github.com:alvr-org/ALVR.git`

## Update Workflow

In `~/Developer/ALVR`:

```bash
git fetch upstream --prune
git checkout master
git reset --hard upstream/master
git push origin master --force
```

## Locking Current Runtime Revision

Record the currently referenced ALVR revision into this repo:

```bash
python3 tools/alvr_lock.py
```

This writes:

- `docs/alvr-lock.json`

Current lock file should always be committed with integration changes.

That file is the integration reference for which ALVR commit this repo is
currently targeting.
