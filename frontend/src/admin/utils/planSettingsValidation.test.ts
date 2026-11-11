import test from 'node:test'
import assert from 'node:assert/strict'
import {
  type PlanSettings,
  buildPlanSettingsPayload,
  findMismatchedPlanFields,
  findSameCycleDuplicateVndWarnings,
} from './planSettingsValidation.ts'

const BASE_SETTINGS: PlanSettings = {
  starter_month_usd_cents: 199,
  starter_year_usd_cents: 2499,
  starter_month_vnd: 49000,
  starter_year_vnd: 590000,
  price_month_usd_cents: 399,
  price_year_usd_cents: 3900,
  price_month_vnd: 99000,
  price_year_vnd: 890000,
  team_month_usd_cents: 1199,
  team_year_usd_cents: 11900,
  team_month_vnd: 279000,
  team_year_vnd: 2790000,
  api_calls_month_pro: 3000,
  api_calls_month_team: 15000,
  bank_name: 'ACB',
  bank_account: '0766223536',
  bank_holder: 'NGUYEN THIEN TRIEU',
  contact: '',
  rapidapi_proxy_secret: '',
  rapidapi_url: '',
  paypal_me_url: 'thientrieu753@gmail.com',
  bank_bin: '970416',
  sepay_webhook_secret: 'whsec_test',
}

test('findMismatchedPlanFields returns empty array when Redis readback matches submitted values', () => {
  const payload = buildPlanSettingsPayload({
    ...BASE_SETTINGS,
    bank_holder: '  NGUYEN   THIEN TRIEU ',
  })
  const savedFromServer: PlanSettings = {
    ...BASE_SETTINGS,
    bank_holder: 'NGUYEN THIEN TRIEU',
  }
  assert.deepEqual(findMismatchedPlanFields(payload, savedFromServer), [])
})

test('findMismatchedPlanFields flags every field that failed to persist on server', () => {
  const payload = buildPlanSettingsPayload(BASE_SETTINGS)
  const staleServerResponse: PlanSettings = {
    ...BASE_SETTINGS,
    price_month_vnd: 49000,
    price_month_usd_cents: 699,
  }
  const mismatches = findMismatchedPlanFields(payload, staleServerResponse)
  assert.equal(mismatches.length, 2)
  assert.ok(mismatches.some(m => m.includes('Giá Pro / tháng (VNĐ)')))
  assert.ok(mismatches.some(m => m.includes('Giá Pro / tháng (USD Cents)')))
})

test('findSameCycleDuplicateVndWarnings detects duplicate or <=1000 VND collisions in same cycle only', () => {
  // Distinct prices per cycle -> zero warnings
  assert.deepEqual(findSameCycleDuplicateVndWarnings(BASE_SETTINGS), [])

  // Starter month == Pro month == 49000 -> warns for monthly cycle
  const dupMonth = findSameCycleDuplicateVndWarnings({
    ...BASE_SETTINGS,
    price_month_vnd: 49000,
  })
  assert.equal(dupMonth.length, 1)
  assert.ok(dupMonth[0].includes('Starter và Pro'))
  assert.ok(dupMonth[0].includes('kỳ tháng'))

  // Within SePay 1000 VND tolerance -> warns
  const nearYear = findSameCycleDuplicateVndWarnings({
    ...BASE_SETTINGS,
    starter_year_vnd: 889500,
    price_year_vnd: 890000,
  })
  assert.equal(nearYear.length, 1)
  assert.ok(nearYear[0].includes('kỳ năm'))
})
