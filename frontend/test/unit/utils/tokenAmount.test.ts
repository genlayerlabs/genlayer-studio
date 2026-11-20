import { formatGenBalance, parseGenAmountToWei } from '@/utils/tokenAmount';
import { describe, expect, it } from 'vitest';

describe('parseGenAmountToWei', () => {
  it('preserves all 18 decimal places without Number precision loss', () => {
    expect(parseGenAmountToWei('0.123456789012345678')).toBe(
      123456789012345678n,
    );
    expect(parseGenAmountToWei('9007199254740993')).toBe(
      9007199254740993000000000000000000n,
    );
  });

  it('rejects values that cannot be represented exactly as wei', () => {
    expect(parseGenAmountToWei('1.0000000000000000001')).toBeNull();
    expect(parseGenAmountToWei('1e3')).toBeNull();
    expect(parseGenAmountToWei('-1')).toBeNull();
  });
});

describe('formatGenBalance', () => {
  it('distinguishes zero from a positive value below display precision', () => {
    expect(formatGenBalance(0n)).toBe((0).toLocaleString());
    expect(formatGenBalance(1n)).toBe('<0.001');
  });

  it('rounds the fraction without losing precision in the whole balance', () => {
    const whole = 9007199254740993n;
    expect(formatGenBalance(whole * 10n ** 18n + 999500000000000000n)).toBe(
      (whole + 1n).toLocaleString(),
    );
  });
});
