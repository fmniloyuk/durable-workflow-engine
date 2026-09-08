"""Why at-least-once execution needs idempotent effects.

Run with: python examples/idempotency_demo.py
"""

from dataclasses import dataclass, field


@dataclass
class NaivePaymentGateway:
    charges: list[int] = field(default_factory=list)

    def charge(self, cents: int) -> None:
        self.charges.append(cents)


@dataclass
class IdempotentPaymentGateway:
    charges: dict[str, int] = field(default_factory=dict)

    def charge(self, *, idempotency_key: str, cents: int) -> None:
        self.charges.setdefault(idempotency_key, cents)


def simulate_redelivery() -> None:
    naive = NaivePaymentGateway()
    safe = IdempotentPaymentGateway()

    # Attempt 1 performs the side effect. Imagine the worker crashes before
    # persisting task success / ACKing Redis, so the task is delivered again.
    naive.charge(5000)
    safe.charge(idempotency_key="invoice:INV-42:charge", cents=5000)

    # Attempt 2 is the at-least-once redelivery.
    naive.charge(5000)
    safe.charge(idempotency_key="invoice:INV-42:charge", cents=5000)

    print("naive charge count:", len(naive.charges))
    print("idempotent charge count:", len(safe.charges))
    assert len(naive.charges) == 2
    assert len(safe.charges) == 1


if __name__ == "__main__":
    simulate_redelivery()
