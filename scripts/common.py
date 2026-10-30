#!/usr/bin/env python3
"""公共工具：抓取、解析 m3u、连通性探测。

注意：显式禁用系统代理（urllib 会读取 macOS 系统代理设置，
本机开着 Clash 时会让所有探测都"假通过"）。
"""
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 显式不走代理；证书不校验（大量 IPTV 源证书不合规）
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=CTX),
)

GH_PROXY = "https://gh-proxy.com/"

ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')


def _host_of(url):
    m = re.match(r"https?://([^/:]+)", url or "")
    return m.group(1).lower() if m else ""


def fetch(url, timeout=20, retries=1):
    """抓取文本；raw.githubusercontent.com 失败时自动走 gh-proxy。"""
    candidates = [url]
    if "raw.githubusercontent.com" in url:
        candidates.append(GH_PROXY + url)
    last = None
    for attempt in range(retries + 1):
        for u in candidates:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"})
                with OPENER.open(req, timeout=timeout) as r:
                    raw = r.read()
                if not raw:
                    continue
                return raw.decode("utf-8", "replace")
            except Exception as e:  # noqa: BLE001
                last = e
        time.sleep(1)
    raise RuntimeError("fetch failed: %s (%s)" % (url, last))


def parse_m3u(text):
    """解析 m3u/m3u8 文本，返回 [{name,url,logo,group,attrs}]。"""
    out = []
    if not text:
        return out
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cur = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.upper().startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(s))
            name = s.split(",", 1)[1].strip() if "," in s else ""
            cur = {
                "name": name or attrs.get("tvg-name", "").strip(),
                "logo": attrs.get("tvg-logo", "").strip(),
                "group": attrs.get("group-title", "").strip(),
                "attrs": attrs,
            }
        elif s.startswith("#"):
            continue
        elif cur is not None:
            cur["url"] = s
            out.append(cur)
            cur = None
    # 兼容纯 txt 格式：名称,url 或 名称$url
    if not out:
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = re.split(r"[,，]", s, maxsplit=1)
            if len(parts) == 2 and re.match(r"https?://", parts[1].strip()):
                out.append({"name": parts[0].strip(), "url": parts[1].strip(),
                            "logo": "", "group": "", "attrs": {}})
    return out


GROUP_RULES = [
    (r"央[视视频]|CCTV|CGTN|央视", "央视"),
    (r"卫[视视]", "卫视"),
    (r"港澳|香港|台湾|澳门|明珠|翡翠|TVB|凤凰|港·澳·台|港奥台", "港澳台"),
    (r"体育|足球|篮球|赛事|NBA|英超|奥运", "体育"),
    (r"电影|影院|影迷|剧场|影视", "电影"),
    (r"少儿|卡通|动漫|动画|儿童|KIDS", "少儿"),
    (r"纪录|纪实|探索|地理|文化|CULTURE", "纪录"),
    (r"音乐|MUSIC", "音乐"),
    (r"新闻|NEWS", "新闻"),
    (r"国际|海外|日韩|欧美|RELIGIOUS", "国际"),
    (r"频道|都市|城市|本地", "地方"),
]

EMOJI_RE = re.compile(
    "[" "🌀-🫿" "☀-➿" "🀀-🋿"
    "☀-➿" "️" "⬀-⯿" "]+")


def _clean(g):
    g = EMOJI_RE.sub("", g or "")
    return re.sub(r"[\s_\-·]+", "", g).strip()


def norm_group(group, name):
    g = _clean(group)
    if g and g not in ("未分类", "其他", "默认", "undefined", "Undefined", ""):
        for pat, label in GROUP_RULES:
            if re.search(pat, g, re.I):
                return label
        if re.search(r"频道$", g) or re.search(r"(省|市|频道)", g):
            return "地方"
        if re.search(r"^[A-Za-z;]+$", g):
            return "国际"
        return "其他"
    for pat, label in GROUP_RULES:
        if re.search(pat, name or "", re.I):
            return label
    return "其他"


def norm_name(name):
    s = (name or "").strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[【\[（(].*?[】\]）)]", "", s)
    s = s.replace("高清", "").replace("超清", "").replace("标清", "")
    s = re.sub(r"(HD|FHD|UHD|4K|1080P|720P|50FPS|25FPS)$", "", s, flags=re.I)
    return s.strip().upper()


def probe(url, timeout=8):
    """探测单个流是否可用，返回 {ok, latency, kind, err}。"""
    t0 = time.time()
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept": "*/*",
                     "Range": "bytes=0-2048",
                     "Connection": "close"},
        )
        with OPENER.open(req, timeout=timeout) as r:
            head = r.read(2048)
            ctype = (r.headers.get("Content-Type") or "").lower()
        lat = int((time.time() - t0) * 1000)
        s = head[:64].lstrip()
        if s.startswith(b"#EXTM3U"):
            return {"ok": True, "latency": lat, "kind": "m3u8", "err": ""}
        if head[:1] == b"\x47":
            return {"ok": True, "latency": lat, "kind": "ts", "err": ""}
        if "mpegurl" in ctype:
            return {"ok": True, "latency": lat, "kind": "m3u8", "err": ""}
        if ctype.startswith(("video/", "audio/")):
            return {"ok": True, "latency": lat, "kind": "media", "err": ""}
        if ctype.startswith(("application/octet-stream", "binary/octet-stream")) and head:
            return {"ok": True, "latency": lat, "kind": "binary", "err": ""}
        return {"ok": False, "latency": lat, "kind": "", "err": "ctype=" + (ctype[:30] or "empty")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "latency": int((time.time() - t0) * 1000), "kind": "", "err": "HTTP%d" % e.code}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "latency": timeout * 1000, "kind": "", "err": "timeout"}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "timed out" in msg:
            msg = "timeout"
        return {"ok": False, "latency": int((time.time() - t0) * 1000), "kind": "", "err": msg[:40]}
