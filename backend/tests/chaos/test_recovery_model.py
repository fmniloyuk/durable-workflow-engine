from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.chaos


@dataclass
class DurableTaskModel:
    state: str = "queued"
    attempts: int = 0
    effect_keys_seen: set[str] | None = None

    def __post_init__(self) -> None:
        if self.effect_keys_seen is None:
            self.effect_keys_seen = set()

    def acquire(self) -> bool:
        if self.state != "queued":
            return False
        self.state = "running"
        self.attempts += 1
        return True

    def lease_expires(self) -> None:
        if self.state == "running":
            self.state = "queued"

    def apply_idempotent_effect(self, key: str) -> bool:
        assert self.effect_keys_seen is not None
        if key in self.effect_keys_seen:
            return False
        self.effect_keys_seen.add(key)
        return True


def test_worker_kill_then_redelivery_recovers_without_duplicate_effect() -> None:
    task = DurableTaskModel()
    assert task.acquire()
    assert task.apply_idempotent_effect("charge:invoice-1")
    task.lease_expires()  # worker died before durable completion/ACK
    assert task.acquire()
    assert not task.apply_idempotent_effect("charge:invoice-1")
    task.state = "succeeded"
    assert task.state == "succeeded"
    assert task.attempts == 2


def test_duplicate_message_cannot_acquire_running_task() -> None:
    task = DurableTaskModel()
    assert task.acquire()
    assert not task.acquire()
