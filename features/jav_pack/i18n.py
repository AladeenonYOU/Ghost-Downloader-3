from app.i18n import N

# Pack 展示
N("JavPack", "Jable / MissAV 视频")
N("JavPack", "启用 Jable / MissAV 解析")
N("JavPack", "粘贴视频详情页链接后自动解析播放清单，优先级高于通用 M3U8 解析")
N("JavPack", "文件命名")
N("JavPack", "番号开头便于后续整理；无码片源会插入标记")
N("JavPack", "番号 + 标题")
N("JavPack", "仅标题")
N("JavPack", "仅番号")
N("JavPack", "按番号建子文件夹")
N("JavPack", "输出到「下载目录 / 番号 /」下，重下同一部片子不会与旧文件混在一起")
N("JavPack", "无码片源标记")
N("JavPack", "命中 MissAV 的无码片源时插入到番号之后，留空则不插入")
N("JavPack", "MissAV 走本地中转")
N("JavPack", "surrit 会拒绝官方 N_m3u8DL-RE 的指纹，中转由 GD 自己取流后转发给下载器")

# 错误
N("TaskErrors", "链接不是 {site} 的视频详情页：{url}")
N("TaskErrors", "在 {site} 页面中没有找到播放清单（{code}），站点结构可能已变更：可在浏览器播放后复制 m3u8 链接交给通用解析")
N("TaskErrors", "无法打开 {site} 页面（{detail}），站点可能启用了人机验证，请先在浏览器中打开一次，或在设置中配置代理")
N("TaskErrors", "{site} 返回了人机验证页面，请先在浏览器中通过验证后重试，或改用浏览器扩展抓取播放链接")
N("TaskErrors", "获取 {site} 播放清单失败：{detail}")
N("TaskErrors", "{site} 只返回了广告占位流（{host}），说明该片源在当前网络下不可用：请更换节点或登录后重试")
N("TaskErrors", "{site} 找不到该视频页（{code}），可能已下架或番号有误")
N("TaskErrors", "MissAV 播放清单拒绝了所有候选来源（{detail}），请更换网络出口后重试")
