# Build locally

## Prerequisites

- Python 3.11+
- Java Runtime Environment
- `zip` utility
- `apksigner` (Android SDK Build-Tools)

## Environment variables

| Variable | Required | Description |
| :--- | :---: | :--- |
| `APP_NAME` | ✅ | App name matching `apps/<platform>/<app>.json` |
| `SOURCE` | ✅ | Patch source matching `sources/<source>.json` |

## Run

```bash
git clone https://github.com/MithunWijayasiri/Morphe-AutoBuilds.git
cd Morphe-AutoBuilds

pip install -r requirements.txt

export APP_NAME="youtube"
export SOURCE="morphe"
python -m src
```

Builds all architectures defined in `arch-config.json` for the given `(APP_NAME, SOURCE)` pair. Defaults to `universal` if no entry exists.

## Force a full rebuild on GitHub Actions

`patch.yml` → Run workflow → enable `force_full_rebuild`. Skips the incremental check and rebuilds everything.

## Manual build (single app)

`manual-patch.yml` → Run workflow. Accepts app name, source, architecture, optional pinned version, and a replace-in-release toggle.
