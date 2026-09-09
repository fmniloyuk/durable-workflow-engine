from prometheus_client import Counter, Gauge, Histogram

QUEUE_DEPTH = Gauge(
    "dwe_queue_depth",
    "Approximate Redis Stream depth by queue and partition",
    ["queue", "partition"],
)
QUEUE_METRICS_UP = Gauge(
    "dwe_queue_metrics_up",
    "Whether Redis queue depth metrics were readable",
    ["queue", "partition"],
)
TASK_LATENCY = Histogram(
    "dwe_task_latency_seconds",
    "Task execution latency",
    ["queue", "kind"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300),
)
TASK_OUTCOMES = Counter(
    "dwe_task_outcomes_total", "Task terminal outcomes", ["queue", "outcome"]
)
RETRIES = Counter("dwe_task_retries_total", "Task retries scheduled", ["queue"])
WORKER_UTILIZATION = Gauge(
    "dwe_worker_utilization_ratio", "Fraction of worker slots currently occupied", ["worker"]
)
WORKER_HEARTBEAT_FAILURES = Counter(
    "dwe_worker_heartbeat_failures_total", "Worker heartbeat persistence failures", ["worker"]
)
WORKER_PROCESS_FAILURES = Counter(
    "dwe_worker_process_failures_total", "Unhandled worker message processing failures", ["worker"]
)
DEAD_LETTER_COUNT = Gauge("dwe_dead_letter_tasks", "Durable tasks currently dead-lettered")
POISON_MESSAGES = Counter(
    "dwe_poison_messages_total", "Malformed or unprocessable queue messages", ["queue"]
)
OUTBOX_PUBLISHED = Counter(
    "dwe_outbox_published_total", "Outbox events successfully published", ["event_type"]
)
OUTBOX_PUBLISH_FAILURES = Counter(
    "dwe_outbox_publish_failures_total", "Outbox event publication failures", ["event_type"]
)
SCHEDULER_FAILURES = Counter(
    "dwe_scheduler_failures_total", "Scheduler iteration failures", ["operation"]
)
