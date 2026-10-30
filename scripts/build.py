#!/usr/bin/env python3
"""下载上游 m3u → 解析 → 并发探测 → 去重分组 → 生成自己的 m3u。"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re  # noqa: E402
from common import fetch, parse_m3u, probe, norm_group, norm_name  # noqa: E402

JUNK = re.compile(r"更新时间|公告|测试|请勿|备用|防失联|广告|加群|QQ|Telegram|订阅|说明|空频道|无效", re.I)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "output")

WORKERS = int(os.environ.get("PROBE_WORKERS", "48"))
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "8"))
MAX_PER_NAME = int(os.environ.get("MAX_PER_NAME", "3"))
MAX_PER_UPSTREAM = int(os.environ.get("MAX_PER_UPSTREAM", "2500"))
MAX_TOTAL = int(os.environ.get("MAX_TOTAL", "9000"))


def load_upstreams():
    items, seen = [], set()
    for name in ("upstreams.json", "discovered.json"):
        p = os.path.join(ROOT, "config" if name == "upstreams.json" else "output", name)
        if not os.path.exists(p):
            continue
        for e in json.load(open(p, encoding="utf-8")):
            if not e.get("enabled", True):
                continue
            u = e["url"]
            if u in seen:
                continue
            seen.add(u)
            items.append(e)
    return items


def main():
    t_start = time.time()
    ups = load_upstreams()
    print("上游 %d 个" % len(ups), flush=True)

    raw = []          # (source, ch)
    src_stats = {}

    def _pull(e):
        try:
            return e, parse_m3u(fetch(e["url"], timeout=25)), None
        except Exception as ex:  # noqa: BLE001
            return e, [], ex

    with ThreadPoolExecutor(max_workers=8) as pool:
        for e, chs, err in pool.map(_pull, ups):
            if err is not None:
                src_stats[e["name"]] = -1
                print("  %-28s 失败 %s" % (e["name"], str(err)[:50]), flush=True)
                continue
            for c in chs[:MAX_PER_UPSTREAM]:
                raw.append((e["name"], c))
            src_stats[e["name"]] = len(chs)
            print("  %-28s %4d 条" % (e["name"], len(chs)), flush=True)

    # URL 去重
    uniq, seen = [], set()
    for src, c in raw:
        u = c.get("url", "").strip()
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        c["source"] = src
        uniq.append(c)
        if len(uniq) >= MAX_TOTAL:
            break
    print("去重后 %d 个流（原始 %d）" % (len(uniq), len(raw)), flush=True)

    # 并发探测
    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(probe, c["url"], TIMEOUT): c for c in uniq}
        for f in as_completed(futs):
            c = futs[f]
            try:
                r = f.result()
            except Exception as ex:  # noqa: BLE001
                r = {"ok": False, "latency": 99999, "kind": "", "err": str(ex)[:40]}
            done += 1
            if done % 200 == 0:
                print("  探测 %d/%d ..." % (done, len(uniq)), flush=True)
            if r["ok"]:
                c.update(r)
                results.append(c)
    print("存活 %d / %d（%.1f%%）" % (len(results), len(uniq),
                                 100.0 * len(results) / max(1, len(uniq))), flush=True)

    # 失败原因分布（诊断用）
    from collections import Counter
    errs = Counter()
    for c in uniq:
        pass

    # 同名多线路：保留最快的 N 条
    by_name = {}
    for c in results:
        c["group"] = norm_group(c.get("group"), c.get("name"))
        key = norm_name(c.get("name"))
        by_name.setdefault(key, []).append(c)

    final = []
    for key, lst in by_name.items():
        lst.sort(key=lambda x: x.get("latency", 99999))
        final.extend(lst[:MAX_PER_NAME])

    # 排序：分组优先，其次时延
    order = ["央视", "卫视", "地方", "港澳台", "体育", "电影", "少儿", "纪录", "国际", "其他"]
    def gkey(c):
        g = c["group"]
        return (order.index(g) if g in order else len(order), c.get("latency", 99999))
    final.sort(key=gkey)

    os.makedirs(OUT, exist_ok=True)
    lines = ["#EXTM3U"]
    for c in final:
        name = c.get("name", "").strip()
        if not name or len(name) < 2 or JUNK.search(name):
            continue
        logo = c.get("logo", "")
        attrs = 'tvg-name="%s"' % name.replace('"', "")
        if logo:
            attrs += ' tvg-logo="%s"' % logo.replace('"', "")
        attrs += ' group-title="%s"' % c["group"]
        lines.append("#EXTINF:-1 %s,%s" % (attrs, name))
        lines.append(c["url"])
    m3u_path = os.path.join(OUT, "iptv.m3u")
    open(m3u_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    # 统计
    groups = {}
    for c in final:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
    by_src = {}
    for c in final:
        by_src[c["source"]] = by_src.get(c["source"], 0) + 1

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.time() - t_start, 1),
        "upstreams": len(ups),
        "raw": len(raw),
        "unique": len(uniq),
        "alive": len(results),
        "alive_rate": round(100.0 * len(results) / max(1, len(uniq)), 1),
        "output_channels": len(final),
        "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1])),
        "by_source": dict(sorted(by_src.items(), key=lambda kv: -kv[1])),
        "per_upstream": src_stats,
        "channels": [{"name": c.get("name"), "url": c["url"], "group": c["group"],
                      "latency": c.get("latency"), "kind": c.get("kind"),
                      "source": c["source"]} for c in final],
    }
    json.dump(report, open(os.path.join(OUT, "report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n输出 %d 个频道 → output/iptv.m3u" % len(final), flush=True)
    print("分组:", json.dumps(groups, ensure_ascii=False), flush=True)
    print("来源:", json.dumps(by_src, ensure_ascii=False), flush=True)
    print("耗时 %.1fs" % (time.time() - t_start), flush=True)


if __name__ == "__main__":
    main()
