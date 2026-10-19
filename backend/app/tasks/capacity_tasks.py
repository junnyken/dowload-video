"""Capacity sampling (2026-10-19): one record per minute of concurrent
downloads, refusals, CPU and memory — see app.core.capacity."""
from app.core.celery_app import celery_app


@celery_app.task(name="capacity_sample", ignore_result=True)
def capacity_sample():
    from app.core import capacity
    return capacity.sample()
