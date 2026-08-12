# Runbook: Admin Endpoint Abuse / Lockout

> Scenario: An operator is locked out of the admin panel after too many failed login attempts, or suspicious admin actions appear in the audit log. May indicate a brute-force attempt or compromised credential.

---

## Symptoms

- Admin login returns HTTP 429 with `{"error": "locked_out", "retry_after_seconds": 900}`.
- Telegram ops alert fires: `access.denied` events from an unfamiliar IP.
- Audit log (`audit_logs` table) contains unexpected `admin.*` actions not performed by a known operator.
- Repeated `wrong_password` or `ip_not_allowed` entries in access denial logs.
- Admin session token stops working mid-session (token was invalidated by Redis flush).

---

## Quick Check

```bash
# 1. Check if a lockout is active for your IP address (replace IP)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli get "admin:lockout:YOUR_IP"'
# Returns 1 if locked out; TTL shows seconds remaining

# 2. Check attempt counter for an IP
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli get "admin:attempts:YOUR_IP"'

# 3. Check all active admin lockouts
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli --scan --pattern "admin:lockout:*"'
```

---

## Likely Causes

1. **Operator typed the wrong password 3 times** — Lockout triggers after `_MAX_ATTEMPTS = 3` failures within `_ATTEMPT_WINDOW = 5 minutes`. Lockout TTL is `_LOCKOUT_TTL = 15 minutes`. This is the most common benign cause.

2. **Automated scanning / brute-force attack** — An external actor is probing `POST /api/v1/admin/login` with password dictionaries. The lockout system engages per-IP, but an attacker rotating IPs can still hammer the endpoint.

3. **Operator IP changed mid-session** — If `ADMIN_ALLOWED_IPS` is configured, a VPN or dynamic IP change will cause 403 rejections even with a valid session token.

4. **Redis was flushed** — All active sessions (`admin:session:{token}`) and lockout state are stored in Redis. If Redis was restarted without persistence replay, all sessions are invalidated.

5. **Compromised `ADMIN_PASSWORD` or session token** — Someone has a valid credential and is performing unauthorized actions. Visible in `audit_logs` with `actor_email = "admin"` from unexpected IPs.

---

## Recovery Steps

### Step 1 — Clear the IP lockout (operator locked out of own IP)

```bash
# Get your public IP
curl -s https://api.ipify.org

# Clear lockout for your IP (replace with actual IP)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli del "admin:lockout:YOUR_IP" "admin:attempts:YOUR_IP"'

# Verify cleared
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli get "admin:lockout:YOUR_IP"'
```

### Step 2 — If you do not know your IP or cannot get in, wait out the lockout

The lockout expires automatically after **15 minutes**. No action is needed unless there is an active security incident.

### Step 3 — Check the audit log for suspicious actions

```bash
# Fetch recent admin actions (last 50)
curl -s https://dowloadvideo.io.vn/api/v1/admin/audit \
  -H "X-Admin-Token: $ADMIN_TOKEN" | python3 -m json.tool | head -100

# Fetch recent access.denied events
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.audit import get_recent_access_denials
import json
denials = get_recent_access_denials(limit=20)
for d in denials:
    print(d.get(\"created_at\",\"\")[:19], d.get(\"ip_address\",\"\"), d.get(\"metadata\",{}).get(\"reason\",\"\"))
"'
```

### Step 4 — Rotate the ADMIN_PASSWORD if compromise is suspected

```bash
# 1. Generate a new strong password
NEW_PASS=$(openssl rand -base64 24)
echo "New password: $NEW_PASS"  # Save this securely

# 2. Update on VPS
ssh vidgrab "sed -i 's/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=${NEW_PASS}/' /home/ubuntu/vidgrab/.env"

# 3. Invalidate all existing sessions (flush admin sessions in Redis)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli --scan --pattern "admin:session:*" | \
  xargs -r docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli del'

# 4. Redeploy to apply new env
bash ~/workspace/projects/Dowload-video/deploy-vps.sh --no-build
```

### Step 5 — Restrict admin access to known IP(s) only

```bash
# Add ADMIN_ALLOWED_IPS to .env (comma-separated)
ssh vidgrab 'echo "ADMIN_ALLOWED_IPS=203.0.113.10,203.0.113.11" >> /home/ubuntu/vidgrab/.env'
bash ~/workspace/projects/Dowload-video/deploy-vps.sh --no-build
```

Once set, only the listed IPs can reach any admin endpoint. Remove the env var to restore open access.

### Step 6 — Block an attacker IP at the network level

```bash
# Oracle Cloud: add a stateful ingress rule to block the IP via security list
# Or on the VPS directly:
ssh vidgrab 'sudo iptables -I INPUT -s ATTACKER_IP -j DROP'
# Make persistent:
ssh vidgrab 'sudo iptables-save > /etc/iptables/rules.v4'
```

---

## Rollback / Mitigation

- All lockout and session state is ephemeral (Redis, no TTL persistence to disk for sessions). A Redis restart clears all lockouts and sessions — operators will need to re-login but attackers' lockout state is also cleared.
- The 15-minute lockout and 3-attempt window are hardcoded constants in `admin.py` (`_LOCKOUT_TTL`, `_MAX_ATTEMPTS`, `_ATTEMPT_WINDOW`). Changing them requires a code deploy.
- If the admin API is being aggressively scanned, consider placing it behind a path prefix that is not publicly known, or adding Caddy IP allowlisting at the reverse proxy layer.

---

## When to Escalate

- Audit log shows `admin.*` actions (user plan changes, job deletions) that no operator performed — active account takeover, rotate password and revoke all sessions immediately (Step 4).
- More than 50 `access.denied` events from different IPs within 1 hour — coordinated brute-force; block at firewall/Oracle Security List level and consider temporarily disabling the admin login endpoint.
- Session tokens are being used from IPs that do not match any operator — token theft; rotate password, invalidate all sessions, and audit what actions were taken.
