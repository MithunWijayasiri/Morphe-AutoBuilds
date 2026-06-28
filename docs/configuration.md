# Configuration

## App selection (`patch-config.json`)

```json
{
  "patch_list": [
    { "app_name": "youtube", "source": "morphe" },
    { "app_name": "instagram", "source": "piko" }
  ]
}
```

`source` maps to `sources/<source>.json`. The `sources/` directory holds patch-toolchain definitions; `apps/` holds per-app download configs.

## Architecture matrix (`arch-config.json`)

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

## App config (`apps/<platform>/<app>.json`)

Organized by download mirror. Pipeline tries platforms in order until one succeeds: APKMirror → APKPure → Uptodown → Aptoide.

APKMirror format:

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

`"version": ""` → downloads latest. APKPure/Uptodown/Aptoide use a minimal subset (`name`, `package`, `version`).

## Patch rules (`patches/<app>-<source>.txt`)

`+` force-includes a patch, `-` excludes. Empty file → all patches apply.

```text
+ microg-support
- custom-branding
```

## Adding / removing apps

See [operations runbook](.claude/rules/operations.md).
