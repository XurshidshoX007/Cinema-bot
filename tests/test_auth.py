"""HMAC signing and verification tests."""

import time

from services.stats_webapp_auth import (
    build_signed_stats_webapp_url,
    verify_signed_stats_webapp_request,
    build_signed_share_query,
    verify_signed_share_request,
)


def test_signed_stats_url_roundtrip():
    """Sign qilingan URL tekshiruvdan o'tishi kerak."""
    url = build_signed_stats_webapp_url("https://example.com/", 12345)
    assert "uid=12345" in url
    assert "sig=" in url

    # URL'dan query parametrlarni ajratish
    from urllib.parse import urlsplit, parse_qs
    query = parse_qs(urlsplit(url).query)
    assert verify_signed_stats_webapp_request(query)


def test_signed_stats_expired():
    """Muddati o'tgan URL rad etilishi kerak."""
    from urllib.parse import urlsplit, parse_qs
    url = build_signed_stats_webapp_url("https://example.com/", 12345, ttl_seconds=30)
    query = parse_qs(urlsplit(url).query)

    # Vaqtni kelajakka surish
    future_time = int(time.time()) + 3600
    assert not verify_signed_stats_webapp_request(query, now=future_time)


def test_signed_stats_tampered():
    """O'zgartirilgan signature rad etilishi kerak."""
    from urllib.parse import urlsplit, parse_qs
    url = build_signed_stats_webapp_url("https://example.com/", 12345)
    query = parse_qs(urlsplit(url).query)
    query["sig"] = ["tampered_signature"]
    assert not verify_signed_stats_webapp_request(query)


def test_share_link_roundtrip():
    """Share link sign va verify."""
    code = "test123"
    query = build_signed_share_query(code)
    assert "exp" in query
    assert "sig" in query
    assert verify_signed_share_request(code, query)


def test_share_link_wrong_code():
    """Boshqa kod bilan verify qilish rad etilishi kerak."""
    query = build_signed_share_query("test123")
    assert not verify_signed_share_request("wrong_code", query)


def test_share_media_flag():
    """Media flag bilan sign va verify."""
    code = "movie42"
    query = build_signed_share_query(code, media=True)
    assert verify_signed_share_request(code, query, media=True)
    assert not verify_signed_share_request(code, query, media=False)
