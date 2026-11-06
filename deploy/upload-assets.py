#!/usr/bin/env python3
"""Upload every file in a folder to the GitHub Release of a tag. Python 3.6 standard library (runs on the Nano).

Usage: GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo python3 deploy/upload-assets.py vX.Y.Z <dist_dir>
An existing asset with the same name is replaced, so a rerun is safe.
"""
import json, os, sys, urllib.request
from urllib.parse import quote

tag, dist = sys.argv[1], sys.argv[2]
repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
H = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}


def call(method, url, body=None, ctype=None):
    h = dict(H)
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
        return json.loads(data.decode()) if data else None


rel = call("GET", "https://api.github.com/repos/%s/releases/tags/%s" % (repo, quote(tag)))
existing = {a["name"]: a["id"] for a in rel["assets"]}
for name in sorted(os.listdir(dist)):
    if name in existing:
        call("DELETE", "https://api.github.com/repos/%s/releases/assets/%d" % (repo, existing[name]))
    with open(os.path.join(dist, name), "rb") as f:
        body = f.read()
    ctype = "application/gzip" if name.endswith(".tar.gz") else "text/plain"
    a = call("POST", "https://uploads.github.com/repos/%s/releases/%d/assets?name=%s" % (repo, rel["id"], quote(name)), body, ctype)
    print("uploaded", a["name"], a["size"], "bytes")
