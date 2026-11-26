from key_pool import KeyPool, parse_keys


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_parse_keys_drops_blanks_and_placeholders():
    assert parse_keys(" k1 , ,k2,sk-xxx1,gsk_your-groq-key ") == ["k1", "k2"]
    assert parse_keys("") == []


def test_round_robin_rotation():
    pool = KeyPool({"groq": ["a", "b", "c"]})
    assert [pool.get_key("groq") for _ in range(6)] == ["a", "b", "c", "a", "b", "c"]


def test_unknown_provider_has_no_key():
    assert KeyPool({"groq": ["a"]}).get_key("openai") is None


def test_rate_limited_key_is_skipped_until_cooldown_ends():
    clock = FakeClock()
    pool = KeyPool({"groq": ["a", "b"]}, clock=clock)
    pool.mark_rate_limited("a", seconds=60)

    assert [pool.get_key("groq") for _ in range(3)] == ["b", "b", "b"]
    assert pool.stats() == {"groq": {"total": 2, "available": 1}}

    clock.now += 61
    assert {pool.get_key("groq") for _ in range(4)} == {"a", "b"}
    assert pool.stats() == {"groq": {"total": 2, "available": 2}}


def test_all_keys_cooling_returns_none():
    clock = FakeClock()
    pool = KeyPool({"groq": ["a", "b"]}, clock=clock)
    pool.mark_rate_limited("a", seconds=30)
    pool.mark_rate_limited("b", seconds=30)
    assert pool.get_key("groq") is None
    assert pool.stats()["groq"]["available"] == 0

    clock.now += 31
    assert pool.get_key("groq") in {"a", "b"}
