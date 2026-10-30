#!/usr/bin/env python3
"""自动发现 GitHub 上近 30 天仍在更新的 IPTV 直播源仓库，验证其 m3u 可用后落盘。

输出 output/discovered.json，build.py 会自动合并。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fetch, UA, OPENER  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "output")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""

QUERIES = [
    "iptv m3u",
    "m3u8 直播",
    "iptv playlist",
    "电视直播源",
    "直播源 m3u",
    "ipv6 iptv",
]
DIRS = ["", "tv", "m3u", "output", "dist"]
MAX_CANDIDATES = 40
MIN_STARS = 20


def api(path):
    url = "https://api.github.com/" + path.lstrip("/")
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(url, headers=headers)
    with OPENER.open(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def search(days=30):
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    repos, seen = [], set()
    for q in QUERIES:
        qq = urllib.parse.quote("%s pushed:>%s" % (q, since))
        try:
            data = api("search/repositories?q=%s&sort=stars&order=desc&per_page=10" % qq)
        except urllib.error.HTTPError as e:
            print("  搜索失败 %s: HTTP %s" % (q, e.code), flush=True)
            continue
        except Exception as e:  # noqa: BLE001
            print("  搜索失败 %s: %s" % (q, str(e)[:40]), flush=True)
            continue
        for it in data.get("items", []):
            fn = it["full_name"]
            if fn in seen or it["stargazers_count"] < MIN_STARS:
                continue
            seen.add(fn)
            repos.append({
                "repo": fn,
                "stars": it["stargazers_count"],
                "pushed": (it.get("pushed_at") or "")[:10],
                "branch": it.get("default_branch") or "main",
            })
        time.sleep(1)
    return repos


def find_playlists(repo):
    out = []
    for d in DIRS:
        try:
            items = api("repos/%s/contents/%s" % (repo["repo"], d) if d else "repos/%s/contents" % repo["repo"])
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if it.get("type") != "file":
                continue
            name = it.get("name", "")
            size = it.get("size", 0)
            if not re.search(r"\.(m3u|m3u8|txt)$", name, re.I):
                continue
            if not re.search(r"iptv|live|tv|m3u|list|zb|电视|直播", name, re.I):
                continue
            if size < 300 or size > 6 * 1024 * 1024:
                continue
            out.append({
                "path": it["path"],
                "size": size,
                "url": "https://raw.githubusercontent.com/%s/%s/%s" % (
                    repo["repo"], repo["branch"], it["path"]),
            })
    return out


def main():
    t0 = time.time()
    print("搜索近 30 天更新的 IPTV 仓库 ...", flush=True)
    repos = search()
    print("候选仓库 %d 个" % len(repos), flush=True)
    repos.sort(key=lambda r: -r["stars"])

    verified, seen_url = [], set()
    for repo in repos:
        if len(verified) >= 8:
            break
        cands = find_playlists(repo)
        for c in cands:
            if c["url"] in seen_url:
                continue
            seen_url.add(c["url"])
            if len(seen_url) > MAX_CANDIDATES:
                break
            try:
                text = fetch(c["url"], timeout=20)
            except Exception:  # noqa: BLE001
                continue
            if not text.lstrip().upper().startswith("#EXTM3U"):
                continue
            n = text.count("#EXTINF")
            if n < 10:
                continue
            verified.append({
                "name": "gh-" + repo["repo"].split("/")[-1].lower()[:24] + "-" + re.sub(r"\W", "", os.path.basename(c["path"]))[:16],
                "url": c["url"],
                "enabled": True,
                "source_repo": repo["repo"],
                "stars": repo["stars"],
                "pushed": repo["pushed"],
                "entries": n,
            })
            print("  ✓ %-38s %4d 条  %s" % (repo["repo"], n, c["path"]), flush=True)
            break
        time.sleep(0.5)

    os.makedirs(OUT, exist_ok=True)
    json.dump(verified, open(os.path.join(OUT, "discovered.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n发现可用上游 %d 个，耗时 %.1fs" % (len(verified), time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
