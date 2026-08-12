"""
Patch bgutil-ytdlp-pot-provider to downgrade version-mismatch from hard error to warning.

The jim60105/bgutil-pot Docker server reports version 0.8.x while the Python plugin
expects 1.x.x. The /get_pot API is compatible — only the version string differs.
Without this patch the plugin raises PoTokenProviderRejectedRequest and PO tokens
are never generated, breaking YouTube web-client downloads.
"""
import sys

PLUGIN_PATH = (
    "/usr/local/lib/python3.10/site-packages"
    "/yt_dlp_plugins/extractor/getpot_bgutil.py"
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
