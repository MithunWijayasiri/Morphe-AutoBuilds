<div align="center">

# Morphe AutoBuilds

[![Daily Build](https://img.shields.io/github/actions/workflow/status/MithunWijayasiri/Morphe-AutoBuilds/patch.yml?label=Daily%20Build&style=for-the-badge&color=2ea44f)](https://github.com/MithunWijayasiri/Morphe-AutoBuilds/actions/workflows/patch.yml)
[![Latest Release](https://img.shields.io/github/v/release/MithunWijayasiri/Morphe-AutoBuilds?style=for-the-badge&label=Latest%20Release&color=0366d6)](https://github.com/MithunWijayasiri/Morphe-AutoBuilds/releases/latest)
[![Python Version](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/MithunWijayasiri/Morphe-AutoBuilds?style=for-the-badge&color=orange)](LICENSE)

Automated pipeline that builds patched APKs for 60+ Android apps using the ReVanced/Morphe toolchain. Runs daily on GitHub Actions — no root required.

[![View Latest Release](https://img.shields.io/badge/View%20Latest%20Release-0A0A0A?style=flat&logo=github&logoColor=white)](https://github.com/MithunWijayasiri/Morphe-AutoBuilds/releases/latest)
[![Report Bug](https://img.shields.io/badge/Report%20Bug-0A0A0A?style=flat&logo=github&logoColor=white)](https://github.com/MithunWijayasiri/Morphe-AutoBuilds/issues)

</div>

---

## Quick Downloads

All APKs rebuilt daily at 06:00 UTC.

| Mirror | Link |
| :--- | :--- |
| **GitHub Releases** | [Download Latest](https://github.com/MithunWijayasiri/Morphe-AutoBuilds/releases/latest) |

---

## Features

- **Fully automated.** GitHub Actions cron runs daily — zero intervention.
- **Multi-architecture.** Per-app configs for `arm64-v8a`, `armeabi-v7a`, `universal` — smaller APKs, better device match.
- **Multi-source downloads.** Falls back across APKMirror, APKPure, Aptoide, Uptodown.
- **Incremental builds.** Only rebuilds apps where versions or patches changed. Unchanged APKs carried over from previous release.
- **Granular patch control.** Text files per app/source to include or exclude specific patches.
- **Auto-signed.** Consistent public keystore — install directly after download.
- **Single rolling release.** One `latest` tag, assets updated in-place. Clean for update checkers.

---

## Configuration

### App selection (`patch-config.json`)

```json
{
  "patch_list": [
    { "app_name": "youtube", "source": "morphe" },
    { "app_name": "instagram", "source": "piko" }
  ]
}
```

`source` value maps to `sources/<source>.json`. The `sources/` directory contains patch-source definitions (tool repositories), separate from `apps/` which holds per-app download configs.

### Architecture matrix (`arch-config.json`)

```json
[
  {
    "app_name": "youtube",
    "source": "morphe",
    "arches": ["arm64-v8a", "armeabi-v7a", "universal"]
  }
]
```

Absent entry → builds `["universal"]` only.

### App config (`apps/<platform>/<app>.json`)

Configs live under `apps/` organized by download mirror platform (not by patch source).

| Platform | Directory |
| :--- | :--- |
| APKMirror | `apps/apkmirror/` |
| APKPure | `apps/apkpure/` |
| Uptodown | `apps/uptodown/` |
| Aptoide | `apps/aptoide/` |

Each app can have configs on multiple platforms — the pipeline tries them in order until one succeeds.

APKMirror format (example at `apps/apkmirror/youtube.json`):

```json
{
  "org": "google-inc",
  "name": "youtube",
  "type": "APK",
  "arch": "universal",
  "dpi": "nodpi",
  "package": "com.google.android.youtube",
  "version": ""
}
```

`"version": ""` → downloads latest.

### Patch rules (`patches/youtube-morphe.txt`)

`+` to force-include, `-` to exclude. Empty file → all patches apply by default.

```text
# Essential patches
+ microg-support
+ premium-heading

# Exclusions
- custom-branding
```

---

## Build locally

### Prerequisites

- Python 3.11+
- Java Runtime Environment
- `zip` utility
- `apksigner` (Android SDK Build-Tools)

| Variable | Required | Description | Valid values |
| :--- | :---: | :--- | :--- |
| `APP_NAME` | ✅ Yes | App name (matches file in `apps/<platform>/<app>.json`) | Any configured app name |
| `SOURCE` | ✅ Yes | Patch source (matches file in `sources/<source>.json`) | `morphe`, `revanced`, `piko`, etc. |
| `ARCH` | ❌ No | Target architecture. Falls back to `arch-config.json`, then `"universal"`. | `arm64-v8a`, `armeabi-v7a`, `universal` |

```bash
git clone https://github.com/MithunWijayasiri/Morphe-AutoBuilds.git
cd Morphe-AutoBuilds

pip install -r requirements.txt

export APP_NAME="youtube"
export SOURCE="morphe"
python -m src

# Optional architecture override (defaults from arch-config.json):
export ARCH="arm64-v8a"
python -m src
```

---

## GitHub Actions workflows

### Daily build (`patch.yml`)

- **Schedule:** 06:00 UTC daily. Also triggerable via `workflow_dispatch` with `force_full_rebuild` flag.
- **Pipeline:** download-tools → check-updates → patch-apps → build-apps (matrix) → create-single-release → cleanup
- **Output:** Single rolling `latest` release. Only changed apps rebuilt. Unchanged APKs carried over.

### Manual build (`manual-patch.yml`)

- **Trigger:** Manual via "Run workflow" button.
- **Options:** App name, source, architecture, specific version, and whether to replace in release.

---

## Contributing

1. Fork the repo.
2. Create a feature branch.
3. Test locally (`python -m src` with your app's env vars).
4. Open a pull request.

---

## Disclaimer

These are automated builds using publicly available ReVanced/Morphe tools and patches. Not affiliated with any app developers. Use at your own risk.

**GmsCore / MicroG-RE** required for non-root patched apps to function. Download from [the official releases](https://github.com/revanced/gmscore/releases/latest).

---

<div align="center">

**If useful, star the repo.**

</div>
