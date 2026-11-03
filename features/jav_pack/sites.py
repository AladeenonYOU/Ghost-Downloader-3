"""Jable / MissAV 页面解析。

这里只放纯函数：站点识别、番号与标题提取、播放清单地址提取、命名拼装。
不依赖项目的 HTTP 客户端与 UI，便于单独测试（tests/jav_pack/test_sites.py）。

参考实现：Greasy Fork 脚本 474848《Jable一键下载收藏 (支持MissAV)》v2.3.7
- Jable：视频页内联脚本中的 `var hlsUrl = "..."` 即主播放清单
- MissAV：视频页内联脚本的 `seek` 附近带 36 位流 ID，拼成 surrit.com/{id}/playlist.m3u8
- MissAV 的 surrit 会校验请求来源，缺少 Referer 直接 403
"""

from __future__ import annotations

import html as htmlLib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

# ── 站点识别 ──

JABLE_HOST_PATTERN = re.compile(r"^(?:www\.)?(?:jable\.[a-z][a-z0-9-]*(?:\.[a-z]{2,})?|fs1\.app)$", re.I)
MISSAV_HOST_PATTERN = re.compile(r"^(?:www\.)?missav[0-9]*\.[a-z][a-z0-9-]*(?:\.[a-z]{2,})?$", re.I)

SITE_TITLES = {"jable": "Jable", "missav": "MissAV"}

# surrit / nineyu 的清单只认这几个老品牌域名的 Referer，新镜像（fans / com / cz …）
# 和带 www. 的前缀都会被 403。镜像站解析出的清单因此要换 Referer 再请求。
MISSAV_CANONICAL_REFERERS = (
    "https://missav.ws/",
    "https://missav.live/",
    "https://missav.ai/",
    "https://missav123.com/",
)

JABLE_VIDEO_PATH = re.compile(r"^/videos/([^/]+)/?$", re.I)
MISSAV_DM_SEGMENT = re.compile(r"^dm\d+$", re.I)
MISSAV_LANGUAGE_SEGMENT = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.I)
# 部分镜像把详情页放在 /v/{番号}（实测 missav.cz）
MISSAV_CONTAINER_SEGMENTS = frozenset({"v", "video", "videos"})

# MissAV 的列表页/分类页也长成 /xx/slug，必须排除，否则粘贴分类页会被误判成视频
MISSAV_NON_VIDEO_SLUGS = frozenset({
    "new", "release", "releases", "chinese-subtitle", "chinese-subtitles",
    "uncensored-leak", "uncensored", "actresses", "actress", "genres", "genre",
    "makers", "maker", "directors", "labels", "series", "playlist", "playlists",
    "search", "saved", "history", "login", "register", "about", "contact",
    "dmca", "terms", "privacy", "en", "cn", "ja", "ko", "zh", "tw",
})

# 番号后缀：MissAV 用 URL 尾巴表示片源属性，番号本体要剥掉
CODE_SUFFIX_PATTERN = re.compile(
    r"-(?:uncensored-leak|uncensored|leak|chinese-subtitle|english-subtitle|ch-sub|en-sub|zh-sub)$",
    re.I,
)
UNCENSORED_SUFFIX_PATTERN = re.compile(r"-(?:uncensored-leak|uncensored)$", re.I)

# surrit / nineyu 的流 ID 是带连字符的 UUID（36 字符）
STREAM_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
STREAM_ID_LOOSE_PATTERN = re.compile(r"[0-9a-f-]{36}", re.I)

# 页面被 Cloudflare 拦下时的特征串。注意 challenge-platform 在正常页面里也会出现
# （CF 的常规引导脚本），只有短页面才当作拦截，否则会误杀完整详情页。
CHALLENGE_TITLE_PATTERN = re.compile(
    r"<title>\s*(?:just a moment|attention required|请稍候|正在检查|access denied)",
    re.I,
)
CHALLENGE_STRONG_MARKERS = (
    "cf_chl_opt",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
)
CHALLENGE_MARKERS = ("challenge-platform",)
CHALLENGE_MAX_BYTES = 8000

TAG_PATTERN = re.compile(r"<[^>]+>")

# 站点在片源不可用（地区限制 / 未登录 / 反爬降级）时会往播放器塞广告流，
# Jable 实测就是把 hlsUrl 指向这些联盟域名。命中即视为「没有正片」。
AD_HOST_MARKERS = (
    "mushroomtrack", "tapioni", "adxadserv", "labadena", "magsrv", "fluxtrck",
    "mayzaent", "javhd-trk", "mnaspm", "theporndude", "doubleclick",
    "googlesyndication", "googletagmanager", "adservice", "popads",
)


@dataclass(frozen=True)
class SitePage:
    site: str
    pageUrl: str
    code: str
    slug: str
    isUncensored: bool = False


# ── URL 判定 ──


def matchSite(url: str) -> str | None:
    """返回 "jable" / "missav" / None，只按域名判断。"""
    host = (urlparse(str(url or "").strip()).hostname or "").lower()
    if not host:
        return None
    if JABLE_HOST_PATTERN.match(host):
        return "jable"
    if MISSAV_HOST_PATTERN.match(host):
        return "missav"
    return None


def normalizePageUrl(url: str) -> str:
    """去掉 #片段，保留查询串（部分站点把语言/跟踪参数放在查询里）。"""
    parsed = urlparse(str(url or "").strip())
    return parsed._replace(fragment="").geturl()


def missavSlug(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    parts = [p for p in parts if not MISSAV_DM_SEGMENT.match(p)]
    while parts and (
        MISSAV_LANGUAGE_SEGMENT.match(parts[0])
        or parts[0].lower() in MISSAV_CONTAINER_SEGMENTS
    ):
        parts = parts[1:]
    return parts[0] if parts else ""


def stripCodeSuffix(slug: str) -> str:
    code = slug
    while True:
        stripped = CODE_SUFFIX_PATTERN.sub("", code)
        if stripped == code:
            return code
        code = stripped


def toSitePage(url: str) -> SitePage | None:
    """把 URL 解析成视频详情页；不是视频页返回 None。"""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    site = matchSite(url)
    if site is None:
        return None

    if site == "jable":
        matched = JABLE_VIDEO_PATH.match(parsed.path)
        if not matched:
            return None
        slug = matched.group(1).strip()
        if not slug or not any(char.isdigit() for char in slug):
            return None
        return SitePage("jable", normalizePageUrl(url), slug, slug, False)

    slug = missavSlug(parsed.path)
    if not slug or not any(char.isdigit() for char in slug):
        return None
    if slug.lower() in MISSAV_NON_VIDEO_SLUGS:
        return None
    code = stripCodeSuffix(slug)
    if not code:
        return None
    return SitePage(
        "missav", normalizePageUrl(url), code, slug,
        bool(UNCENSORED_SUFFIX_PATTERN.search(slug)),
    )


def isVideoPage(url: str) -> bool:
    return toSitePage(url) is not None


# 主站被 Cloudflare 拦时用来兜底的镜像（实测 .fans / .media 对同一番号稳定可用）
MISSAV_MIRROR_HOSTS = ("missav.fans", "missav.media")


def pageCandidates(page: SitePage) -> list[str]:
    """主站打不开时依次尝试的等价地址（同番号、不同镜像/路径形态）。"""
    if page.site != "missav":
        return []

    candidates: list[str] = []
    seen = {page.pageUrl.rstrip("/")}

    def add(url: str) -> None:
        key = url.rstrip("/")
        if key not in seen:
            seen.add(key)
            candidates.append(url)

    for host in MISSAV_MIRROR_HOSTS:
        add(f"https://{host}/{page.slug}")
        for language in ("en", "cn"):
            add(f"https://{host}/{language}/{page.code}")
        add(f"https://{host}/v/{page.code}")

    return candidates


def pageOrigin(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def refererFor(page: SitePage) -> str:
    """MissAV 的 surrit 校验的是站点根 Referer，脚本里用的就是 origin + "/"。"""
    origin = pageOrigin(page.pageUrl)
    if page.site == "missav":
        return f"{origin}/" if origin else ""
    return page.pageUrl


# ── 播放清单提取 ──


def _unescapeUrl(text: str) -> str:
    return htmlLib.unescape(text.replace("\\/", "/")).strip().strip("\"'").strip()


def _absolute(streamUrl: str, pageUrl: str) -> str:
    from urllib.parse import urljoin
    return urljoin(pageUrl, _unescapeUrl(streamUrl))


JABLE_STREAM_PATTERNS = (
    # 站点自己的播放器变量
    re.compile(r"""var\s+hlsUrl\s*=\s*['"]([^'"]+)['"]"""),
    re.compile(r"""hlsUrl\s*[:=]\s*['"]([^'"]+\.m3u8[^'"]*)['"]"""),
    # 页内播放器标签
    re.compile(r"""<source[^>]+src\s*=\s*['"]([^'"]+\.m3u8[^'"]*)['"]""", re.I),
    re.compile(r"""data-(?:url|src|video)\s*=\s*['"]([^'"]+\.m3u8[^'"]*)['"]""", re.I),
)

MISSAV_PLAYLIST_PATTERN = re.compile(
    r"https?:(?:\\?/){2}(surrit|nineyu)\.com(?:\\?/)+([0-9a-f-]{36})(?:\\?/)+(?:playlist\.m3u8)",
    re.I,
)
MISSAV_SEEK_PATTERN = re.compile(
    r"(surrit|nineyu)\.com(?:\\?/)+([0-9a-f-]{36})(?:\\?/)+seek",
    re.I,
)
MISSAV_UUID_NEAR_SEEK_PATTERN = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\\?/|[\"'])[^<>]{0,40}?seek",
    re.I,
)
ANY_M3U8_PATTERN = re.compile(r"https?://[^\"'\s<>]+\.m3u8[^\"'\s<>]*", re.I)


def extractJableStream(html: str) -> str:
    candidates: list[str] = []
    for pattern in JABLE_STREAM_PATTERNS:
        for matched in pattern.finditer(html):
            value = _unescapeUrl(matched.group(1))
            if value and value not in candidates:
                candidates.append(value)
    # 兜底：任何 m3u8 链接，优先带 token 的正式播放地址
    for matched in ANY_M3U8_PATTERN.finditer(html):
        value = _unescapeUrl(matched.group())
        if value and value not in candidates:
            candidates.append(value)

    if not candidates:
        return ""
    for candidate in candidates:
        if not isAdStream(candidate):
            if "/hls/" in candidate or "token=" in candidate:
                return candidate
    for candidate in candidates:
        if not isAdStream(candidate):
            return candidate
    return candidates[0]


def extractMissavStream(html: str) -> str:
    matched = MISSAV_PLAYLIST_PATTERN.search(html)
    if matched:
        return f"https://{matched.group(1).lower()}.com/{matched.group(2)}/playlist.m3u8"

    matched = MISSAV_SEEK_PATTERN.search(html)
    if matched:
        return f"https://{matched.group(1).lower()}.com/{matched.group(2)}/playlist.m3u8"

    matched = MISSAV_UUID_NEAR_SEEK_PATTERN.search(html)
    if matched:
        return f"https://surrit.com/{matched.group(1)}/playlist.m3u8"

    # 对齐油猴脚本的取值方式：seek 前 38~2 个字符正好是 36 位流 ID
    seekIndex = html.find("seek")
    if seekIndex > 38:
        candidate = html[seekIndex - 38:seekIndex - 2]
        if STREAM_ID_LOOSE_PATTERN.fullmatch(candidate):
            return f"https://surrit.com/{candidate}/playlist.m3u8"

    candidates = [_unescapeUrl(m) for m in ANY_M3U8_PATTERN.findall(html)]
    for candidate in candidates:
        if "surrit.com" in candidate or "nineyu.com" in candidate:
            return candidate
    return ""


def extractStream(site: str, html: str, pageUrl: str = "") -> str:
    """返回绝对播放清单地址；提取不到返回空串。"""
    if site == "jable":
        found = extractJableStream(html)
    elif site == "missav":
        found = extractMissavStream(html)
    else:
        raise ValueError(f"unsupported site: {site}")
    if not found:
        return ""
    return _absolute(found, pageUrl) if pageUrl else _unescapeUrl(found)


# ── 标题与命名 ──


def _textOf(markup: str) -> str:
    return re.sub(r"\s+", " ", htmlLib.unescape(TAG_PATTERN.sub(" ", markup or ""))).strip()


def parsePageTitle(html: str) -> str:
    if not html:
        return ""
    matched = re.search(
        r"""<meta[^>]+property\s*=\s*['"]og:title['"][^>]*>""", html, re.I,
    )
    if matched:
        content = re.search(r"""content\s*=\s*['"]([^'"]*)['"]""", matched.group(0), re.I)
        if content and content.group(1).strip():
            return _textOf(content.group(1))

    matched = re.search(r"""<meta[^>]+content\s*=\s*['"]([^'"]*)['"][^>]+property\s*=\s*['"]og:title['"]""", html, re.I)
    if matched and matched.group(1).strip():
        return _textOf(matched.group(1))

    matched = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if matched and _textOf(matched.group(1)):
        return _textOf(matched.group(1))

    matched = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return _textOf(matched.group(1)) if matched else ""


SITE_SUFFIX_PATTERN = re.compile(
    r"\s*[-|–—]\s*(?:Jable(?:\.TV)?|MissAV(?:\.\w+)?|免费.*|在线观看.*)\s*$",
    re.I,
)


def cleanTitle(title: str, code: str = "") -> str:
    """去掉站点后缀、番号前缀和多余分隔符。"""
    text = re.sub(r"\s+", " ", htmlLib.unescape(str(title or ""))).strip()
    for _ in range(3):
        stripped = SITE_SUFFIX_PATTERN.sub("", text)
        if stripped == text:
            break
        text = stripped
    if code:
        text = re.sub(rf"^\s*[\[【(（]?\s*{re.escape(code)}\s*[\]】)）]?\s*[-–—:：|]?\s*", "", text, flags=re.I)
    return text.strip(" -–—:|·")


def buildName(
    code: str,
    title: str,
    isUncensored: bool = False,
    mode: str = "code-title",
    uncensoredTag: str = "[无码破解]",
) -> str:
    """拼装输出文件名（不含扩展名）。

    mode:
      - code-title: 番号 + 标题（MissAV 整页解析默认）
      - title:      仅标题，缺标题时退回番号
      - code:       仅番号
    """
    normalizedCode = str(code or "").strip().upper()
    normalizedTitle = cleanTitle(title, normalizedCode)

    if mode == "code" or not normalizedTitle:
        return normalizedCode or normalizedTitle or "video"
    if mode == "title":
        return normalizedTitle

    parts = [normalizedCode] if normalizedCode else []
    tag = str(uncensoredTag or "").strip()
    if isUncensored and tag:
        parts.append(tag)
    parts.append(normalizedTitle)
    return " ".join(part for part in parts if part).strip() or "video"


def isAdStream(url: str) -> bool:
    host = (urlparse(str(url or "").strip()).hostname or "").lower()
    return any(marker in host for marker in AD_HOST_MARKERS)


def isChallengePage(html: str) -> bool:
    if not html:
        return False
    if CHALLENGE_TITLE_PATTERN.search(html):
        return True
    lowered = html.lower()
    if any(marker in lowered for marker in CHALLENGE_STRONG_MARKERS):
        return True
    # 完整详情页同样带 challenge-platform 脚本，只有「短得只剩拦截页」才算被拦
    return len(html) < CHALLENGE_MAX_BYTES and any(
        marker in lowered for marker in CHALLENGE_MARKERS
    )
