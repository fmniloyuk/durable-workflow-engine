from collections import defaultdict, deque

from app.schemas import TaskDefinition


class DAGValidationError(ValueError):
    pass


def validate_dag(tasks: list[TaskDefinition]) -> list[str]:
    """Validate a workflow DAG and return a stable topological ordering."""
    keys = [task.key for task in tasks]
    if len(keys) != len(set(keys)):
        raise DAGValidationError("task keys must be unique")

    known = set(keys)
    indegree = {key: 0 for key in keys}
    children: dict[str, list[str]] = defaultdict(list)

    for task in tasks:
        if len(task.depends_on) != len(set(task.depends_on)):
            raise DAGValidationError(f"task {task.key!r} contains duplicate dependencies")
        for dependency in task.depends_on:
            if dependency == task.key:
                raise DAGValidationError(f"task {task.key!r} cannot depend on itself")
            if dependency not in known:
                raise DAGValidationError(
                    f"task {task.key!r} depends on unknown task {dependency!r}"
                )
            indegree[task.key] += 1
            children[dependency].append(task.key)

    ready = deque(key for key in keys if indegree[key] == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if len(order) != len(tasks):
        cyclic = sorted(key for key, degree in indegree.items() if degree > 0)
        raise DAGValidationError(f"workflow contains a cycle involving: {', '.join(cyclic)}")
    return order
