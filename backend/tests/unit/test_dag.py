import pytest

from app.dag import DAGValidationError, validate_dag
from app.schemas import TaskDefinition


def task(key: str, depends_on: list[str] | None = None) -> TaskDefinition:
    return TaskDefinition(key=key, depends_on=depends_on or [])


def test_valid_dag_returns_topological_order() -> None:
    tasks = [task("a"), task("b", ["a"]), task("c", ["a"]), task("d", ["b", "c"])]
    order = validate_dag(tasks)
    positions = {key: index for index, key in enumerate(order)}
    assert positions["a"] < positions["b"] < positions["d"]
    assert positions["a"] < positions["c"] < positions["d"]


def test_rejects_cycle() -> None:
    with pytest.raises(DAGValidationError, match="cycle"):
        validate_dag([task("a", ["b"]), task("b", ["a"])])


def test_rejects_missing_dependency() -> None:
    with pytest.raises(DAGValidationError, match="unknown task"):
        validate_dag([task("a", ["missing"])])


def test_rejects_duplicate_keys() -> None:
    with pytest.raises(DAGValidationError, match="unique"):
        validate_dag([task("a"), task("a")])


def test_rejects_duplicate_dependency_edges() -> None:
    with pytest.raises(DAGValidationError, match="duplicate dependencies"):
        validate_dag([task("a"), task("b", ["a", "a"])])
