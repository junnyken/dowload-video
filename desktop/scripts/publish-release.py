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
rel = req(f'https://api.github.com/repos/{repo}/releases', json.dumps({
    'tag_name': tag, 'name': f'VidGrab {tag[1:]}', 'body': body, 'draft': False,
    'prerelease': False, 'make_latest': 'true'}).encode(), 'POST', {'Content-Type': 'application/json'})
up = rel['upload_url'].split('{')[0]
for f in files:
    name = os.path.basename(f)
    a = req(f'{up}?name={name}', open(f, 'rb').read(), 'POST', {'Content-Type': 'application/octet-stream'})
    print(repo, name, a.get('size'))
print(repo, 'release', rel.get('html_url'))
