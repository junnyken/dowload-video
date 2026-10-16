"""POST /discover-container dispatches discover_container_task with
max_items / include_sections / raw_input. Celery checks kwargs against the
task signature before sending, so a mismatch (`raw_input` vs `_raw_input`)
made every Browse request a 500 while tests that mock the task stayed green."""
import inspect

import app.api.container as container_api
from app.tasks.container_tasks import discover_container_task


def test_dispatch_kwargs_match_task_signature():
    src = inspect.getsource(container_api.discover_container)
    for key in ("max_items", "include_sections", "raw_input"):
        assert f'"{key}":' in src
    # Same binding Celery's check_arguments does (self is bound already).
    inspect.signature(discover_container_task.run).bind(
        "job", "https://www.tiktok.com/@tiktok", "cid",
        max_items=10, include_sections=None, raw_input="https://www.tiktok.com/@tiktok",
    )
