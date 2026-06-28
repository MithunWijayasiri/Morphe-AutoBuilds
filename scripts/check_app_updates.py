#!/usr/bin/env python3
"""
Incremental update checker for Morphe AutoBuilds.

Strategy:
1. Read patch-config.json + arch-config.json -> full expected matrix.
2. Fetch existing 'latest' release manifest (manifest.json asset, if present).
3. For each (app, source, arch):
   - Determine current configured app version (from apps/<platform>/<app>.json).
   - Determine current patch-source signature (latest GitHub release tag(s) of
     repos listed in sources/<source>.json).
   - Compare to manifest.json -> if changed OR APK missing -> needs build.
4. Output:
   - GitHub Actions outputs: build_matrix (JSON), has_updates, total/update counts.
   - File: build_matrix.json    (matrix entries that need rebuild).
   - File: carry_over.json      (existing APK names to re-upload unchanged).
   - File: new_manifest.json    (manifest to upload with the new release).

Force full rebuild: env FORCE_FULL_REBUILD=true (also: any app missing from the
old manifest is rebuilt automatically).

Fail-safe: any unexpected error -> full rebuild matrix is emitted (preserves the
previous always-build behavior so nothing breaks).
"""
import os
import sys
import re
import json
import logging
import importlib
import subprocess
import traceback
from pathlib import Path
from typing import Dict, List, Tuple, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCH_CONFIG = REPO_ROOT / "patch-config.json"
ARCH_CONFIG = REPO_ROOT / "arch-config.json"
SOURCES_DIR = REPO_ROOT / "sources"
APPS_DIR = REPO_ROOT / "apps"

MANIFEST_NAME = "manifest.json"
RELEASE_TAG = "latest"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
FORCE_FULL = os.environ.get("FORCE_FULL_REBUILD", "false").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def write_gh_output(key: str, value: str) -> None:
    """Append key=value (multiline-safe) to GITHUB_OUTPUT, or print locally."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        preview = value if len(value) < 200 else value[:200] + "..."
        print(f"[gh-output] {key}={preview}")
        return
    with open(out, "a", encoding="utf-8") as f:
        if "\n" in value:
            f.write(f"{key}<<EOF_GH\n{value}\nEOF_GH\n")
        else:
            f.write(f"{key}={value}\n")


def run_gh(args: List[str], timeout: int = 120) -> Tuple[int, str, str]:
    """Run `gh ...`; returns (rc, stdout, stderr). Never raises."""
    env = os.environ.copy()
    if GITHUB_TOKEN and "GH_TOKEN" not in env:
        env["GH_TOKEN"] = GITHUB_TOKEN
    try:
        p = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, env=env, timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "gh CLI not found"
    except Exception as e:
        return 1, "", f"{e}"


def load_patch_config() -> List[dict]:
    with PATCH_CONFIG.open("r", encoding="utf-8") as f:
        return json.load(f).get("patch_list", [])


def load_arch_config() -> Dict[Tuple[str, str], List[str]]:
    if not ARCH_CONFIG.exists():
        return {}
    with ARCH_CONFIG.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        (e["app_name"], e["source"]): e.get("arches", ["universal"])
        for e in data
    }


def load_app_config_version(app_name: str) -> str:
    """Return the configured 'version' field from the first matching app config,
    or '' if none is pinned (means 'latest at build time')."""
    cfg, _ = load_app_config(app_name)
    return (cfg.get("version") or "").strip() if cfg else ""


def load_app_config(app_name: str) -> Tuple[Optional[dict], Optional[str]]:
    """Return (config_dict, platform) for the first platform that has an app
    config file, or (None, None) if none exists. The platform name matches the
    provider module (apkmirror/apkpure/uptodown/aptoide) so the caller can use it
    to dispatch version lookups."""
    for platform in ("apkmirror", "apkpure", "uptodown", "aptoide"):
        fp = APPS_DIR / platform / f"{app_name}.json"
        if fp.exists():
            try:
                with fp.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                return data, platform
            except Exception:
                raise
    return None, None


_latest_app_version_cache: Dict[str, str] = {}


def fetch_latest_app_version(app_name: str) -> str:
    """Query the app's store listing for the newest version currently published.

    This lets plan_incremental() detect that a *new app version* shipped even
    when the app config is pinned to "latest" (config 'version' == ''). Without
    it, the only signals we had were the patch-source signature and the (empty)
    config version, so apps that follow upstream's newest release never triggered
    a rebuild.

    Best-effort: any error (network, parse, missing module) returns '' rather
    than raising, so a transient store outage never degrades to a full rebuild.
    """
    if app_name in _latest_app_version_cache:
        return _latest_app_version_cache[app_name]

    resolved = ""
    config, platform = load_app_config(app_name)
    if config and platform:
        try:
            import importlib
            mod = importlib.import_module(f"src.{platform}")
            get_latest = getattr(mod, "get_latest_version", None)
            if callable(get_latest):
                # Provider signatures vary slightly (some take arch), but all
                # accept (app_name, config). Extra kwargs are not used.
                ver = get_latest(app_name, config)
                if isinstance(ver, str) and ver.strip():
                    resolved = ver.strip()
        except Exception as e:
            logging.info(f"  latest-version probe failed for {app_name}/{platform}: {e}")
            resolved = ""

    _latest_app_version_cache[app_name] = resolved
    return resolved


# ---------------------------------------------------------------------------
# Source-signature: detect when patch repos publish new releases
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Also put this script's own dir on the path so we can reuse record_build's
# filename-version parser (kept in one place so the two always agree).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import utils as provider_utils
from record_build import extract_version_from_filename

_repo_sig_cache: Dict[Tuple[str, str, str, str], str] = {}


def fetch_repo_signature(user: str, repo: str, tag: str, provider: str = "github") -> str:
    """Get a stable identifier for the current state of a repo's release.
    Returns 'tag_name@published_at' on success, else a short error sentinel."""
    key = (user, repo, tag, provider)
    if key in _repo_sig_cache:
        return _repo_sig_cache[key]

    try:
        if provider == "gitlab":
            # For GitLab we store the project path in the `repo` slot (see
            # get_source_signature), e.g. "Group/Subgroup/Project".
            sig = _fetch_gitlab_signature(repo, tag)
        elif provider == "codeberg":
            sig = _fetch_codeberg_signature(user, repo, tag)
        else:
            sig = _fetch_github_signature(user, repo, tag)
        _repo_sig_cache[key] = sig
        return sig
    except Exception as e:
        # Sentinel format must contain '@err:' so that
        # _is_unreliable_source_sig() (which looks for '@err:') treats this as an
        # unreliable signature and plan_incremental() falls back to the old one.
        sig = f"{tag}@err:{type(e).__name__}"
        _repo_sig_cache[key] = sig
        logging.warning(f"  {provider} api failed for {user}/{repo}: {e}")
        return sig


def _fetch_default_branch_sha(user: str, repo: str) -> str:
    """Fetch the latest commit SHA of the repo's default branch.

    This makes the source signature sensitive to *any* upstream change, not just
    GitHub Releases. Many patch authors push commits or create tags without a
    Release object; some publish exclusively via pre-releases that ``/releases/
    latest`` ignores. The default-branch SHA flips whenever new code lands, so a
    rebuild is triggered even in those cases. Returns '' on failure (best-effort;
    the release-based tokens still form a valid signature)."""
    try:
        rc, out, _ = run_gh(["api", f"repos/{user}/{repo}", "--jq", ".default_branch"])
        if rc != 0 or not out.strip():
            branch = "main"
        else:
            branch = out.strip()
        rc, out, _ = run_gh([
            "api", f"repos/{user}/{repo}/commits/{branch}", "--jq", ".sha"
        ])
        if rc == 0:
            sha = out.strip()
            if sha:
                # Short SHA is plenty for change detection.
                return sha[:12]
    except Exception:
        pass
    return ""


def _fetch_github_signature(user: str, repo: str, tag: str) -> str:
    if tag == "latest":
        api = f"repos/{user}/{repo}/releases/latest"
    elif tag in ("", "dev", "prerelease"):
        api = f"repos/{user}/{repo}/releases?per_page=10"
    else:
        api = f"repos/{user}/{repo}/releases/tags/{tag}"

    rc, out, err = run_gh(["api", api])
    if rc != 0:
        # Some repos publish ONLY prereleases, so /releases/latest 404s. Fall
        # back to the list endpoint and pick the most recent release of any kind.
        if tag == "latest":
            list_api = f"repos/{user}/{repo}/releases?per_page=10"
            rc2, out2, err2 = run_gh(["api", list_api])
            if rc2 == 0:
                rc, out, err = rc2, out2, err2
                api = list_api
        if rc != 0:
            # No release info at all (repo publishes via commits/tags only).
            # Fall back to a commit-SHA-only signature so we still detect changes.
            sha = _fetch_default_branch_sha(user, repo)
            if sha:
                return f"@|sha:{sha}"
            raise RuntimeError(f"gh api {api} failed: {err.strip()[:80]}")

    data = json.loads(out)

    if isinstance(data, list):
        if tag == "dev":
            data = [r for r in data if "dev" in (r.get("tag_name") or "").lower()]
        elif tag == "prerelease":
            data = [r for r in data if r.get("prerelease")]
        if not data:
            # No releases of the requested kind; use commit SHA so the signature
            # still moves when upstream changes instead of freezing forever.
            sha = _fetch_default_branch_sha(user, repo)
            return f"@|sha:{sha}" if sha else f"@|none"
        data.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        rel = data[0]
    else:
        rel = data

    tag_name = rel.get("tag_name") or rel.get("name") or "?"
    published = rel.get("published_at") or rel.get("created_at") or "?"
    updated = rel.get("updated_at") or ""

    # Some upstream projects update/re-upload release assets without bumping the
    # release tag. In that case, published_at/tag_name may stay the same, so we
    # also incorporate a deterministic "assets signature" to detect changes.
    assets = rel.get("assets") or []
    asset_parts: List[str] = []
    if isinstance(assets, list):
        for a in assets:
            if not isinstance(a, dict):
                continue
            name = (a.get("name") or "").strip()
            if not name:
                continue
            # Prefer strong identifiers when available.
            digest = (a.get("digest") or "").strip()
            a_updated = (a.get("updated_at") or "").strip()
            size = str(a.get("size") or "").strip()
            # Keep this compact but sensitive to real changes.
            # Prefer sha256 digest, else a (size, updated) composite, else either
            # field on its own. (Previous code had a dead `or` chain: the middle
            # operand was a non-empty f-string by construction.)
            if digest:
                token = digest
            elif size and a_updated:
                token = f"{size}@{a_updated}"
            elif a_updated:
                token = a_updated
            else:
                token = size
            asset_parts.append(f"{name}:{token}")
    asset_parts.sort()
    assets_sig = ",".join(asset_parts)

    # Commit SHA of the default branch: detects commits/tags/prereleases that are
    # not reflected in the Release object. Best-effort; '' if unavailable.
    sha = _fetch_default_branch_sha(user, repo)
    sha_part = f"|sha:{sha}" if sha else ""

    # Format: <tag>@<published>@<updated>|<assets_sig><sha_part>
    return f"{tag_name}@{published}@{updated}|{assets_sig}{sha_part}"


def _fetch_gitlab_signature(project: str, tag: str) -> str:
    from urllib.parse import quote
    encoded = quote(project, safe="")
    if tag == "latest":
        api = f"https://gitlab.com/api/v4/projects/{encoded}/releases/permalink/latest"
    elif tag in ("", "dev", "prerelease"):
        api = f"https://gitlab.com/api/v4/projects/{encoded}/releases"
    else:
        api = f"https://gitlab.com/api/v4/projects/{encoded}/releases/{quote(tag, safe='')}"

    data = provider_utils.fetch_json(api)
    if isinstance(data, list):
        data = data[0] if data else {}
    tag_name = data.get("tag_name") or "?"
    published = data.get("released_at") or data.get("created_at") or "?"
    return f"{tag_name}@{published}"


def _fetch_codeberg_signature(user: str, repo: str, tag: str) -> str:
    from urllib.parse import quote
    base = f"https://codeberg.org/api/v1/repos/{user}/{repo}/releases"
    if tag == "latest":
        api = f"{base}/latest"
    elif tag in ("", "dev", "prerelease"):
        api = base
    else:
        api = f"{base}/tags/{quote(tag, safe='')}"

    data = provider_utils.fetch_json(api)
    tag_name = data.get("tag_name") or "?"
    published = data.get("published_at") or "?"
    return f"{tag_name}@{published}"


def _fetch_bundle_signature(bundle_url: str) -> str:
    """Compute a content-based signature for a bundle source.

    Previously this was signed as a *static* `f"bundle:{url}"` string, which
    never changes. So when a bundle published new patch/integration versions,
    the signature stayed identical and `plan_incremental()` saw no change ->
    no rebuild was triggered (even though new patches that support new app
    versions had been published).

    We now fetch the bundle JSON and build a signature from its contents so any
    real change (new asset URL/size) flips the signature. Falls back to a static
    sentinel on failure so we don't mask the issue by force-rebuilding every run.
    """
    try:
        data = provider_utils.fetch_json(bundle_url)
    except Exception as e:
        logging.warning(f"  bundle fetch failed for {bundle_url}: {e}")
        return f"bundle:{bundle_url}@err:{type(e).__name__}"

    if not isinstance(data, dict):
        return f"bundle:{bundle_url}@badjson:not-a-dict"

    tokens: List[str] = []
    for key in ("patches", "integrations"):
        for item in data.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            name = (item.get("name") or "").strip()
            if url or name:
                tokens.append(f"{key}:{name}:{url}")
    tokens.sort()
    body = ",".join(tokens)
    return f"bundle:{body}" if body else f"bundle:{bundle_url}@empty"


_source_sig_cache: Dict[str, str] = {}


def get_source_signature(source: str) -> str:
    """Combine release signatures of every repo declared in sources/<source>.json
    into a single deterministic string."""
    if source in _source_sig_cache:
        return _source_sig_cache[source]

    src_file = SOURCES_DIR / f"{source}.json"
    if not src_file.exists():
        # Case-insensitive fallback
        for f in SOURCES_DIR.glob("*.json"):
            if f.stem.lower() == source.lower():
                src_file = f
                break
    if not src_file.exists():
        sig = f"missing-source:{source}"
        _source_sig_cache[source] = sig
        return sig

    try:
        with src_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        sig = f"unparseable:{e}"
        _source_sig_cache[source] = sig
        return sig

    if isinstance(data, dict) and "bundle_url" in data:
        sig = _fetch_bundle_signature(data["bundle_url"])
        _source_sig_cache[source] = sig
        return sig

    parts: List[str] = []
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            provider = (entry.get("provider") or "github").lower().strip()
            tag = entry.get("tag", "latest")

            # An entry that carries NO repo identifiers (typically the [0]
            # metadata slot, e.g. {"name": "piko-patches"}) is just the patch
            # package's output name -- download_required() treats it the same way
            # (uses it as `name`, downloads from the remaining entries). It is
            # NOT a repo, so it contributes nothing to the signature and must be
            # skipped. We only track entries that actually declare a repo, which
            # keeps the signature honest about what gets downloaded.
            has_github = bool(entry.get("user") and entry.get("repo"))
            has_project = bool(entry.get("project"))
            if provider == "gitlab" and has_project:
                project = entry.get("project")
                parts.append(f"gitlab:{project}@{fetch_repo_signature('', project, tag, provider)}")
            elif provider == "codeberg" and has_github:
                user = entry.get("user")
                repo = entry.get("repo")
                parts.append(f"codeberg:{user}/{repo}@{fetch_repo_signature(user, repo, tag, provider)}")
            elif provider == "github" and has_github:
                user = entry.get("user")
                repo = entry.get("repo")
                parts.append(f"{user}/{repo}@{fetch_repo_signature(user, repo, tag, provider)}")
            # else: metadata-only entry (no repo) -> intentionally skipped.

    sig = ";".join(parts) if parts else f"empty:{source}"
    _source_sig_cache[source] = sig
    return sig


# ---------------------------------------------------------------------------
# Existing release manifest + assets
# ---------------------------------------------------------------------------
def _get_repo_owner_name() -> Optional[Tuple[str, str]]:
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    owner = owner.strip()
    name = name.strip()
    if not owner or not name:
        return None
    return owner, name


def fetch_existing_manifest() -> Optional[dict]:
    rc, _, err = run_gh(["release", "download", RELEASE_TAG,
                         "--pattern", MANIFEST_NAME, "--clobber"])
    if rc != 0:
        msg = err.strip()[:120]
        logging.info(f"No existing '{MANIFEST_NAME}' on '{RELEASE_TAG}' ({msg})")
        return None
    try:
        with open(MANIFEST_NAME, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"Bad manifest.json: {e}")
        return None


def fetch_existing_apk_names() -> List[str]:
    repo = _get_repo_owner_name()
    if repo:
        owner, name = repo
        rc, out, _ = run_gh(
            ["api", f"repos/{owner}/{name}/releases/tags/{RELEASE_TAG}", "--jq", ".id"]
        )
        rel_id = out.strip() if rc == 0 else ""
        if rel_id:
            rc, out, _ = run_gh(
                [
                    "api",
                    "--paginate",
                    f"repos/{owner}/{name}/releases/{rel_id}/assets?per_page=100",
                    "--jq",
                    ".[].name",
                ],
                timeout=300,
            )
            if rc == 0:
                names = [ln.strip() for ln in out.splitlines() if ln.strip()]
                return [n for n in names if n.endswith(".apk")]

    rc, out, _ = run_gh(["release", "view", RELEASE_TAG, "--json", "assets"])
    if rc != 0:
        return []
    try:
        return [
            a.get("name", "")
            for a in json.loads(out).get("assets", [])
            if a.get("name", "").endswith(".apk")
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Matrix planning
# ---------------------------------------------------------------------------
def build_full_matrix() -> List[dict]:
    """Expand patch-config + arch-config into the full per-arch matrix."""
    patch_list = load_patch_config()
    arch_map = load_arch_config()
    matrix: List[dict] = []
    seen = set()
    for entry in patch_list:
        app = entry.get("app_name")
        src = entry.get("source")
        if not app or not src:
            continue
        arches = arch_map.get((app, src), ["universal"])
        for arch in arches:
            key = (app, src, arch)
            if key in seen:
                continue
            seen.add(key)
            matrix.append({"app_name": app, "source": src, "arch": arch})
    return matrix


def make_manifest_key(app: str, source: str, arch: str) -> str:
    return f"{app}|{source}|{arch}"


def _is_unreliable_source_sig(sig: str) -> bool:
    s = (sig or "").lower()
    return (
        "@err:" in s
        or "@badjson:" in s
        or s.startswith("missing-source:")
        or s.startswith("unparseable:")
    )


def _recover_apk_from_release(app: str, arch: str, existing_apks: List[str]) -> str:
    a = (app or "").lower()
    rarch = (arch or "").lower()
    candidates: List[str] = []
    for n in existing_apks:
        nl = (n or "").lower()
        if not nl.endswith(".apk"):
            continue
        if not nl.startswith(f"{a}-{rarch}-"):
            continue
        candidates.append(n)
    candidates.sort()
    return candidates[-1] if candidates else ""


def _is_newer_version(candidate: str, reference: str) -> bool:
    """True if `candidate` is a strictly newer version than `reference`.

    Uses src.utils.normalize_version for the comparison so build-number and
    parenthesised-version conventions match what the build/download code uses.
    Falls back to a plain string inequality if normalization yields nothing,
    and returns False on any parse error (never triggers a rebuild on a guess).
    """
    try:
        cand = provider_utils.normalize_version(candidate)
        ref = provider_utils.normalize_version(reference)
    except Exception:
        return False
    if not cand or not ref:
        return False
    # Pad to equal length so e.g. (4,0,0) vs (4,0,0,1) compare correctly.
    n = max(len(cand), len(ref))
    cand += [0] * (n - len(cand))
    ref += [0] * (n - len(ref))
    return cand > ref




def plan_incremental(full_matrix: List[dict], old_manifest: Optional[dict],
                     existing_apks: List[str]) -> Tuple[List[dict], List[str], dict]:
    """Decide which entries need rebuilding.
    Returns (build_matrix, carry_over_apks, new_manifest_entries)."""
    old_entries = (old_manifest or {}).get("entries", {}) if isinstance(old_manifest, dict) else {}
    existing_apk_set = set(existing_apks)

    build_matrix: List[dict] = []
    carry_over: List[str] = []
    new_entries: dict = {}

    for entry in full_matrix:
        app = entry["app_name"]
        src = entry["source"]
        arch = entry["arch"]
        mkey = make_manifest_key(app, src, arch)

        cur_app_ver = load_app_config_version(app)            # '' if 'latest'
        cur_src_sig = get_source_signature(src)
        old = old_entries.get(mkey)
        old_src_sig = (old or {}).get("source_sig", "")
        if old and old_src_sig and _is_unreliable_source_sig(cur_src_sig):
            cur_src_sig = old_src_sig
        carried_apk = (old or {}).get("apk", "")
        # built_version is the version actually shipped in the carried APK
        # (populated post-build by record_build.py -> merge_manifest.py). Carry
        # it forward so we can compare it against the store's newest version.
        old_built_ver = (old or {}).get("built_version", "")
        if old:
            if not carried_apk or carried_apk not in existing_apk_set:
                recovered = _recover_apk_from_release(app, arch, existing_apks)
                if recovered:
                    carried_apk = recovered
                    # Recovered filename carries its own version; re-derive it
                    # rather than trusting the (possibly stale) stored value.
                    old_built_ver = extract_version_from_filename(recovered)
        # Backfill for manifest entries that predate the built_version field:
        # if the APK is still present but built_version was never recorded,
        # derive it from the filename so the store-version check can fire.
        if not old_built_ver and carried_apk:
            old_built_ver = extract_version_from_filename(carried_apk)

        new_entries[mkey] = {
            "app_name": app,
            "source": src,
            "arch": arch,
            "config_version": cur_app_ver,
            "source_sig": cur_src_sig,
            # apk filename is filled in *after* build by the workflow; for now
            # carry over whatever the old manifest had so we know what to keep.
            "apk": carried_apk,
            # Preserved verbatim; refreshed by the merge step after each build.
            "built_version": old_built_ver,
        }

        reasons: List[str] = []
        if FORCE_FULL:
            reasons.append("force-rebuild")
        if not old:
            reasons.append("new-entry")
        else:
            if old.get("config_version", "") != cur_app_ver:
                reasons.append(f"app-version: {old.get('config_version','')!r}->{cur_app_ver!r}")
            if old.get("source_sig", "") != cur_src_sig:
                reasons.append("patch-source-updated")
            # New app version detection for apps pinned to "latest" (no pinned
            # config version). When config_version is empty, the config_version
            # compare above can never fire, so without this check a brand-new
            # upstream release would be invisible to the planner and the stale
            # APK would be carried forever. Compare the version we last built
            # against the version the store is publishing right now.
            if not cur_app_ver and old_built_ver:
                latest_store_ver = fetch_latest_app_version(app)
                if latest_store_ver and _is_newer_version(latest_store_ver, old_built_ver):
                    reasons.append(
                        f"new-version: built {old_built_ver!r} -> store {latest_store_ver!r}"
                    )
            old_apk = carried_apk
            if old_apk and old_apk not in existing_apk_set:
                reasons.append("apk-missing-from-release")
            if not old_apk:
                reasons.append("no-apk-recorded")

        if reasons:
            logging.info(f"  REBUILD {app}/{src}/{arch}: {'; '.join(reasons)}")
            build_matrix.append(entry)
        else:
            old_apk = carried_apk
            if old_apk and old_apk in existing_apk_set:
                carry_over.append(old_apk)
                logging.info(f"  carry  {app}/{src}/{arch}: {old_apk}")
            else:
                # Defensive: if we can't carry it, we must rebuild.
                logging.info(f"  REBUILD {app}/{src}/{arch}: no carry-over apk")
                build_matrix.append(entry)

    # The build job in src/__main__.py builds ALL arches for an (app, source) in
    # one matrix run (it iterates arches from arch-config.json itself). To avoid
    # duplicate work and to keep the workflow contract unchanged, we deduplicate
    # the build matrix on (app, source) -- if ANY arch needs rebuild, the whole
    # (app, source) gets rebuilt and the resulting APKs replace those arches in
    # the carry-over set.
    deduped: List[dict] = []
    seen_pairs = set()
    for e in build_matrix:
        pair = (e["app_name"], e["source"])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        # Strip 'arch' from the matrix entry to match the original schema that
        # the existing build-apps job expects.
        deduped.append({"app_name": e["app_name"], "source": e["source"]})

    # Drop carry-overs whose (app, source) is being rebuilt -- the rebuild will
    # produce fresh APKs for ALL arches of that pair, so the old ones are stale.
    rebuilding_pairs = seen_pairs
    filtered_carry: List[str] = []
    for apk in carry_over:
        # Determine the (app, source) of this APK by looking up the manifest entry.
        owner_pair = None
        for ekey, eval_ in new_entries.items():
            if eval_.get("apk") == apk:
                owner_pair = (eval_["app_name"], eval_["source"])
                break
        if owner_pair is None or owner_pair not in rebuilding_pairs:
            filtered_carry.append(apk)
        else:
            logging.info(f"  drop carry {apk}: its (app,source) is rebuilding")

    return deduped, filtered_carry, new_entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def emit_full_rebuild(reason: str) -> None:
    """Emergency fallback: build everything (preserves the previous behavior)."""
    logging.warning(f"Falling back to FULL rebuild: {reason}")
    full = build_full_matrix()
    Path("build_matrix.json").write_text(json.dumps(full), encoding="utf-8")
    Path("carry_over.json").write_text(json.dumps([]), encoding="utf-8")
    # Empty manifest -> next run will treat everything as 'new-entry' until a
    # successful build writes a fresh manifest.
    Path("new_manifest.json").write_text(
        json.dumps({"entries": {}}, indent=2), encoding="utf-8")
    write_gh_output("build_matrix", json.dumps(full))
    write_gh_output("has_updates", "true" if full else "false")
    write_gh_output("update_count", str(len(full)))
    write_gh_output("total_count", str(len(full)))
    write_gh_output("carry_count", "0")
    write_gh_output("incremental", "false")


def main() -> int:
    try:
        full = build_full_matrix()
        logging.info(f"Full matrix: {len(full)} (app, source, arch) entries")

        if FORCE_FULL:
            logging.info("FORCE_FULL_REBUILD=true -> rebuilding everything")
            old_manifest = None
        else:
            old_manifest = fetch_existing_manifest()

        existing_apks = fetch_existing_apk_names()
        logging.info(f"Existing release has {len(existing_apks)} APK assets")

        if old_manifest is None and not FORCE_FULL:
            # No manifest yet -> first incremental run; rebuild everything once
            # to populate it. (Future runs will be incremental.)
            emit_full_rebuild("no manifest in existing release (first incremental run)")
            return 0

        build_mx, carry_over, new_entries = plan_incremental(
            full, old_manifest, existing_apks)

        Path("build_matrix.json").write_text(json.dumps(build_mx), encoding="utf-8")
        Path("carry_over.json").write_text(json.dumps(carry_over), encoding="utf-8")
        Path("new_manifest.json").write_text(
            json.dumps({"entries": new_entries}, indent=2), encoding="utf-8")

        write_gh_output("build_matrix", json.dumps(build_mx))
        write_gh_output("has_updates", "true" if build_mx else "false")
        write_gh_output("update_count", str(len(build_mx)))
        write_gh_output("total_count", str(len(full)))
        write_gh_output("carry_count", str(len(carry_over)))
        write_gh_output("incremental", "true")

        logging.info("=" * 60)
        logging.info(f"  Total entries:     {len(full)}")
        logging.info(f"  Need rebuild:      {len(build_mx)}")
        logging.info(f"  Carry over:        {len(carry_over)}")
        logging.info("=" * 60)

        return 0

    except Exception as e:
        logging.error(f"check_app_updates failed: {e}")
        traceback.print_exc()
        emit_full_rebuild(f"unexpected error: {e}")
        return 0  # Never fail the workflow over a planning error.


if __name__ == "__main__":
    sys.exit(main())
