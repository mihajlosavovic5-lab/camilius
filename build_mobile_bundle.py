"""Builds static/mobile-bundle.zip from the mobile/ folder - the artifact the
Android app downloads over the air (via api.py's /api/mobile-manifest and the
capacitor-updater plugin) so that changes to the app's web assets don't require a
new signed APK build and reinstall every time.

Usage: python build_mobile_bundle.py
Just rebuilds the zip - no version to pass in. /api/mobile-manifest serves the
same cache-bust stamp this script writes to static/mobile-bundle.version, so the
app automatically sees it as "new" the moment this changes, with no separate step
to remember.

That stamp is also baked into mobile/index.html itself (a data-bundle-version
attribute on <html>, written by update_index_html_bundle_version() below) - a
freshly installed APK's `npx cap sync` copies mobile/ verbatim, so this value
travels into the native build too. capacitor-updater's own current() call reports
the literal string "builtin" for a bundle that's never been OTA-updated, which
would otherwise never match /api/mobile-manifest's real version - app.js reads
this attribute to recognize "the builtin bundle IS already this exact version"
and skip a needless re-download of content it already has.

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

The mobile/fonts/*.woff2 files need the exact same treatment and previously didn't
get it: app.css's @font-face src urls and index.html's <link rel="preload"> hrefs
both reference them by a bare "fonts/<name>.woff2" path that never changes, so a
font file swapped in via OTA kept losing to the WebView's cache of the old one even
though app.css's own ?v= stamp (correctly) changed. update_font_references() below
appends a separate hash-of-the-font-files stamp to every such reference, in both
app.css and index.html, before compute_cache_bust_stamp() runs - so a font-only
change also flows into app.css's own ?v= stamp (it's one of CACHE_BUSTED_FILES),
same as any other edit to that file would.

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
FONTS_DIR = MOBILE_DIR / "fonts"
OUTPUT = ROOT_DIR / "static" / "mobile-bundle.zip"
VERSION_FILE = ROOT_DIR / "static" / "mobile-bundle.version"
INDEX_HTML = MOBILE_DIR / "index.html"
APP_CSS = MOBILE_DIR / "app.css"
CACHE_BUSTED_FILES = ["app.css", "app.js", "i18n.js", "hours-picker.js"]

# Matches data-bundle-version="<anything>" on the <html> tag, if a previous run
# already wrote one - same idempotent shape as the other stamp writers here, so a
# repeat run with unchanged CACHE_BUSTED_FILES content writes back byte-identical
# HTML instead of growing attributes.
BUNDLE_VERSION_ATTR_RE = re.compile(r'(<html\b[^>]*?)\s*data-bundle-version="[a-f0-9]*"')

# Matches "fonts/<name>.woff2", optionally already carrying a previous "?v=" stamp -
# same idempotent shape as update_index_html_cache_busters' pattern, so a repeat run
# with unchanged fonts writes back byte-identical content instead of growing stamps.
FONT_REF_RE = re.compile(r"fonts/[\w.-]+\.woff2(?:\?v=[a-f0-9]+)?")


def compute_font_stamp():
    hasher = hashlib.sha256()
    for path in sorted(FONTS_DIR.glob("*.woff2")):
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:12]


def update_font_references(stamp):
    def restamp(text):
        return FONT_REF_RE.sub(lambda m: m.group(0).split("?")[0] + f"?v={stamp}", text)

    APP_CSS.write_text(restamp(APP_CSS.read_text(encoding="utf-8")), encoding="utf-8")
    INDEX_HTML.write_text(restamp(INDEX_HTML.read_text(encoding="utf-8")), encoding="utf-8")


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


def update_index_html_bundle_version(stamp):
    html = INDEX_HTML.read_text(encoding="utf-8")
    html = BUNDLE_VERSION_ATTR_RE.sub(r"\1", html, count=1)
    html = html.replace("<html", f'<html data-bundle-version="{stamp}"', 1)
    INDEX_HTML.write_text(html, encoding="utf-8")


def build():
    (MOBILE_DIR / "hours-picker.js").write_bytes((ROOT_DIR / "static" / "hours-picker.js").read_bytes())

    update_font_references(compute_font_stamp())

    stamp = compute_cache_bust_stamp()
    update_index_html_cache_busters(stamp)
    update_index_html_bundle_version(stamp)

    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in MOBILE_DIR.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(MOBILE_DIR))
    VERSION_FILE.write_text(stamp, encoding="utf-8")
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes), cache-bust stamp {stamp}")


if __name__ == "__main__":
    build()
