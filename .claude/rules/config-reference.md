# Config reference

Exact field formats for every config file. Read when editing config; not needed for general code work.

## `patch-config.json`

Master list of apps to build. `source` maps to `sources/<source>.json` (case-sensitive).

```json
{ "patch_list": [ { "app_name": "youtube", "source": "morphe" } ] }
```

## `arch-config.json`

Per-`(app, source)` arch overrides. Absent entry ⇒ `["universal"]` only.

```json
[ { "app_name": "youtube", "source": "morphe", "arches": ["arm64-v8a", "armeabi-v7a", "universal"] } ]
```

Valid arches: `arm64-v8a`, `armeabi-v7a`, `universal`.

## `apps/<platform>/<app>.json`

APK locator, one file per store. `version: ""` ⇒ latest at build time (common case). `download_platform` tries platforms in order: `apkmirror` → `apkpure` → `uptodown` → `aptoide`; first existing config + successful download wins.

**apkmirror** — richest schema:
```json
{ "org": "google-inc", "name": "youtube", "type": "APK",
  "arch": "universal", "dpi": "nodpi",
  "package": "com.google.android.youtube", "version": "" }
```
- `type`: `APK` or `BUNDLE` (BUNDLE/split → merged with APKEditor before patching).
- `dpi`: e.g. `nodpi`, `120-640dpi`.
- optional `release_prefix`: overrides the APKMirror release-page slug when it differs from `name`.

**apkpure / uptodown / aptoide** — minimal:
```json
{ "name": "duolingo", "package": "com.duolingo", "version": "" }
```
- uptodown may add `"type": "XAPK"` (split → merged like BUNDLE).

## `sources/<source>.json`

Patch-toolchain definition. JSON array; **first element is a display-name entry only** (`{"name": "..."}`, no download). Remaining elements are repos to download (CLI + patches).

```json
[
  { "name": "piko-patches" },
  { "user": "MorpheApp", "repo": "morphe-cli", "tag": "latest" },
  { "user": "crimera", "repo": "piko", "tag": "latest" }
]
```

Per-repo `provider` (default `github`):
- **github**: `{ "user", "repo", "tag" }`
- **gitlab**: `{ "provider": "gitlab", "project": "Group/Proj", "tag" }`
- **codeberg**: `{ "provider": "codeberg", "user", "repo", "tag" }`

`tag` values: a literal tag, `latest`, `""`/`dev`/`prerelease` (pick most-recent / dev / prerelease release).

**Bundle form** — single object, not an array:
```json
{ "name": "areteruhiro-patches-bundle", "bundle_url": "https://.../bundle.json" }
```
Downloads patches+integrations from the bundle JSON; ReVanced CLI fetched separately.

## `patches/<app>-<source>.txt`

Per-line patch overrides. **Empty file is valid** = apply all default patches.

```text
+ microg-support      # force include (-e)
- custom-branding     # exclude (-d)
```

Filename is `<app>-<source>.txt`; a few legacy files use `<app>.txt`.

## `manifest.json` (release asset, not in repo)

Incremental-build state on the `latest` release. Keyed `app|source|arch`.

```json
{ "entries": { "youtube|morphe|universal": {
    "app_name": "youtube", "source": "morphe", "arch": "universal",
    "config_version": "", "source_sig": "<tag@published@updated|assets>",
    "apk": "youtube-universal-morphe-v19.0.apk" } } }
```

`config_version` = app config `version`. `source_sig` = combined live release signature of the source repos. Mismatch on either, or a missing `apk`, triggers a rebuild.
