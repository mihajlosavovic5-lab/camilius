"""Builds static/mobile-bundle.zip from the mobile/ folder - the artifact the
Android app downloads over the air (via api.py's /api/mobile-manifest and the
capacitor-updater plugin) so that changes to the app's web assets don't require a
new signed APK build and reinstall every time.

Usage: python build_mobile_bundle.py
Just rebuilds the zip - no version to pass in. /api/mobile-manifest hashes the
zip's own content to get a version string, so the app automatically sees it as
"new" the moment this changes, with no separate step to remember.
"""

import zipfile
from pathlib import Path

MOBILE_DIR = Path(__file__).parent / "mobile"
OUTPUT = Path(__file__).parent / "static" / "mobile-bundle.zip"


def build():
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in MOBILE_DIR.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(MOBILE_DIR))
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
