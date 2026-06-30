# CLAUDE.md

## What this is

**Morphe AutoBuilds Lite** . Daily pipeline: downloads stock APKs → patches with ReVanced/Morphe → signs → publishes multiple apps to a single rolling GitHub release. Repo is the build engine (`src/`, `scripts/`) + declarative config (`patch-config.json`, `arch-config.json`, `apps/`, `sources/`, `patches/`). Runs on GitHub Actions. `docs/` = GitHub Pages site (`index.html` landing page + `build.md`/`configuration.md` docs).

## Build / run

```bash
pip install -r requirements.txt
APP_NAME="youtube" SOURCE="morphe" python -m src   # builds all arches from arch-config.json
FORCE_FULL_REBUILD=true python scripts/check_app_updates.py  # force planner locally
```

## Architecture

Pipeline: `download-tools → check-updates → patch-apps → build-apps (matrix) → create-single-release → cleanup`

- **check-updates**: incremental planner (`check_app_updates.py`) — compares `patch-config.json` × `arch-config.json` against `manifest.json` on version + source signature. Changed/missing → rebuild; unchanged → carry over.
- **build-apps**: `fail-fast: false` matrix, one cell per `(app, source)`. One failure never cancels others.
- **create-single-release**: uploads rebuilt + carried APKs + `manifest.json` to `latest` tag via `gh release upload --clobber`.

`src/__main__.py::run_build` runtime decisions:
- **Toolchain**: inferred from downloaded filenames (CLI name → patch extension → source-name heuristic).
- **APK source**: APKMirror → APKPure → Uptodown → Aptoide, first success wins.
- **Version**: pinned in app config → CLI-supported versions high→low (retries on fingerprint failure) → store latest.
- **BUNDLE/split**: merged with APKEditor. Non-universal builds strip other ABIs' `lib/`.
- **Signing**: always `keystore/public.jks` (alias/pass: `public`) for in-place updates.

The planner is fail-safe: any error → `emit_full_rebuild`. Never let planning changes hard-fail the workflow.

## Gotchas

- **Case-sensitive on Linux runner**: `source` in `patch-config.json` must match `sources/<source>.json` filename casing exactly.
- **Empty `patches/<app>-<source>.txt`** = apply all patches. Valid, not an error.
- **Tool cache** keyed on `hashFiles('patch-config.json', 'arch-config.json')` — editing either busts the cache.
- `git commit` is the user's responsibility — never commit.
