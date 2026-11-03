from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from app.client import buildClient, toEmulation
from app.config.cfg import cfg
from app.i18n import N
from app.models.pack import FeaturePack, TaskParser
from app.models.task import PageTaskOptions, Task, TaskError, TaskOptions
from app.platform.filesystem import toSafeFilename

from m3u8_pack import M3U8Pack, M3U8Parser
from m3u8_pack.config import m3u8Config

from . import relay
from .config import javConfig
from .sites import (
    MISSAV_CANONICAL_REFERERS,
    SITE_TITLES,
    SitePage,
    buildName,
    extractStream,
    isAdStream,
    isChallengePage,
    pageCandidates,
    parsePageTitle,
    refererFor,
    toSitePage,
)

PAGE_TIMEOUT = 20

# 页面请求里这些头由本模块统一接管，或对站点无害但会干扰模拟身份
SKIP_HEADERS = {
    "host", "content-length", "accept-encoding", "connection", "accept",
    "referer", "user-agent", "upgrade-insecure-requests",
    "if-none-match", "if-modified-since", "range",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
}


def defaultUserAgent() -> str:
    """N_m3u8DL-RE 不走浏览器模拟，必须显式给它一个真实 UA，否则 surrit 会拒绝。"""
    try:
        from app.client import PROFILES_BY_FAMILY
        major = PROFILES_BY_FAMILY["chrome"][0][1][0]
    except Exception:
        major = 131

    if sys.platform == "win32":
        platform = "Windows NT 10.0; Win64; x64"
    elif sys.platform == "darwin":
        platform = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        platform = "X11; Linux x86_64"
    return (
        f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def _looksLikeVideoPage(site: str, html: str) -> bool:
    """判断页面是否真的带播放器数据，避免把「页面能打开但没有片源」当成成功。"""
    lowered = (html or "").lower()
    if site == "jable":
        return "hlsurl" in lowered or ".m3u8" in lowered
    return "surrit" in lowered or "seek" in lowered or ".m3u8" in lowered


def statusOf(response) -> int:
    """wreq 的 StatusCode 不是 int，这里统一转成三位数字状态码。"""
    status = getattr(response, "status", 0)
    asInt = getattr(status, "as_int", None)
    if callable(asInt):
        return int(asInt())
    try:
        return int(status)
    except (TypeError, ValueError):
        digits = "".join(char for char in str(status) if char.isdigit())
        return int(digits[:3]) if digits else 0


def buildHeaders(page: SitePage, source: dict[str, str], userAgent: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in (source or {}).items():
        key = str(name or "").strip()
        if not key or value is None or key.lower() in SKIP_HEADERS:
            continue
        headers[key] = str(value)
    referer = refererFor(page)
    if referer:
        headers["Referer"] = referer
    headers["User-Agent"] = userAgent
    return headers


class JavParser(TaskParser):
    # 高于通用 M3U8(80) 与 GitHub(90)：详情页链接必须由站点规则先接管
    priority = 95

    def match(self, options: TaskOptions) -> bool:
        if not javConfig.enabled.value:
            return False
        return toSitePage(options.url) is not None

    def matchPassive(self, options: TaskOptions) -> bool:
        return self.match(options)

    async def parse(self, options: TaskOptions) -> Task:
        page = toSitePage(options.url)
        if page is None:
            raise TaskError(
                "链接不是 {site} 的视频详情页：{url}",
                site="Jable / MissAV", url=options.url,
            )

        userAgent = (options.userAgent or options.sourceUserAgent or "").strip() or defaultUserAgent()
        html, page = await self._fetchPage(page, options, userAgent)
        headers = buildHeaders(page, options.headers, userAgent)

        streamUrl = extractStream(page.site, html, page.pageUrl)
        if not streamUrl:
            raise TaskError(
                "在 {site} 页面中没有找到播放清单（{code}），站点结构可能已变更："
                "可在浏览器播放后复制 m3u8 链接交给通用解析",
                site=SITE_TITLES[page.site], code=page.code,
            )
        if isAdStream(streamUrl):
            raise TaskError(
                "{site} 只返回了广告占位流（{host}），说明该片源在当前网络下不可用："
                "请更换节点或登录后重试",
                site=SITE_TITLES[page.site], host=urlparse(streamUrl).netloc,
            )
        logger.info("{} 解析到播放清单: {}", SITE_TITLES[page.site], urlparse(streamUrl).netloc)
        if page.site == "missav":
            headers["Referer"] = await self._resolveReferer(page, streamUrl, options, headers)

        if javConfig.useRelay.value and relay.shouldRelay(streamUrl):
            streamUrl = await self._wrapWithRelay(streamUrl, headers)

        pageTitle = options.pageTitle if isinstance(options, PageTaskOptions) else ""
        title = (pageTitle or "").strip() or parsePageTitle(html)
        baseName = buildName(
            page.code, title, page.isUncensored,
            javConfig.namingMode.value, javConfig.uncensoredTag.value,
        )

        # 清单探测、直播判定、清晰度枚举、下载步骤参数全部复用 M3U8 Pack
        manifestOptions = replace(options, url=streamUrl, headers=headers)
        try:
            task = await M3U8Parser().parse(manifestOptions)
        except TaskError:
            raise
        except Exception as e:
            raise TaskError(
                "获取 {site} 播放清单失败：{detail}",
                site=SITE_TITLES[page.site],
                detail=str(e) or type(e).__name__,
            ) from e

        suffix = Path(task.name).suffix or f".{m3u8Config.outputFormat.value}"
        # 标题往往很长，压缩到 120 字符以内，避免 Windows 全路径超 260
        stem = toSafeFilename(baseName, fallback=toSafeFilename(page.code.upper() or "video"), maxLength=120)
        task.name = f"{stem}{suffix}"
        task.packId = "jav"
        if javConfig.saveByCodeFolder.value and page.code:
            task.outputFolder = task.outputFolder / toSafeFilename(page.code.upper(), fallback="video")
        return task

    async def _wrapWithRelay(self, streamUrl: str, headers: dict[str, str]) -> str:
        """surrit 会拒绝官方 N_m3u8DL-RE 的指纹，改走 127.0.0.1 中转由 wreq 取流。"""
        try:
            base = await relay.ensureStarted(javConfig.relayPort.value)
            payload = relay.encodeSession(
                streamUrl, headers.get("Referer", ""), headers.get("User-Agent", ""),
            )
            wrapped = relay.relayPlaylistUrl(base, payload)
            logger.info("播放清单改走本地中转: {}", base)
            return wrapped
        except Exception as error:
            logger.warning("本地中转不可用，回退直连: {}", repr(error))
            return streamUrl

    async def _resolveReferer(
        self, page: SitePage, streamUrl: str, options: TaskOptions, headers: dict[str, str],
    ) -> str:
        """MissAV 的清单只认几个老品牌域名的 Referer，镜像域名要换一个再请求。"""
        current = headers.get("Referer", "")
        candidates = ([current] if current else []) + [
            candidate for candidate in MISSAV_CANONICAL_REFERERS if candidate != current
        ]

        attempts: list[str] = []
        for candidate in candidates:
            try:
                status = await self._probeManifest(streamUrl, {**headers, "Referer": candidate}, options)
            except Exception as error:
                attempts.append(f"{candidate} → {type(error).__name__}")
                continue
            if 200 <= status < 300:
                if candidate != current:
                    logger.info("MissAV 清单要求 Referer={}，已自动切换", candidate)
                return candidate
            attempts.append(f"{candidate} → {status}")

        raise TaskError(
            "MissAV 播放清单拒绝了所有候选来源（{detail}），请更换网络出口后重试",
            detail="；".join(attempts),
        )

    async def _probeManifest(self, url: str, headers: dict[str, str], options: TaskOptions) -> int:
        emulation = toEmulation(
            options.clientProfile or cfg.clientProfile.value,
            options.sourceUserAgent,
        )
        client = buildClient(
            emulation=emulation,
            headers=headers,
            userAgent=options.userAgent or None,
            sourceUserAgent=options.sourceUserAgent,
            timeout=PAGE_TIMEOUT,
        )
        try:
            response = await client.get(url)
            return statusOf(response)
        finally:
            client.close()

    async def _fetchPage(
        self, page: SitePage, options: TaskOptions, userAgent: str,
    ) -> tuple[str, SitePage]:
        """主站打不开/被拦时自动换镜像，返回 (页面 HTML, 实际命中的站点信息)。"""
        statuses: list[str] = []
        for url in [page.pageUrl, *pageCandidates(page)]:
            host = urlparse(url).netloc
            candidatePage = toSitePage(url) or page
            headers = buildHeaders(candidatePage, options.headers, userAgent)
            try:
                body, finalUrl = await self._fetchOne(url, options, headers)
            except TaskError as error:
                statuses.append(f"{host} → {error}")
                continue

            if not _looksLikeVideoPage(page.site, body):
                statuses.append(f"{host} → 页面没有播放器数据")
                continue

            if url != page.pageUrl:
                logger.info("{} 改用镜像 {} 打开 {}", SITE_TITLES[page.site], host, page.code)
            resolved = candidatePage if finalUrl == url else replace(candidatePage, pageUrl=finalUrl)
            return body, resolved

        raise TaskError(
            "打不开 {site} 页面（{detail}）：主站可能被拦截或该番号已下架，可换节点后重试",
            site=SITE_TITLES[page.site], detail="；".join(statuses[:3]),
        )

    async def _fetchOne(
        self, url: str, options: TaskOptions, headers: dict[str, str],
    ) -> tuple[str, str]:
        emulation = toEmulation(
            options.clientProfile or cfg.clientProfile.value,
            options.sourceUserAgent,
        )
        client = buildClient(
            emulation=emulation,
            headers=headers,
            userAgent=options.userAgent or None,
            sourceUserAgent=options.sourceUserAgent,
            timeout=PAGE_TIMEOUT,
        )
        try:
            response = await client.get(url)
            status = statusOf(response)
            if status != 200:
                raise TaskError("HTTP {status}", status=status)
            body = await response.text()
            finalUrl = str(response.url)
        finally:
            client.close()

        if isChallengePage(body):
            raise TaskError("人机验证页")
        return body, finalUrl


class JavPack(FeaturePack):
    packId = "jav"
    capturedLinks = N("JavPack", "Jable / MissAV 视频")
    config = javConfig
    parsers = [JavParser]

    def taskCardClass(self, task: Task) -> type | None:
        from m3u8_pack.cards import M3U8LiveTaskCard, M3U8TaskCard
        return M3U8LiveTaskCard if getattr(task, "isLive", False) else M3U8TaskCard

    def draftCardClass(self, task: Task) -> type | None:
        from m3u8_pack.cards import M3U8DraftCard
        return M3U8DraftCard

    def optionCards(self, task, parent=None):
        return M3U8Pack.optionCards(self, task, parent)

    def editCards(self, task, parent=None):
        return M3U8Pack.editCards(self, task, parent)

    async def activate(self):
        if not javConfig.enabled.value or not javConfig.useRelay.value:
            return
        try:
            base = await relay.ensureStarted(javConfig.relayPort.value)
            logger.info("JavPack 本地中转已就绪: {}", base)
        except Exception as error:
            logger.warning("JavPack 本地中转启动失败: {}", repr(error))

    async def deactivate(self):
        await relay.stop()
