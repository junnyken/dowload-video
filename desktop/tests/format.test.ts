// Run: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactViews } from '../src/lib/format.ts';

test('compactViews: Vietnamese compact counts', () => {
  assert.equal(compactViews(0), '0');
  assert.equal(compactViews(950), '950');
  assert.equal(compactViews(1200), '1,2 N');
  assert.equal(compactViews(12000), '12 N');
  assert.equal(compactViews(999999), '999,9 N');
  assert.equal(compactViews(3_400_000), '3,4 Tr');
  assert.equal(compactViews(1_100_000_000), '1,1 Tỷ');
});

test('compactViews: unknown or invalid is empty', () => {
  assert.equal(compactViews(null), '');
  assert.equal(compactViews(undefined), '');
  assert.equal(compactViews(-5), '');
  assert.equal(compactViews(NaN), '');
});
