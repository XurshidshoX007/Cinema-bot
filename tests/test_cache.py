"""Cache trim function tests."""

from database import _trim_cache


def test_trim_cache_under_limit():
    """Chegaradan kichik bo'lganda hech narsa o'chirmaydi."""
    cache = {"a": 1, "b": 2, "c": 3}
    _trim_cache(cache, 5)
    assert len(cache) == 3


def test_trim_cache_at_limit():
    """Chegarada teng bo'lganda o'chirmaydi."""
    cache = {"a": 1, "b": 2, "c": 3}
    _trim_cache(cache, 3)
    assert len(cache) == 3


def test_trim_cache_over_limit():
    """Chegaradan oshganda eski yozuvlarni o'chiradi."""
    cache = {f"key_{i}": i for i in range(10)}
    _trim_cache(cache, 5)
    assert len(cache) == 5


def test_trim_cache_empty():
    """Bo'sh cache'da xato chiqmaydi."""
    cache = {}
    _trim_cache(cache, 10)
    assert len(cache) == 0
