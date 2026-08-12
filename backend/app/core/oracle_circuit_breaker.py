"""
Oracle Cloud Circuit Breaker — Fix 3
=====================================
3-state circuit breaker for the Oracle VPS direct extraction path.

CLOSED  → normal operation, direct attempts allowed
OPEN    → oracle IP is bot-blocked, skip direct for 10 min
HALF    → probe period: allow one direct attempt to test recovery

State transitions:
  CLOSED + 3 fails in 5 min  → OPEN  (auto, from record_failure)
  OPEN   + 10 min elapsed    → HALF  (auto, from get_state)
  HALF   + probe success     → CLOSED (from record_success)
  HALF   + probe failure     → OPEN  (from record_failure)

Redis keys:
  oracle_circuit:state     → "closed" | "open" | "half"
  oracle_circuit:fail_count → consecutive failure count (TTL = _FAIL_WINDOW)
  oracle_circuit:open_since → unix timestamp when OPEN started
  oracle_circuit:last_probe → unix timestamp of last probe attempt
"""

import time
from app.core.redis_client import get_redis

_KEY_STATE      = "oracle_circuit:state"
_KEY_FAILS      = "oracle_circuit:fail_count"
_KEY_OPEN_SINCE = "oracle_circuit:open_since"
_KEY_LAST_PROBE = "oracle_circuit:last_probe"

_FAIL_WINDOW    = 300   # 5-min sliding window to count failures
_FAIL_THRESHOLD = 3     # failures within window → OPEN
_OPEN_DURATION  = 600   # 10 min in OPEN before auto-HALF


class OracleCircuitBreaker:
    """Redis-backed 3-state circuit breaker for Oracle VPS direct YouTube extraction."""

    def get_state(self) -> str:
        """Return current state: 'closed', 'open', or 'half'."""
        try:
            rc = get_redis()
            raw = rc.get(_KEY_STATE)
            state = (raw if isinstance(raw, str) else raw.decode()) if raw else "closed"

            if state == "open":
                open_since_raw = rc.get(_KEY_OPEN_SINCE)
                if open_since_raw:
                    open_since = float(open_since_raw if isinstance(open_since_raw, str) else open_since_raw.decode())
                    if time.time() - open_since >= _OPEN_DURATION:
                        rc.set(_KEY_STATE, "half")
                        rc.set(_KEY_LAST_PROBE, str(time.time()))
                        print("[OracleCircuit] OPEN → HALF (auto, 10 min elapsed)")
                        return "half"
            return state
        except Exception:
            return "closed"  # fail-safe: allow attempt

    def record_success(self) -> None:
        """Call after a successful direct Oracle extraction."""
        try:
            rc = get_redis()
            state = self.get_state()
            if state == "half":
                rc.set(_KEY_STATE, "closed")
                rc.delete(_KEY_FAILS, _KEY_OPEN_SINCE)
                print("[OracleCircuit] HALF → CLOSED (probe success)")
            elif state == "closed":
                rc.delete(_KEY_FAILS)
        except Exception:
            pass

    def record_failure(self) -> None:
        """Call after a bot-block/403 on direct Oracle extraction."""
        try:
            rc = get_redis()
            state = self.get_state()
            if state == "half":
                rc.set(_KEY_STATE, "open")
                rc.set(_KEY_OPEN_SINCE, str(time.time()))
                rc.set(_KEY_FAILS, "0")
                print("[OracleCircuit] HALF → OPEN (probe failed)")
                return

            pipe = rc.pipeline()
            pipe.incr(_KEY_FAILS)
            pipe.expire(_KEY_FAILS, _FAIL_WINDOW)
            results = pipe.execute()
            fail_count = results[0]

            if fail_count >= _FAIL_THRESHOLD and state == "closed":
                rc.set(_KEY_STATE, "open")
                rc.set(_KEY_OPEN_SINCE, str(time.time()))
                rc.delete(_KEY_FAILS)
                print(f"[OracleCircuit] CLOSED → OPEN ({fail_count} fails in {_FAIL_WINDOW}s window)")
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return status dict for admin dashboard."""
        try:
            rc = get_redis()
            state = self.get_state()
            fail_count_raw = rc.get(_KEY_FAILS)
            open_since_raw = rc.get(_KEY_OPEN_SINCE)
            open_since = float(open_since_raw if isinstance(open_since_raw, str) else open_since_raw.decode()) if open_since_raw else 0
            return {
                "state": state,
                "fail_count": int(fail_count_raw or 0),
                "open_since": open_since,
                "seconds_until_half": max(0, _OPEN_DURATION - (time.time() - open_since)) if state == "open" and open_since else 0,
            }
        except Exception:
            return {"state": "unknown", "fail_count": 0, "open_since": 0, "seconds_until_half": 0}


oracle_circuit = OracleCircuitBreaker()
