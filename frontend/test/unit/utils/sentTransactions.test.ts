import { describe, expect, it } from 'vitest';
import { compareTimestamps } from '@/utils/sentTransactions';

describe('compareTimestamps', () => {
  it('keeps microsecond precision', () => {
    expect(
      compareTimestamps(
        '2026-11-18T10:00:00.123900',
        '2026-11-18T10:00:00.123100',
      ),
    ).toBeGreaterThan(0);
    expect(
      compareTimestamps(
        '2026-11-18T10:00:00.1231',
        '2026-11-18T10:00:00.123100',
      ),
    ).toBe(0);
  });

  it('orders across seconds and with a timezone suffix', () => {
    expect(
      compareTimestamps('2026-11-18T10:00:01Z', '2026-11-18T10:00:00.999999Z'),
    ).toBeGreaterThan(0);
    expect(
      compareTimestamps('2026-11-18T10:00:00', '2026-11-18T10:00:00.000001'),
    ).toBeLessThan(0);
  });
});
