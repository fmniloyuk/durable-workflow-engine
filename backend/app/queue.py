import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any

from opentelemetry.propagate import inject
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.config import get_settings

CONSUMER_GROUP = "dwe-workers"


@dataclass(frozen=True)
class QueueMessage:
    stream: str
    message_id: str
    task_id: str
    workflow_id: str
    queue: str
    headers: dict[str, str]


class RedisTransport:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.settings = get_settings()

    def partition_for(self, task_id: str) -> int:
        digest = hashlib.blake2b(task_id.encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.settings.queue_partitions

    def stream_name(self, queue: str, partition: int) -> str:
        return f"dwe:q:{queue}:{partition}"

    async def ensure_groups(self, queues: list[str]) -> None:
        for queue in queues:
            for partition in range(self.settings.queue_partitions):
                stream = self.stream_name(queue, partition)
                try:
                    await self.redis.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
                except ResponseError as exc:
                    if "BUSYGROUP" not in str(exc):
                        raise

    async def publish_task(self, *, queue: str, task_id: str, workflow_id: str) -> str:
        partition = self.partition_for(task_id)
        stream = self.stream_name(queue, partition)
        carrier: dict[str, str] = {}
        inject(carrier)
        fields = {
            "task_id": task_id,
            "workflow_id": workflow_id,
            "queue": queue,
            "traceparent": carrier.get("traceparent", ""),
            "tracestate": carrier.get("tracestate", ""),
        }
        return str(await self.redis.xadd(stream, fields))

    async def publish_poison(self, *, source_stream: str, fields: dict[str, str], error: str) -> None:
        await self.redis.xadd(
            "dwe:dlq:poison",
            {"source_stream": source_stream, "error": error, "payload": repr(fields)},
        )

    async def publish_dead_letter(self, payload: dict[str, Any]) -> None:
        await self.redis.xadd(
            "dwe:dlq:tasks",
            {key: str(value) for key, value in payload.items()},
        )

    async def _decode_entries(
        self, stream: str, entries: list[tuple[str, dict[str, str]]]
    ) -> list[QueueMessage]:
        messages: list[QueueMessage] = []
        for message_id, fields in entries:
            try:
                task_id = fields["task_id"]
                workflow_id = fields["workflow_id"]
                queue = fields["queue"]
            except KeyError as exc:
                await self.publish_poison(
                    source_stream=stream, fields=fields, error=f"missing field {exc}"
                )
                await self.redis.xack(stream, CONSUMER_GROUP, message_id)
                continue
            try:
                uuid.UUID(str(task_id))
                uuid.UUID(str(workflow_id))
            except ValueError as exc:
                await self.publish_poison(
                    source_stream=stream, fields=fields, error=f"invalid UUID field: {exc}"
                )
                await self.redis.xack(stream, CONSUMER_GROUP, message_id)
                continue
            messages.append(
                QueueMessage(
                    stream=stream,
                    message_id=str(message_id),
                    task_id=str(task_id),
                    workflow_id=str(workflow_id),
                    queue=str(queue),
                    headers={
                        "traceparent": str(fields.get("traceparent", "")),
                        "tracestate": str(fields.get("tracestate", "")),
                    },
                )
            )
        return messages

    async def reclaim_stale(
        self, queues: list[str], worker_id: str, *, count: int = 10
    ) -> list[QueueMessage]:
        min_idle_ms = max(
            self.settings.lease_seconds,
            self.settings.heartbeat_seconds * 3,
        ) * 1000
        messages: list[QueueMessage] = []
        for queue in queues:
            for partition in range(self.settings.queue_partitions):
                remaining = count - len(messages)
                if remaining <= 0:
                    return messages
                stream = self.stream_name(queue, partition)
                result = await self.redis.xautoclaim(
                    stream,
                    CONSUMER_GROUP,
                    worker_id,
                    min_idle_ms,
                    start_id="0-0",
                    count=remaining,
                )
                entries = result[1] if len(result) > 1 else []
                messages.extend(await self._decode_entries(stream, entries))
        return messages

    async def read(self, queues: list[str], worker_id: str, *, count: int = 10) -> list[QueueMessage]:
        reclaimed = await self.reclaim_stale(queues, worker_id, count=count)
        if reclaimed:
            return reclaimed

        streams = {
            self.stream_name(queue, partition): ">"
            for queue in queues
            for partition in range(self.settings.queue_partitions)
        }
        response = await self.redis.xreadgroup(
            CONSUMER_GROUP,
            worker_id,
            streams=streams,
            count=count,
            block=1000,
        )
        messages: list[QueueMessage] = []
        for stream, entries in response:
            messages.extend(await self._decode_entries(str(stream), entries))
        return messages

    async def ack(self, message: QueueMessage) -> None:
        await self.redis.xack(message.stream, CONSUMER_GROUP, message.message_id)


_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])
local values = redis.call('HMGET', key, 'tokens', 'updated')
local tokens = tonumber(values[1])
local updated = tonumber(values[2])
if tokens == nil then tokens = capacity end
if updated == nil then updated = now end
local elapsed = math.max(0, now - updated)
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'updated', now)
redis.call('PEXPIRE', key, ttl_ms)
return allowed
"""

_CONCURRENCY_ACQUIRE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZSCORE', key, member) then
  redis.call('ZADD', key, expires, member)
  return 1
end
if redis.call('ZCARD', key) >= limit then return 0 end
redis.call('ZADD', key, expires, member)
return 1
"""


class QueueGuards:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.settings = get_settings()

    async def wait_for_rate_token(self, queue: str) -> None:
        rate = self.settings.queue_rate_per_second
        capacity = max(1.0, rate)
        while True:
            now = time.time()
            allowed = await self.redis.eval(
                _TOKEN_BUCKET_LUA,
                1,
                f"dwe:rate:{queue}",
                now,
                rate,
                capacity,
                1,
                60_000,
            )
            if int(allowed) == 1:
                return
            await asyncio.sleep(min(0.1, 1 / rate))

    async def acquire_concurrency(self, queue: str, task_id: str) -> None:
        ttl = self.settings.lease_seconds + self.settings.heartbeat_seconds * 2
        while True:
            now = time.time()
            allowed = await self.redis.eval(
                _CONCURRENCY_ACQUIRE_LUA,
                1,
                f"dwe:concurrency:{queue}",
                now,
                now + ttl,
                self.settings.queue_concurrency_limit,
                task_id,
            )
            if int(allowed) == 1:
                return
            await asyncio.sleep(0.05)

    async def renew_concurrency(self, queue: str, task_id: str) -> None:
        ttl = self.settings.lease_seconds + self.settings.heartbeat_seconds * 2
        await self.redis.zadd(f"dwe:concurrency:{queue}", {task_id: time.time() + ttl}, xx=True)

    async def release_concurrency(self, queue: str, task_id: str) -> None:
        await self.redis.zrem(f"dwe:concurrency:{queue}", task_id)
