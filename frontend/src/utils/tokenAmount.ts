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
