"""Publish a desktop release to a GitHub repo: notes + installer, .sig, .sha256.

    python desktop/scripts/publish-release.py <owner/repo> v<version> <notes.md> <files...>

Token read from ~/.config/vidgrab-gh-token (never printed). Run for junnyken/vidgrab-releases
(DESKTOP_RELEASE_REPO) and junnyken/dowload-video, see docs/desktop/UPDATER.md.
"""
import json, os, sys, urllib.request
repo, tag, notes_path, *files = sys.argv[1:]
tok = open(os.path.expanduser('~/.config/vidgrab-gh-token')).read().strip()
H = {'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
def req(url, data=None, method='GET', headers=None):
    r = urllib.request.Request(url, data=data, method=method, headers={**H, **(headers or {})})
    with urllib.request.urlopen(r, timeout=900) as resp:
        return json.loads(resp.read() or b'{}')
body = open(notes_path, encoding='utf-8').read()
# 27/10/2026: created public first, the release was GitHub's "latest" for the minutes the
# 100 MB installer took to upload — with no installer, so /client/version fell back to the
# env floor (0.9.1). Now: a draft, every file uploaded and checked, then published.
rel = req(f'https://api.github.com/repos/{repo}/releases', json.dumps({
    'tag_name': tag, 'name': f'VidGrab {tag[1:]}', 'body': body, 'draft': True,
    'prerelease': False}).encode(), 'POST', {'Content-Type': 'application/json'})
up = rel['upload_url'].split('{')[0]
for f in files:
    name = os.path.basename(f)
    a = req(f'{up}?name={name}', open(f, 'rb').read(), 'POST', {'Content-Type': 'application/octet-stream'})
    if a.get('size') != os.path.getsize(f):
        sys.exit(f'{repo} {name}: uploaded {a.get("size")} bytes, file has {os.path.getsize(f)} - left as a draft')
    print(repo, name, a.get('size'))
got = {x['name'] for x in req(f'https://api.github.com/repos/{repo}/releases/{rel["id"]}')['assets']}
missing = [os.path.basename(f) for f in files if os.path.basename(f) not in got]
if missing:
    sys.exit(f'{repo}: missing {missing} - left as a draft')
try:
    rel = req(f'https://api.github.com/repos/{repo}/releases/{rel["id"]}', json.dumps({
        'draft': False, 'make_latest': 'true'}).encode(), 'PATCH', {'Content-Type': 'application/json'})
except Exception as e:  # noqa: BLE001 — the draft is complete; publish it on the GitHub page
    sys.exit(f'{repo}: files uploaded but publishing failed ({e}) - publish the draft on {rel.get("html_url")}')
print(repo, 'release', rel.get('html_url'))
