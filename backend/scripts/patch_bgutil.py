"""
Patch bgutil-ytdlp-pot-provider to downgrade version-mismatch from hard error to warning.

The jim60105/bgutil-pot Docker server reports version 0.8.x while the Python plugin
expects 1.x.x. The /get_pot API is compatible — only the version string differs.
Without this patch the plugin raises PoTokenProviderRejectedRequest and PO tokens
are never generated, breaking YouTube web-client downloads.
"""
import sys
import sysconfig
from pathlib import Path

# Ask the interpreter where its packages live rather than naming a version.
#
# This was hardcoded to /usr/local/lib/python3.10/site-packages, so moving the
# base image to python:3.12-slim failed the build with FileNotFoundError on a
# path that no longer existed. A build break is the good outcome — the same
# hardcoding would have silently skipped the patch if this script ever stopped
# treating a missing file as fatal, and PO token generation would have broken
# instead, which shows up as YouTube downloads 403ing on every byte.
_candidates = [
    Path(sysconfig.get_paths()["purelib"]),
    Path(sysconfig.get_paths()["platlib"]),
]
_rel = Path("yt_dlp_plugins/extractor/getpot_bgutil.py")

PLUGIN_PATH = next(
    (str(base / _rel) for base in _candidates if (base / _rel).exists()),
    str(_candidates[0] / _rel),   # keep the old "fail loudly" behaviour
)

OLD = (
    "        if not got_version or _major(got_version) != _major(self.PROVIDER_VERSION):\n"
    "            self._warn_and_raise(\n"
    "                f'Plugin and {name} major versions are mismatched. '\n"
    "                f'Update both the plugin and the {name} to the same version to proceed.')"
)

NEW = (
    "        if not got_version or _major(got_version) != _major(self.PROVIDER_VERSION):\n"
    "            self.logger.warning(\n"
    "                f'[bgutil] Major version mismatch ({got_version} vs {self.PROVIDER_VERSION})"
    " — proceeding anyway.',\n"
    "                once=True)"
)

with open(PLUGIN_PATH) as f:
    content = f.read()

if OLD in content:
    content = content.replace(OLD, NEW)
    with open(PLUGIN_PATH, "w") as f:
        f.write(content)
    print("bgutil patch applied")
    sys.exit(0)

if NEW in content:
    print("bgutil patch: already applied")
    sys.exit(0)

print("bgutil patch: WARNING — expected string not found, plugin may have been updated")
sys.exit(0)
