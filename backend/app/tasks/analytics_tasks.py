"""
Phase 14 — Analytics Celery Tasks
===================================
Tasks registered in celery_app beat schedule:
  • flush_event_buffer       — Redis → Supabase analytics_events (every 60s)
  • run_health_checks_task   — platform fail rate + disk + queue alerts (every 5 min)
  • flush_analytics_daily    — aggregate analytics_events → analytics_daily (daily 23:30 UTC)
  • send_phase14_daily_digest — enhanced daily digest with Growth/Scale metrics (daily 23:55 UTC)
"""

from app.core.celery_app import celery_app


# ── Flush Redis event buffer → Supabase ──────────────────────────────
@celery_app.task(name="flush_event_buffer", ignore_result=True)
def flush_event_buffer():
    import json, os, time
    from datetime import datetime, timezone
    try:
        import redis as _redis
        rc = _redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
        BATCH = 500
        raw = rc.lrange("vg:events:buffer", 0, BATCH - 1)
        if not raw:
            return
        rc.ltrim("vg:events:buffer", BATCH, -1)

        from app.core.database import get_supabase_client
        sb = get_supabase_client()
        rows = []
        for item in raw:
            try:
                e = json.loads(item)
                rows.append({
                    "event_name":          e.get("event_name"),
                    "user_id":             e.get("user_id"),
                    "anonymous_id":        e.get("anonymous_id"),
                    "source":              e.get("source", "web"),
                    "properties":          e.get("properties", {}),
                    "experiment_variants": e.get("experiment_variants", {}),
                    "created_at":          datetime.fromtimestamp(e.get("ts", time.time()), tz=timezone.utc).isoformat(),
                })
            except Exception:
                pass
        if rows:
            sb.table("analytics_events").insert(rows).execute()
            print(f"[Analytics] Flushed {len(rows)} events to Supabase")
    except Exception as ex:
        print(f"[Analytics] flush_event_buffer error: {ex}")


# ── Health check cron ─────────────────────────────────────────────────
@celery_app.task(name="run_health_checks_task", ignore_result=True)
def run_health_checks_task():
    try:
        from app.core.alerts import run_all_health_checks
        run_all_health_checks()
    except Exception as e:
        print(f"[Alerts] health check task error: {e}")


# ── Daily aggregate flush ─────────────────────────────────────────────
@celery_app.task(name="flush_analytics_daily", ignore_result=True)
def flush_analytics_daily():
    """Aggregate yesterday's analytics_events into analytics_daily."""
    from datetime import datetime, timezone, timedelta
    import json
    try:
        from app.core.database import get_supabase_client
        sb = get_supabase_client()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        since = f"{yesterday}T00:00:00+00:00"
        until = f"{yesterday}T23:59:59+00:00"

        events = sb.table("analytics_events").select("event_name,source,user_id,anonymous_id") \
            .gte("created_at", since).lte("created_at", until).limit(50000).execute()

        rows = events.data or []
        counters: dict = {}

        def inc(metric, dims, val=1):
            k = (metric, json.dumps(dims, sort_keys=True))
            counters[k] = counters.get(k, 0) + val

        unique_users = set()
        for r in rows:
            inc("event_count", {"event": r["event_name"], "source": r.get("source", "web")})
            inc("events_by_source", {"source": r.get("source", "web")})
            uid = r.get("user_id") or r.get("anonymous_id")
            if uid:
                unique_users.add(uid)

        inc("unique_users", {}, len(unique_users))

        # Pull download_jobs stats for yesterday
        jobs = sb.table("download_jobs").select("status,platform,source") \
            .gte("created_at", since).lte("created_at", until).limit(20000).execute()
        for j in (jobs.data or []):
            inc("download_by_status", {"status": j["status"], "source": j.get("source", "web")})
            if j.get("platform"):
                inc("download_by_platform", {"platform": j["platform"], "status": j["status"]})

        # Upsert all aggregates
        upsert_rows = [
            {
                "date": yesterday,
                "metric": metric,
                "dimensions": json.loads(dims),
                "value": val,
            }
            for (metric, dims), val in counters.items()
        ]
        if upsert_rows:
            sb.table("analytics_daily").upsert(upsert_rows, on_conflict="date,metric,dimensions").execute()
            print(f"[Analytics] Flushed {len(upsert_rows)} daily aggregates for {yesterday}")
    except Exception as e:
        print(f"[Analytics] flush_analytics_daily error: {e}")


# ── Phase 14 Daily Digest ─────────────────────────────────────────────
@celery_app.task(name="send_phase14_daily_digest", ignore_result=True)
def send_phase14_daily_digest():
    """Enhanced daily digest: downloads + sources + conversion signals + top errors."""
    from datetime import datetime, timezone, timedelta
    try:
        from app.core.database import get_supabase_client
        from app.core.notifications import send_telegram_message_sync
        sb = get_supabase_client()

        today = datetime.now(timezone.utc).date()
        since = f"{today}T00:00:00+00:00"

        jobs = sb.table("download_jobs").select("status,platform,source,error_message") \
            .gte("created_at", since).limit(20000).execute()
        rows = jobs.data or []

        total = len(rows)
        success = sum(1 for r in rows if r["status"] == "success")
        failed  = sum(1 for r in rows if r["status"] in ("failed", "error"))
        fail_rate = failed / max(total, 1)

        sources: dict = {}
        platforms: dict = {}
        errors: dict = {}
        for r in rows:
            src = r.get("source", "web") or "web"
            sources[src] = sources.get(src, 0) + 1
            plat = r.get("platform") or "unknown"
            platforms[plat] = platforms.get(plat, 0) + 1
            if r["status"] in ("failed", "error") and r.get("error_message"):
                short = (r["error_message"] or "")[:60]
                errors[short] = errors.get(short, 0) + 1

        top_platforms = sorted(platforms.items(), key=lambda x: -x[1])[:5]
        top_errors    = sorted(errors.items(), key=lambda x: -x[1])[:3]
        src_lines = "\n".join(f"  • {k}: {v}" for k, v in sorted(sources.items(), key=lambda x: -x[1]))
        plat_lines = "\n".join(f"  • {p}: {c}" for p, c in top_platforms)
        err_lines  = "\n".join(f"  • {e[:50]}: {c}" for e, c in top_errors) or "  — Không có lỗi nổi bật"

        # Pro conversion signal: paywall events from analytics_events
        paywall_hits = 0
        try:
            pe = sb.table("analytics_events").select("id", count="exact") \
                .eq("event_name", "paywall_seen").gte("created_at", since).execute()
            paywall_hits = pe.count or 0
        except Exception:
            pass

        msg = (
            f"📊 <b>VidGrab Daily Digest</b> — {today.strftime('%d/%m/%Y')}\n\n"
            f"📥 <b>Downloads:</b> {total} tổng | ✅ {success} OK | ❌ {failed} lỗi ({fail_rate:.0%})\n\n"
            f"🌐 <b>Theo nguồn:</b>\n{src_lines}\n\n"
            f"🎯 <b>Top nền tảng:</b>\n{plat_lines}\n\n"
            f"🔒 <b>Paywall hits:</b> {paywall_hits}\n\n"
            f"⚠️ <b>Top lỗi:</b>\n{err_lines}"
        )
        send_telegram_message_sync(msg)
    except Exception as e:
        print(f"[Digest] send_phase14_daily_digest error: {e}")
