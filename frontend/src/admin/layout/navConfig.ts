import {
  LayoutDashboard, Globe, Cookie, Network, ListOrdered, Briefcase, ChartColumn,
  HeartPulse, Funnel, Radio, Users, Settings, BookOpen, History, ScrollText,
  Gauge, Siren, Play, Building2, KeyRound, Webhook, Wallet, Sparkles,
  SlidersHorizontal, Shield, Activity,
  type LucideIcon,
} from 'lucide-react'
import type { AdminRole } from '../types/admin.types'

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  minRole?: AdminRole
  /**
   * Kept out of the sidebar, still routed and reachable by URL.
   *
   * The menu had 24 entries and most of them could not show anything on this
   * deployment: the whole Enterprise group is backed by tables holding either
   * zero rows or leftovers from one "R27 Pilot Tenant" trial in August, and
   * Billing in particular cannot ever populate because no STRIPE_* variable
   * exists in the environment. A menu that lists mostly dead ends trains you
   * to ignore it, which is how a real signal gets missed.
   *
   * Nothing is deleted — the routes stay in AdminRoutes.tsx, so bookmarks keep
   * working, and re-listing one is deleting a single line.
   */
  hidden?: boolean
}

export interface NavSection {
  label: string | null
  items: NavItem[]
}

const NAV_OVERVIEW: NavItem[] = [
  { href: '/vid-admin', label: 'Overview', icon: LayoutDashboard, minRole: 'viewer' },
]

const NAV_MONITOR: NavItem[] = [
  { href: '/vid-admin/platforms', label: 'Platforms', icon: Globe, minRole: 'viewer' },
  { href: '/vid-admin/cookies', label: 'Cookies', icon: Cookie, minRole: 'viewer' },
  { href: '/vid-admin/proxy', label: 'Proxy', icon: Network, minRole: 'viewer' },
  { href: '/vid-admin/queue', label: 'Queue', icon: ListOrdered, minRole: 'viewer' },
  { href: '/vid-admin/jobs', label: 'Jobs', icon: Briefcase, minRole: 'viewer' },
  { href: '/vid-admin/analytics', label: 'Analytics', icon: ChartColumn, minRole: 'viewer' },
  { href: '/vid-admin/probes', label: 'Sức khoẻ nền tảng', icon: HeartPulse, minRole: 'viewer' },
  { href: '/vid-admin/funnel', label: 'Funnel', icon: Funnel, minRole: 'viewer' },
  { href: '/vid-admin/youtube-gate', label: 'YouTube Gate', icon: Play, minRole: 'operator' },
  // Ops Signals is the aggregated "is anything wrong right now" view. Queue
  // Health and Anomalies answer the same question from the same Redis state in
  // a different arrangement, so they are folded behind it rather than listed
  // three times. Queue Health also owns the auto-tune controls — reachable at
  // /vid-admin/queue-health when those are needed.
  { href: '/vid-admin/ops-signals', label: 'Ops Signals', icon: Radio, minRole: 'viewer' },
  { href: '/vid-admin/queue-health', label: 'Queue Health', icon: Gauge, minRole: 'viewer', hidden: true },
  { href: '/vid-admin/anomalies', label: 'Anomalies', icon: Siren, minRole: 'viewer', hidden: true },
]

const NAV_MANAGE: NavItem[] = [
  { href: '/vid-admin/users', label: 'Users', icon: Users, minRole: 'operator' },
  { href: '/vid-admin/config', label: 'Config', icon: Settings, minRole: 'admin' },
  { href: '/vid-admin/playbooks', label: 'Playbooks', icon: BookOpen, minRole: 'operator' },
  { href: '/vid-admin/automation-history', label: 'Automation', icon: History, minRole: 'viewer' },
]

// The partner/multi-tenant surface. Every page here reads a table that is
// empty or holds only the August "R27 Pilot Tenant" trial data — api_keys,
// webhook_endpoints, analysis_jobs, payment_events, user_credits and
// user_presets are all at zero rows. Re-list a line here the day that feature
// has real customers.
const NAV_ENTERPRISE: NavItem[] = [
  { href: '/vid-admin/tenants',     label: 'Tenants',     icon: Building2,         minRole: 'admin', hidden: true },
  { href: '/vid-admin/api-keys',    label: 'API Keys',    icon: KeyRound,          minRole: 'admin', hidden: true },
  { href: '/vid-admin/webhooks',    label: 'Webhooks',    icon: Webhook,           minRole: 'admin', hidden: true },
  { href: '/vid-admin/usage',       label: 'Usage',       icon: Activity,          minRole: 'admin', hidden: true },
  { href: '/vid-admin/ai-analysis', label: 'AI Analysis', icon: Sparkles,          minRole: 'admin', hidden: true },
  { href: '/vid-admin/billing',     label: 'Billing',     icon: Wallet,            minRole: 'admin', hidden: true },
  { href: '/vid-admin/presets',     label: 'Presets',     icon: SlidersHorizontal, minRole: 'admin', hidden: true },
]

const NAV_SYSTEM: NavItem[] = [
  // Admin session management, superadmin-only, with a single admin account.
  { href: '/vid-admin/access', label: 'Access', icon: Shield, minRole: 'superadmin', hidden: true },
  { href: '/vid-admin/audit', label: 'Audit Log', icon: ScrollText, minRole: 'admin' },
]

export const NAV_SECTIONS: NavSection[] = [
  { label: null, items: NAV_OVERVIEW },
  { label: 'Monitor', items: NAV_MONITOR },
  { label: 'Manage', items: NAV_MANAGE },
  { label: 'Enterprise', items: NAV_ENTERPRISE },
  { label: 'System', items: NAV_SYSTEM },
]

/** Title for the top bar. Hidden-but-routed pages still resolve. */
export function titleForPath(pathname: string): string {
  const clean = pathname.replace(/\/+$/, '') || '/vid-admin'
  const all = NAV_SECTIONS.flatMap(s => s.items)
  const hit = all.find(i => i.href === clean)
  return hit ? hit.label : 'vid-admin'
}

export const MOBILE_TABS: NavItem[] = [
  NAV_OVERVIEW[0],
  NAV_MONITOR[0],
  NAV_MONITOR[1],
  NAV_MONITOR[4],
  NAV_MONITOR[3],
]
