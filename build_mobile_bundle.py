"""Builds static/mobile-bundle.zip from the mobile/ folder - the artifact the
Android app downloads over the air (via api.py's /api/mobile-manifest and the
capacitor-updater plugin) so that changes to the app's web assets don't require a
new signed APK build and reinstall every time.

Usage: python build_mobile_bundle.py
Just rebuilds the zip - no version to pass in. /api/mobile-manifest hashes the
zip's own content to get a version string, so the app automatically sees it as
"new" the moment this changes, with no separate step to remember.

Also rewrites mobile/index.html's local <link>/<script> tags to append a
"?v=<hash>" cache-buster, derived from app.css/app.js/i18n.js/hours-picker.js's own
content. Reason: switching the WebView to a new bundle (via capacitor-updater's set())
reuses the SAME origin+path for these files every time (e.g. https://localhost/
app.css) - Android's WebView can and does serve a stale cached copy of that URL
across bundle switches even though the underlying file changed, since nothing
about the URL itself changed to signal that. Appending a content-derived query
string forces a fresh fetch exactly when - and only when - the content actually
changes. This rewrite is applied to the SOURCE file (not just the zip) so a
native APK build (which copies mobile/ verbatim via `npx cap sync`) gets the
same cache-busting, not just the OTA path.

Before either of the above, mirrors static/hours-picker.js into mobile/hours-picker.js -
that file is the canonical source for the Working Hours picker's shared logic (used by
both static/pickers.js and mobile/app.js), so this is the one place that copy gets
refreshed. Edit static/hours-picker.js, then re-run this script - editing
mobile/hours-picker.js directly will just get overwritten next run.
"""

import hashlib
import re
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).parent
MOBILE_DIR = ROOT_DIR / "mobile"
OUTPUT = ROOT_DIR / "static" / "mobile-bundle.zip"
INDEX_HTML = MOBILE_DIR / "index.html"
CACHE_BUSTED_FILES = ["app.css", "app.js", "i18n.js", "hours-picker.js"]


def compute_cache_bust_stamp():
    hasher = hashlib.sha256()
    for name in CACHE_BUSTED_FILES:
        hasher.update((MOBILE_DIR / name).read_bytes())
    return hasher.hexdigest()[:12]


def update_index_html_cache_busters(stamp):
    html = INDEX_HTML.read_text(encoding="utf-8")
    for name in CACHE_BUSTED_FILES:
        # Matches href="app.css" or href="app.css?v=<anything>" (idempotent - strips
        # any previous stamp before applying the new one) for both href= and src=.
        pattern = re.compile(r'((?:href|src)=")' + re.escape(name) + r'(?:\?v=[a-f0-9]+)?(")')
        html = pattern.sub(r"\g<1>" + name + f"?v={stamp}" + r"\g<2>", html)
    INDEX_HTML.write_text(html, encoding="utf-8")


def build():
    (MOBILE_DIR / "hours-picker.js").write_bytes((ROOT_DIR / "static" / "hours-picker.js").read_bytes())

    stamp = compute_cache_bust_stamp()
    update_index_html_cache_busters(stamp)

    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in MOBILE_DIR.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(MOBILE_DIR))
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes), cache-bust stamp {stamp}")


if __name__ == "__main__":
    build()
