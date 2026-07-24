"""Channel service tests."""

from services.channel_service import (
    normalize_channel_id,
    parse_channel_submission,
    is_valid_channel_url,
)


def test_normalize_username():
    assert normalize_channel_id("@PrimeCinema") == "@PrimeCinema"


def test_normalize_telegram_link():
    assert normalize_channel_id("https://t.me/PrimeCinema") == "@PrimeCinema"


def test_normalize_numeric_id():
    assert normalize_channel_id("1001234567") == "-1001234567"


def test_normalize_empty():
    assert normalize_channel_id("") == ""


def test_parse_channel_valid():
    result = parse_channel_submission("@test https://t.me/test Test Kanal")
    assert result is not None
    channel_id, url, name = result
    assert channel_id == "@test"
    assert url == "https://t.me/test"
    assert name == "Test Kanal"


def test_parse_channel_too_few_parts():
    assert parse_channel_submission("@test https://t.me/test") is None


def test_parse_channel_none():
    assert parse_channel_submission(None) is None


def test_valid_url_https():
    assert is_valid_channel_url("https://t.me/test") is True


def test_valid_url_http():
    assert is_valid_channel_url("http://t.me/test") is True


def test_invalid_url():
    assert is_valid_channel_url("t.me/test") is False
