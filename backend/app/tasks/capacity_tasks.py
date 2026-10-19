"""Capacity sampling (2026-10-19): one record per minute of concurrent
downloads, refusals, CPU and memory — see app.core.capacity."""
from app.core.celery_app import celery_app


@celery_app.task(name="capacity_sample", ignore_result=True)
def capacity_sample():
    from app.core import capacity
    return capacity.sample()


@celery_app.task(name="proxy_health_check", ignore_result=True, time_limit=300, soft_time_limit=280)
def proxy_health_check():
    """Every admin proxy: alive or dead and why (app.core.proxy_health)."""
    from app.core import proxy_health
    out = proxy_health.run()
    print(f"[ProxyHealth] {out.get('alive')}/{out.get('total')} alive")
    return {"alive": out.get("alive"), "total": out.get("total")}
