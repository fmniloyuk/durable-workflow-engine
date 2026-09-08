import random


def full_jitter_delay(
    attempt: int,
    *,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 300.0,
    rng: random.Random | None = None,
) -> float:
    """AWS-style full-jitter exponential backoff.

    `attempt` is 1-based: the first retry samples [0, base_delay].
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if base_delay_seconds <= 0 or max_delay_seconds <= 0:
        raise ValueError("backoff delays must be positive")
    ceiling = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
    generator = rng or random.SystemRandom()
    return generator.uniform(0, ceiling)
