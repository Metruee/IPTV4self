# IPTV4self

自动收集 GitHub 上仍在维护的公开直播源，**逐个实测连通性**，只把能用的流保留下来，生成自己的 m3u 播放列表。

产物：

- `output/iptv.m3u` — 播放列表（按央视 / 卫视 / 地方 / 港澳台 / 体育 / 电影 / 少儿 / 纪录 / 国际 / 其他 分组）
- `output/report.json` — 每个上游的抓取量、存活率、分组统计
- `output/discovered.json` — 自动发现的上游（近 30 天有更新且已验证可用）

## 使用

订阅地址（任一播放器添加为 M3U 源）：

```
https://raw.githubusercontent.com/Metruee/IPTV4self/main/output/iptv.m3u
```

国内网络访问 raw 失败时，可在前面加 `https://gh-proxy.com/`。

## 它是怎么工作的

1. **发现上游**（`scripts/discover.py`）
   GitHub 搜索 `iptv m3u`、`m3u8 直播`、`电视直播源` 等关键词，只取 **近 30 天有 push 且 star ≥ 20** 的仓库；
   在仓库根目录及 `tv/ m3u/ output/ dist/ public/ playlist/ lists/ sources/ result/` 里找 `.m3u/.m3u8/.txt`，
   逐个下载并确认内容以 `#EXTM3U` 开头、条目 ≥ 10 才收录。

2. **抓取合并**（`scripts/build.py`）
   读取 `config/upstreams.json`（人工精选）+ `output/discovered.json`（自动发现），并发下载全部列表，按 URL 去重。

3. **连通性实测**
   对每个流发 `Range: bytes=0-2048` 请求，判定可用：
   - 返回内容以 `#EXTM3U` 开头 → m3u8 有效
   - 首字节 `0x47` → TS 流有效
   - `Content-Type` 为 `video/*` / `audio/*` / `mpegurl` / `octet-stream` → 有效
   同时记录握手时延。

4. **去重分组输出**
   同一频道保留**最快的前 3 条**线路（`MAX_PER_NAME`），按分组+时延排序写出 m3u。

## 目录

```
config/upstreams.json     人工精选上游
scripts/common.py         抓取 / 解析 / 探测
scripts/discover.py       自动发现上游
scripts/build.py          合并、探测、生成
output/                   产物（由 Actions 自动提交）
.github/workflows/sync.yml 每天 UTC 20:00 / 手动触发
```

## 本地运行

```bash
cd scripts
python3 discover.py       # 可选，需要 GITHUB_TOKEN
python3 build.py
```

可调环境变量：`PROBE_WORKERS`（并发，默认 48）、`PROBE_TIMEOUT`（秒，默认 8）、`MAX_PER_NAME`（同名保留条数，默认 3）。

## 说明

- 只做**公开链接的聚合与可用性验证**，不存储、不分发任何音视频内容；版权归各源站所有。
- 直播源时效性极强，列表每天重建一次，失效源会被自动剔除。
- 部分源有地域/运营商限制，在别人网络可用不代表在你网络可用。
