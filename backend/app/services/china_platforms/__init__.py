"""
China Platform Access Layer — Phase 32B-1, wave 1.

Shared policy / routing / budget / health / observability for China-origin
platforms. Everything is OFF by default (CHINA_ACCESS_ENABLED=false); see
docs/china-access/ for the audit, design, flags and rollout.

Modules import lazily from each other where they touch Redis or the existing
extractors, so importing this package has no side effects.
"""
