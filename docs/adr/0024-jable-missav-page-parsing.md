# Jable / MissAV 走「详情页 URL → 播放清单」的站点解析，不搬油猴脚本的协议唤起

新增 `jav_pack`：把 Jable、MissAV 的视频详情页链接直接交给通用 M3U8 引擎下载。
油猴脚本 474848 的做法是用 Base64 拼 `m3u8dl://` 唤起本地 N_m3u8DL-RE，GD 已经有
`m3u8_pack`（内含 N_m3u8DL-RE 运行时），所以只补「页面 → 清单」这一段，不动下载引擎。

站点差异全部收敛在 `sites.py` 的纯函数里（可单测，不依赖 Qt / wreq）：

- Jable：视频页内联脚本的 `var hlsUrl = "..."` 就是主清单，带 token，相对路径按页面补全。
- MissAV：内联脚本 `seek` 附近是 36 位流 ID，拼 `https://surrit.com/{id}/playlist.m3u8`；
  surrit 校验站点根 Referer，缺少即 403，所以清单请求必须带 `Referer: {origin}/` 与真实桌面 UA。
- 番号与标题：Jable 取 `/videos/{slug}/` 的 slug，MissAV 取语言段之后的段落并剥掉
  `-uncensored-leak` 一类后缀；标题优先用浏览器扩展送来的 `pageTitle`，否则解析 `og:title`。

解析器优先级 95，高于通用 M3U8(80) 与 GitHub(90)，只在这些域名下接管；命中后调用
`M3U8Parser.parse` 生成 `M3U8Task`，再把 `packId` 改成 `jav`。清单探测、直播判定、清晰度枚举、
断点续传、解密与混流参数因此全部沿用 M3U8 Pack，`jav_pack` 自己不实现下载。

## Considered Options

- **原样移植油猴脚本（`m3u8dl://` + Base64 参数）**：GD 内置 N_m3u8DL-RE 运行时，用户不必再装一个
  CLI 并注册协议；而且协议唤起只能传参数，拿不到任务队列、进度、断点续传和清晰度选择。
- **依赖浏览器扩展抓 m3u8**：MissAV 播放器把清单塞在 `seek` 之后的脚本里，通用抓包并不总能命中；
  且用户把链接粘贴进 GD 主窗口时没有浏览器上下文，页面解析必须能在桌面端独立完成。
- **交给 yt-dlp 的通用 extractor**：要额外装 yt-dlp 运行时，这两个站点的页面结构变化也往往先于
  yt-dlp 适配；站点规则很短，自己维护成本更低。
- **新建独立下载任务类型**：等于把 M3U8 的 30 多个步骤参数复制一遍，后续 M3U8 引擎升级要改两处。

## Consequences

- 三条触发路径共用同一套解析：主窗口粘贴链接、剪贴板监听（`matchPassive`）、浏览器扩展的
  `page_media`/`create_task` 消息；扩展无需改动，`FeatureService.parse` 会自动选中本解析器。
- 输出命名默认 `番号 + 标题`，无码片源插入 `[无码破解]`；可选按番号建子文件夹。文件名与目录名
  都过 `toSafeFilename`，避免 Windows 保留名与非法字符。
- 抓不到清单时不会静默降级成「下载 HTML」：直接抛带原因的 `TaskError`（人机验证 / 结构变更 /
  Referer 缺失），提示用户改用浏览器播放后复制 m3u8。
- 站点换域名只需改 `sites.py` 的 `MISSAV_HOST_PATTERN` / `JABLE_HOST_PATTERN`；MissAV 的镜像域名
  用 `missav*.<tld>` 通配，不必逐个列。
- 域名单一、站点结构变更频繁，`jav_pack` 的版本要能独立于 GD 发版更新（与其它 Pack 一致）。

## 实测补充（2026-11-02，真实站点）

- surrit 的清单只认 `missav.ws / live / ai / missav123.com` 四个老域名的 Referer；
  镜像域名（`missav.fans`、`missav.cz`、`missav.com`）和带 `www.` 的前缀一律 403。
  因此清单请求失败时按固定顺序换 Referer 重试，成功后把该值写进任务步骤头。
- 完整详情页同样内嵌 `challenges.cloudflare.com` 与 `challenge-platform` 脚本，
  不能据此判定「被拦截」；只有短页面（< 8 KB）才当作拦截页。
- Jable 在片源不可用（地区限制 / 反爬降级）时会把 `hlsUrl` 指向广告联盟
  （实测 `*.mushroomtrack.com`）。这类流必须识别并报错，否则会把广告当正片下载。
- `missav.cz` 的详情页是 `/v/{番号}`，路径解析要支持 `v` 这种容器段。
- 官方 N_m3u8DL-RE 0.6.0 拉 surrit 一律 403：同一 Referer 下 curl 与 wreq 都是 200，
  同一二进制拉公开 HLS 也正常，说明是它自身指纹被 surrit 拒。实际下载需要
  missav 兼容版二进制，或由 GD 侧做本地中转（见后续决策）。

## 后续决策：surrit 走本地中转，而不是换二进制

`relay.py` 在 127.0.0.1 起一个只服务本机的中转（默认端口 18640，占用时自动换随机端口）：

```
N_m3u8DL-RE → http://127.0.0.1:PORT/p/{会话}.m3u8
              中转用 wreq 带 Referer/UA 取上游 → 改写清单 URI → 回传
```

- **会话编码在 URL 里**（上游地址 + Referer + UA 的 base64），服务端零状态：
  进程退出重启后，只要端口没被占，暂停的任务仍能续传（已用两个独立进程验证）。
- **不改运行时**：不引入第三方二进制，也不用担心 GD 更新运行时把它覆盖回去。
- 清单与变体清单都改写，`#EXT-X-KEY` / `#EXT-X-MAP` 的 URI 一并改写，避免相对地址指错。
- 只在 `surrit.com` / `nineyu.com` 上启用（`shouldRelay`），Jable 与普通 HLS 完全不受影响；
  可用「MissAV 走本地中转」开关关闭。
- **连接复用反而更慢**：同一个 wreq Client 并发拉分段实测 12 MB/20s，
  每请求新建客户端 26 MB/20s，因此不做连接池复用（注释里写了原因，防止后人"优化"回去）。

实测：真实详情页 → 解析出 4 档清晰度 → 官方二进制 20 秒写入 15 个分片 / 27 MB。

## 补充：镜像回退与回环地址不能走代理

- **镜像回退**：`missav.ai` 对 wreq 也返回 403（Cloudflare），但同番号在 `missav.fans` / `missav.media`
  正常。`pageCandidates()` 依次尝试「原地址 → 同 slug → `/{lang}/{code}` → `/v/{code}`」，
  并要求页面里真的有播放器数据（`surrit` / `seek` / `.m3u8`）才算成功，避免把空壳页当命中。
- **回环地址必须绕过代理**：系统代理会把 `http://127.0.0.1:18640` 当成远端去连，返回 502。
  为此 `app/client.py` 增加 `isLoopbackUrl()` 与 `buildClient(shouldUseProxy=...)`，
  M3U8 解析清单/变体时对回环地址禁用代理，`M3U8TaskStep` 也不给二进制传 `--custom-proxy`。
- **中转改成标准库实现**：打包版不带 aiohttp，改用 `http.server` + 独立线程承载 wreq 的
  事件循环，Pack 可以直接放进 features 目录运行。

