export interface PlanSettings {
  starter_month_usd_cents: number
  starter_year_usd_cents: number
  starter_month_vnd: number
  starter_year_vnd: number
  price_month_usd_cents: number
  price_year_usd_cents: number
  price_month_vnd: number
  price_year_vnd: number
  team_month_usd_cents: number
  team_year_usd_cents: number
  team_month_vnd: number
  team_year_vnd: number
  api_calls_month_pro: number
  api_calls_month_team: number
  bank_name: string
  bank_account: string
  bank_holder: string
  contact: string
  rapidapi_proxy_secret: string
  rapidapi_url: string
  paypal_me_url: string
  bank_bin: string
  sepay_webhook_secret: string
}

export const NUMERIC_PLAN_KEYS: ReadonlySet<keyof PlanSettings> = new Set<keyof PlanSettings>([
  'starter_month_usd_cents',
  'starter_year_usd_cents',
  'starter_month_vnd',
  'starter_year_vnd',
  'price_month_usd_cents',
  'price_year_usd_cents',
  'price_month_vnd',
  'price_year_vnd',
  'team_month_usd_cents',
  'team_year_usd_cents',
  'team_month_vnd',
  'team_year_vnd',
  'api_calls_month_pro',
  'api_calls_month_team',
])

export const PLAN_FIELD_LABELS: Record<keyof PlanSettings, string> = {
  starter_month_usd_cents: 'Giá Starter / tháng (USD Cents)',
  starter_year_usd_cents: 'Giá Starter / năm (USD Cents)',
  starter_month_vnd: 'Giá Starter / tháng (VNĐ)',
  starter_year_vnd: 'Giá Starter / năm (VNĐ)',
  price_month_usd_cents: 'Giá Pro / tháng (USD Cents)',
  price_year_usd_cents: 'Giá Pro / năm (USD Cents)',
  price_month_vnd: 'Giá Pro / tháng (VNĐ)',
  price_year_vnd: 'Giá Pro / năm (VNĐ)',
  team_month_usd_cents: 'Giá Team / tháng (USD Cents)',
  team_year_usd_cents: 'Giá Team / năm (USD Cents)',
  team_month_vnd: 'Giá Team / tháng (VNĐ)',
  team_year_vnd: 'Giá Team / năm (VNĐ)',
  api_calls_month_pro: 'Lượt gọi API / tháng — Pro',
  api_calls_month_team: 'Lượt gọi API / tháng — Team',
  bank_name: 'Ngân hàng',
  bank_account: 'Số tài khoản',
  bank_holder: 'Chủ tài khoản',
  contact: 'Liên hệ',
  rapidapi_proxy_secret: 'X-RapidAPI-Proxy-Secret',
  rapidapi_url: 'Link API trên chợ RapidAPI',
  paypal_me_url: 'Link PayPal.Me / Email PayPal',
  bank_bin: 'Mã BIN ngân hàng',
  sepay_webhook_secret: 'SePay Webhook Secret',
}

const ALL_PLAN_KEYS = Object.keys(PLAN_FIELD_LABELS) as Array<keyof PlanSettings>

function normalizeText(val: unknown): string {
  return String(val ?? '').trim().replace(/\s+/g, ' ')
}

export function buildPlanSettingsPayload(form: PlanSettings): PlanSettings {
  const out = {} as Record<keyof PlanSettings, number | string>
  for (const key of ALL_PLAN_KEYS) {
    if (NUMERIC_PLAN_KEYS.has(key)) {
      out[key] = Number(form[key] ?? 0)
    } else {
      out[key] = String(form[key] ?? '')
    }
  }
  return out as unknown as PlanSettings
}

/**
 * Compare submitted form values against the persisted values returned by Redis.
 * Returns Vietnamese labels of any fields that did not persist as submitted.
 */
export function findMismatchedPlanFields(
  submitted: PlanSettings,
  saved: Partial<PlanSettings> | null | undefined,
): string[] {
  if (!saved || typeof saved !== 'object') {
    return ALL_PLAN_KEYS.map(k => PLAN_FIELD_LABELS[k])
  }
  const mismatched: string[] = []
  for (const key of ALL_PLAN_KEYS) {
    if (NUMERIC_PLAN_KEYS.has(key)) {
      const expected = Number(submitted[key])
      const actual = Number(saved[key])
      if (!Number.isFinite(actual) || actual !== expected) {
        mismatched.push(`${PLAN_FIELD_LABELS[key]} (nhập ${expected}, lưu ${String(saved[key] ?? 'trống')})`)
      }
    } else {
      const expected = normalizeText(submitted[key])
      const actual = normalizeText(saved[key])
      if (actual !== expected) {
        mismatched.push(PLAN_FIELD_LABELS[key])
      }
    }
  }
  return mismatched
}

function formatVnd(amount: number): string {
  return `${Math.round(amount).toLocaleString('vi-VN')}₫`
}

/**
 * Warn when two tiers share the same VND price (or are within SePay's ±1.000₫
 * tolerance) in the same billing period, because SePay Case A (transfer memo
 * with only `VGxxxxxxxx`) cannot disambiguate them and routes to manual review.
 */
export function findSameCycleDuplicateVndWarnings(
  form: Partial<PlanSettings> | null | undefined,
): string[] {
  if (!form) return []
  const warnings: string[] = []

  const cycles: Array<{
    periodLabel: string
    entries: Array<{ tierLabel: string; vnd: number }>
  }> = [
    {
      periodLabel: 'kỳ tháng',
      entries: [
        { tierLabel: 'Starter', vnd: Number(form.starter_month_vnd ?? 0) },
        { tierLabel: 'Pro', vnd: Number(form.price_month_vnd ?? 0) },
        { tierLabel: 'Team', vnd: Number(form.team_month_vnd ?? 0) },
      ],
    },
    {
      periodLabel: 'kỳ năm',
      entries: [
        { tierLabel: 'Starter', vnd: Number(form.starter_year_vnd ?? 0) },
        { tierLabel: 'Pro', vnd: Number(form.price_year_vnd ?? 0) },
        { tierLabel: 'Team', vnd: Number(form.team_year_vnd ?? 0) },
      ],
    },
  ]

  for (const cycle of cycles) {
    const { periodLabel, entries } = cycle
    for (let i = 0; i < entries.length; i++) {
      for (let j = i + 1; j < entries.length; j++) {
        const a = entries[i]
        const b = entries[j]
        if (a.vnd <= 0 || b.vnd <= 0) continue
        const diff = Math.abs(a.vnd - b.vnd)
        if (diff === 0) {
          warnings.push(
            `Trùng giá VNĐ ${periodLabel}: gói ${a.tierLabel} và ${b.tierLabel} đang cùng mức ${formatVnd(a.vnd)} — chuyển khoản SePay mã VG (Case A) sẽ không phân biệt được gói và phải chuyển sang duyệt tay.`,
          )
        } else if (diff <= 1000) {
          warnings.push(
            `Giá VNĐ ${periodLabel} chênh ≤ 1.000₫: gói ${a.tierLabel} (${formatVnd(a.vnd)}) và ${b.tierLabel} (${formatVnd(b.vnd)}) nằm trong dung sai ±1.000₫ của SePay nên giao dịch mã VG sẽ phải duyệt tay.`,
          )
        }
      }
    }
  }

  return warnings
}
