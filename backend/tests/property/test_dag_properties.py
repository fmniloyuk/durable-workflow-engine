import pytest
from hypothesis import given, strategies as st

from app.dag import validate_dag
from app.schemas import TaskDefinition

pytestmark = pytest.mark.property


@st.composite
def acyclic_workflows(draw: st.DrawFn) -> list[TaskDefinition]:
    size = draw(st.integers(min_value=1, max_value=25))
    tasks: list[TaskDefinition] = []
    for index in range(size):
        possible = [f"t{i}" for i in range(index)]
        if possible:
            dependencies = draw(
                st.lists(
                    st.sampled_from(possible),
                    unique=True,
                    max_size=min(5, len(possible)),
                )
            )
        else:
            dependencies = []
        tasks.append(TaskDefinition(key=f"t{index}", depends_on=dependencies))
    return tasks


@given(acyclic_workflows())
def test_topological_order_respects_every_edge(tasks: list[TaskDefinition]) -> None:
    order = validate_dag(tasks)
    assert set(order) == {task.key for task in tasks}
    positions = {key: index for index, key in enumerate(order)}
    for task in tasks:
        for dependency in task.depends_on:
            assert positions[dependency] < positions[task.key]
