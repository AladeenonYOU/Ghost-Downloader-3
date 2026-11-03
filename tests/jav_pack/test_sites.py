from __future__ import annotations

import pytest

from jav_pack.sites import (
    buildName,
    cleanTitle,
    extractJableStream,
    extractMissavStream,
    extractStream,
    isAdStream,
    isChallengePage,
    isVideoPage,
    matchSite,
    parsePageTitle,
    refererFor,
    toSitePage,
)

STREAM_ID = "8f2a1c34-9b7d-4e56-a1b2-3c4d5e6f7a8b"


@pytest.mark.parametrize(
    ("url", "site", "code", "isUncensored"),
    [
        ("https://jable.tv/videos/abc-123/", "jable", "abc-123", False),
        ("https://fs1.app/videos/abc-123", "jable", "abc-123", False),
        ("https://www.jable.tv/videos/abc-123/?f=hot", "jable", "abc-123", False),
        ("https://missav.ws/cn/mimk-217", "missav", "mimk-217", False),
        ("https://missav.ws/dm14/en/waaa-323-uncensored-leak", "missav", "waaa-323", True),
        ("https://missav.live/ja/ssis-001-chinese-subtitle", "missav", "ssis-001", False),
        ("https://missav123.com/cn/abc-999/", "missav", "abc-999", False),
    ],
)
def test_video_pages(url: str, site: str, code: str, isUncensored: bool):
    page = toSitePage(url)
    assert page is not None
    assert page.site == site
    assert page.code == code
    assert page.isUncensored is isUncensored
    assert isVideoPage(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://jable.tv/latest-updates/",
        "https://jable.tv/models/some-model/",
        "https://missav.ws/en/new",
        "https://missav.ws/en/actresses/xyz",
        "https://missav.ws/cn/genres/drama",
        "https://example.com/videos/abc-123/",
        "ftp://jable.tv/videos/abc-123/",
        "",
    ],
)
def test_non_video_pages(url: str):
    assert toSitePage(url) is None
    assert not isVideoPage(url)


def test_match_site_only_by_host():
    assert matchSite("https://jable.tv/videos/a-1/") == "jable"
    assert matchSite("https://missav.ws/cn/a-1") == "missav"
    assert matchSite("https://notmissav.ws/cn/a-1") is None
    assert matchSite("https://example.com/?u=https://missav.ws/cn/a-1") is None


def test_jable_stream_from_global_variable():
    html = '<script>var hlsUrl = "https://cdn.jable.tv/hls/abc-123/index.m3u8?token=deadbeef";</script>'
    assert extractJableStream(html) == "https://cdn.jable.tv/hls/abc-123/index.m3u8?token=deadbeef"
    assert extractStream("jable", html, "https://jable.tv/videos/abc-123/").startswith("https://cdn.jable.tv/")


def test_jable_stream_relative_url_is_absolutized():
    html = '<script>var hlsUrl = "/hls/abc-123/index.m3u8?token=1"</script>'
    assert extractStream("jable", html, "https://jable.tv/videos/abc-123/") == (
        "https://jable.tv/hls/abc-123/index.m3u8?token=1"
    )


def test_jable_stream_falls_back_to_source_tag():
    html = '<video><source src="https://cdn.jable.tv/hls/x/index.m3u8?token=2" type="application/x-mpegURL"></video>'
    assert extractStream("jable", html, "https://jable.tv/videos/x-1/").endswith("index.m3u8?token=2")


def test_ad_stream_detection():
    assert isAdStream("https://hot-box-gen.mushroomtrack.com/hls/cqZK/63081.m3u8")
    assert isAdStream("https://a.labadena.com/api/spots/220808")
    assert not isAdStream("https://cdn.jable.tv/hls/abc/index.m3u8")
    assert not isAdStream("https://surrit.com/8f2a1c34-9b7d-4e56-a1b2-3c4d5e6f7a8b/playlist.m3u8")


def test_jable_prefers_real_stream_over_ad():
    # 真实页面里 hlsUrl 可能被指向广告联盟，同时页面里还有正片地址
    html = (
        '<script>var hlsUrl = "https://adoda-smart-coin.mushroomtrack.com/hls/a/1.m3u8";</script>'
        '<video><source src="https://cdn.jable.tv/hls/real/index.m3u8?token=2"></video>'
    )
    assert extractJableStream(html) == "https://cdn.jable.tv/hls/real/index.m3u8?token=2"


def test_jable_returns_ad_only_when_nothing_else():
    # 全部候选都是广告时仍返回，由 pack 层给出「片源不可用」的明确提示
    html = '<script>var hlsUrl = "https://x.mushroomtrack.com/hls/a/1.m3u8";</script>'
    assert isAdStream(extractJableStream(html))


def test_missav_stream_from_playlist_url():
    html = f'<script>var u="https://surrit.com/{STREAM_ID}/playlist.m3u8";</script>'
    assert extractMissavStream(html) == f"https://surrit.com/{STREAM_ID}/playlist.m3u8"


def test_missav_stream_from_escaped_playlist_url():
    html = (
        '<script>var u="https:' + "\\/" + "\\/" + f'surrit.com\\/{STREAM_ID}\\/playlist.m3u8";</script>'
    )
    assert extractMissavStream(html) == f"https://surrit.com/{STREAM_ID}/playlist.m3u8"


def test_missav_stream_from_seek_path():
    html = f'<script src="https://surrit.com/{STREAM_ID}/seek/abcdef/index.m3u8"></script>'
    assert extractMissavStream(html) == f"https://surrit.com/{STREAM_ID}/playlist.m3u8"


def test_missav_stream_from_script_heuristic():
    # 对齐油猴脚本：seek 前 38~2 个字符正好是 36 位流 ID
    html = "<script>var a=1;" + "x" * 20 + STREAM_ID + "/seek/abcdef/index.m3u8</script>"
    assert extractMissavStream(html) == f"https://surrit.com/{STREAM_ID}/playlist.m3u8"


def test_missav_stream_from_uuid_near_seek():
    html = '<script>var cfg={"uuid":"' + STREAM_ID + '","seek":1699999999}</script>'
    assert extractMissavStream(html) == f"https://surrit.com/{STREAM_ID}/playlist.m3u8"


def test_extract_stream_returns_empty_when_missing():
    assert extractStream("jable", "<html><body>nothing</body></html>", "https://jable.tv/videos/a-1/") == ""
    assert extractStream("missav", "<html><body>nothing</body></html>", "https://missav.ws/cn/a-1") == ""


def test_parse_page_title_prefers_og_title():
    html = (
        '<html><head><meta property="og:title" content="MIMK-217 示例标题 | MissAV">'
        "<title>备用标题</title></head></html>"
    )
    assert parsePageTitle(html) == "MIMK-217 示例标题 | MissAV"


def test_parse_page_title_falls_back_to_h1_then_title():
    assert parsePageTitle("<html><body><h1>H1 标题</h1></body></html>") == "H1 标题"
    assert parsePageTitle("<html><head><title>标签标题</title></head></html>") == "标签标题"
    assert parsePageTitle("") == ""


def test_clean_title_strips_site_suffix_and_code_prefix():
    assert cleanTitle("MIMK-217 示例标题 | MissAV") == "MIMK-217 示例标题"
    assert cleanTitle("MIMK-217 示例标题 | MissAV", "mimk-217") == "示例标题"
    assert cleanTitle("ABC-123 标题 - Jable.TV", "abc-123") == "标题"


def test_build_name_modes():
    assert buildName("abc-123", "ABC-123 标题 - Jable.TV") == "ABC-123 标题"
    assert buildName("abc-123", "标题", True) == "ABC-123 [无码破解] 标题"
    assert buildName("abc-123", "标题", mode="code") == "ABC-123"
    assert buildName("abc-123", "标题", mode="title") == "标题"
    assert buildName("abc-123", "") == "ABC-123"
    assert buildName("", "") == "video"
    assert buildName("abc-123", "标题", True, uncensoredTag="") == "ABC-123 标题"


def test_referer_for_missav_uses_origin_root():
    page = toSitePage("https://missav.ws/cn/mimk-217")
    assert page is not None
    assert refererFor(page) == "https://missav.ws/"

    jablePage = toSitePage("https://jable.tv/videos/abc-123/")
    assert jablePage is not None
    assert refererFor(jablePage) == "https://jable.tv/videos/abc-123/"


def test_challenge_page_detection():
    assert isChallengePage("<title>Just a moment...</title>")
    assert isChallengePage('<div id="challenge-platform"></div>')
    assert isChallengePage("<html>" + "x" * 100 + "cf_chl_opt" + "</html>")
    assert not isChallengePage("<html><title>正常页面</title></html>")


def test_challenge_detection_ignores_cf_bootstrap_on_full_page():
    # 真实详情页（数十 KB）同样内嵌 challenges.cloudflare.com 与 challenge-platform，
    # 只要页面是完整的就不能判定为拦截
    body = (
        "<html><head><title>详情页</title>"
        "<script src=\"https://challenges.cloudflare.com/turnstile/v0/api.js\"></script>"
        "<script>window.__cf={platform:'challenge-platform'}</script></head><body>"
        + "内容" * 20000
        + "</body></html>"
    )
    assert not isChallengePage(body)
