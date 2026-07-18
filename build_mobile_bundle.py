"""Builds static/mobile-bundle.zip from the mobile/ folder - the artifact the
Android app downloads over the air (via api.py's /api/mobile-manifest and the
capacitor-updater plugin) so that changes to the app's web assets don't require a
new signed APK build and reinstall every time.

Usage: python build_mobile_bundle.py <version>
Then set MOBILE_BUNDLE_VERSION=<version> on Railway (and redeploy) so the manifest
endpoint starts advertising it. Version can be anything that changes each time
(e.g. a date-based string or incrementing integer) - the app only checks for
inequality with what it last applied, there's no ordering requirement.
"""

import sys
import zipfile
from pathlib import Path

MOBILE_DIR = Path(__file__).parent / "mobile"
OUTPUT = Path(__file__).parent / "static" / "mobile-bundle.zip"


def build(version):
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in MOBILE_DIR.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(MOBILE_DIR))
    print(f"Wrote {OUTPUT} for version '{version}' ({OUTPUT.stat().st_size} bytes)")
    print(f"Next: set MOBILE_BUNDLE_VERSION={version} on Railway and redeploy.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python build_mobile_bundle.py <version>")
        sys.exit(1)
    build(sys.argv[1])
