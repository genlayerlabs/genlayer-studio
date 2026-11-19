const WEI_PER_GEN = 10n ** 18n;

export const parseGenAmountToWei = (value: string): bigint | null => {
  const normalized = value.trim();
  if (!/^\d+(?:\.\d{1,18})?$/.test(normalized)) {
    return null;
  }

  const parts = normalized.split('.');
  const whole = parts[0] ?? '0';
  const fraction = parts[1] ?? '';
  return BigInt(whole) * WEI_PER_GEN + BigInt(fraction.padEnd(18, '0') || '0');
};

export const formatGenBalance = (wei: bigint): string => {
  const thousandths = WEI_PER_GEN / 1000n;
  if (wei > 0n && wei < thousandths) return '<0.001';
  const rounded = (wei + thousandths / 2n) / thousandths;
  const whole = rounded / 1000n;
  const fraction = Number(rounded % 1000n) / 1000;
  const suffix = fraction
    .toLocaleString(undefined, {
      maximumFractionDigits: 3,
    })
    .slice(1);
  return whole.toLocaleString() + suffix;
};
