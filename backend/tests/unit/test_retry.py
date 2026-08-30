import random

import pytest

from app.retry import full_jitter_delay


def test_full_jitter_is_bounded() -> None:
    rng = random.Random(42)
    for attempt in range(1, 12):
        delay = full_jitter_delay(
            attempt, base_delay_seconds=2, max_delay_seconds=30, rng=rng
        )
        assert 0 <= delay <= min(30, 2 * (2 ** (attempt - 1)))


def test_backoff_rejects_invalid_attempt() -> None:
    with pytest.raises(ValueError):
        full_jitter_delay(0)


def test_backoff_rejects_non_positive_delay() -> None:
    with pytest.raises(ValueError):
        full_jitter_delay(1, base_delay_seconds=0)
