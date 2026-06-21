# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Lean always-on guide. Deeper detail is lazy-loaded — read the rules files below only when a task needs them:

- **`.claude/rules/config-reference.md`** — exact JSON/TXT field formats for every config file (`apps/`, `sources/`, `patches/`, `arch-config.json`, `patch-config.json`, `manifest.json`). Read before editing any config.
- **`.claude/rules/operations.md`** — runbook: add an app, fix a failing build, update a patch source, trigger/force a build, pipeline stage detail, release/manifest strategy. Read for operational tasks.

## What this is

Automated daily pipeline that downloads stock APKs, patches them with the ReVanced/Morphe toolchains, signs, and publishes 60+ enhanced Android apps to a single rolling GitHub release. No application code ships — the repo is the build engine (`src/`, `scripts/`) plus declarative config (`patch-config.json`, `arch-config.json`, `apps/`, `sources/`, `patches/`). Runs on GitHub Actions; rarely run end-to-end locally.

## Build / run

Build one app locally (Python 3.11+, JRE, `zip`, `apksigner` on PATH required):

```bash
pip install -r requirements.txt
APP_NAME="youtube" SOURCE="morphe" python -m src
APP_NAME="youtube" SOURCE="morphe" python -m src   # builds all arches from arch-config.json
```

`python -m src` (entry: `src/__main__.py`) is the whole build unit. It reads `arch-config.json` itself and loops over every arch for the `(APP_NAME, SOURCE)` pair, so one invocation can emit multiple APKs. No test suite, no linter configured.

Run the incremental planner locally (prints plan; writes to GitHub only when `gh`/`GH_TOKEN` present):

```bash
python scripts/check_app_updates.py
FORCE_FULL_REBUILD=true python scripts/check_app_updates.py
```

## Architecture

Pipeline (`.github/workflows/patch.yml`), 6 stages:

```text
download-tools → check-updates → patch-apps → build-apps (matrix) → create-single-release → cleanup
```

- **check-updates** runs `scripts/check_app_updates.py` — the incremental planner. It expands `patch-config.json` × `arch-config.json` into the full `(app, source, arch)` matrix, then compares each entry against `manifest.json` (an asset on the `latest` release) on two axes: the app's configured `version` and a **source signature** (live release tag + `published_at` + asset digests of every repo in `sources/<source>.json`). Changed/missing → rebuild; unchanged → carry the existing APK over untouched.
- **build-apps** is a `fail-fast: false` matrix; each cell runs `python -m src`. One app failing never cancels the others.
- **create-single-release** uploads rebuilt + carried-over APKs + `manifest.json` to the one rolling `latest` tag via `gh release upload --clobber`. No per-day tags.

The planner is **fail-safe**: any unexpected error → full-rebuild fallback (`emit_full_rebuild`). Never let a planning change make the workflow hard-fail — preserve that fallback.

`src/__main__.py::run_build` chooses toolchain and APK at runtime, not from config:

- **Toolchain (Morphe vs ReVanced)** is inferred from the *downloaded files* (CLI filename, then patch extension `.mpp`/`.rvp`), then a source-name heuristic. Patch flags differ per toolchain and per ReVanced CLI major version (v6+ uses `patch -p ... -b`).
- **APK source** is tried in fallback order APKMirror → APKPure → Uptodown → Aptoide; first success wins.
- **Version**: pinned `version` in app config → only that; else ask the CLI for supported versions and try them high→low, retrying older ones on fingerprint/patch failure; else store's latest.
- **BUNDLE / split APKs** are merged to one `.apk` with APKEditor before patching. Non-universal builds strip other ABIs' `lib/` with `zip --delete`.
- **Signing** always uses `keystore/public.jks` (alias/password all literally `public`) so installed apps update in place.

## Gotchas

- **Case-sensitive on the Linux runner.** `sources/Hoo.json` ≠ `sources/hoo.json`; the `source` value in `patch-config.json` must match filename casing exactly. The planner has a case-insensitive fallback for signatures; the build downloader does not.
- **Empty `patches/<app>-<source>.txt` is valid** = apply all patches. Not an error; do not confuse with a missing file.
- Tool cache key is `hashFiles('patch-config.json', 'arch-config.json')` — editing either invalidates the cached CLI/patches.
- `git commit` is the user's responsibility — never commit. Mutating commands need explicit approval.
