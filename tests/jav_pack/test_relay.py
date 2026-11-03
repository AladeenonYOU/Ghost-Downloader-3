from __future__ import annotations

import pytest

from jav_pack.relay import (
    decodeSession,
    encodeSession,
    relayPlaylistUrl,
    relaySegmentUrl,
    rewritePlaylist,
    shouldRelay,
)

BASE = "https://surrit.com/8f2a1c34-9b7d-4e56-a1b2-3c4d5e6f7a8b"


def test_session_roundtrip():
    payload = encodeSession(f"{BASE}/playlist.m3u8", "https://missav.ws/", "UA/1.0")
    assert "=" not in payload
    assert decodeSession(payload) == {
        "url": f"{BASE}/playlist.m3u8",
        "referer": "https://missav.ws/",
        "userAgent": "UA/1.0",
    }


def test_session_survives_non_ascii_referer():
    payload = encodeSession(f"{BASE}/playlist.m3u8", "https://missav.ws/中文/", "UA")
    assert decodeSession(payload)["referer"] == "https://missav.ws/中文/"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{BASE}/playlist.m3u8", True),
        ("https://nineyu.com/abc/seek/index.m3u8", True),
        ("https://cdn.jable.tv/hls/x/index.m3u8", False),
        ("http://127.0.0.1:18640/p/abc.m3u8", False),
        ("", False),
    ],
)
def test_should_relay(url: str, expected: bool):
    assert shouldRelay(url) is expected


def test_relay_urls():
    payload = encodeSession(f"{BASE}/playlist.m3u8", "https://missav.ws/", "UA")
    assert relayPlaylistUrl("http://127.0.0.1:18640", payload) == f"http://127.0.0.1:18640/p/{payload}.m3u8"
    segment = relaySegmentUrl("http://127.0.0.1:18640", payload, f"{BASE}/1920x1080/seg0.ts")
    assert segment.startswith("http://127.0.0.1:18640/s/")
    assert "?u=" in segment


def test_rewrite_master_playlist_keeps_tags():
    text = (
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n"
        "1920x1080/video.m3u8\n"
    )
    out = rewritePlaylist(text, f"{BASE}/playlist.m3u8", lambda u: f"R[{u}]")
    assert "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080" in out
    assert f"R[{BASE}/1920x1080/video.m3u8]" in out


def test_rewrite_media_playlist_with_key_and_map():
    text = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x01\n'
        '#EXT-X-MAP:URI="init.mp4"\n'
        "#EXTINF:4.000,\nseg0.ts\n#EXTINF:4.000,\nseg1.ts\n#EXT-X-ENDLIST\n"
    )
    out = rewritePlaylist(text, f"{BASE}/1920x1080/video.m3u8", lambda u: f"R[{u}]")
    assert f'URI="R[{BASE}/1920x1080/key.bin]"' in out
    assert f'URI="R[{BASE}/1920x1080/init.mp4]"' in out
    assert f"R[{BASE}/1920x1080/seg0.ts]" in out
    assert f"R[{BASE}/1920x1080/seg1.ts]" in out
    assert "#EXT-X-ENDLIST" in out


def test_rewrite_keeps_absolute_urls_untouched_in_shape():
    text = "#EXTM3U\nhttps://cdn.example.com/a/seg0.ts\n"
    out = rewritePlaylist(text, f"{BASE}/playlist.m3u8", lambda u: f"R[{u}]")
    assert "R[https://cdn.example.com/a/seg0.ts]" in out
