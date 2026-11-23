import type { StudioFeesDistribution } from '@/types';
import { getRuntimeConfig } from './runtimeConfig';

export const feeFields = {
  leaderTimeunitsAllocation: 'Leader timeout (time units)',
  validatorTimeunitsAllocation: 'Validator timeout (time units)',
  appealRounds: 'Appeal rounds',
  rotations: 'Rotations per round',
} as const;

export type FeeField = keyof typeof feeFields;
export type FeeSettings = Record<FeeField, string>;

const storageKey = 'consensusStore.feeSettings';
const envKeys = {
  leaderTimeunitsAllocation: 'VITE_LEADER_TIMEOUT_FEE',
  validatorTimeunitsAllocation: 'VITE_VALIDATORS_TIMEOUT_FEE',
  appealRounds: 'VITE_APPEAL_ROUNDS_FEE',
  rotations: 'VITE_ROTATIONS_FEE',
} as const;

export function isValidFeeSetting(field: FeeField, input: string) {
  const value = input.trim();
  if (value === '') return true;
  if (field === 'rotations') {
    const rotations = value.split(',');
    return (
      rotations.length <= 101 &&
      rotations.every(
        (rotation) =>
          /^\d+$/.test(rotation.trim()) &&
          Number.isSafeInteger(Number(rotation)),
      )
    );
  }
  return (
    /^\d+$/.test(value) &&
    Number.isSafeInteger(Number(value)) &&
    (field === 'appealRounds' ? Number(value) <= 100 : Number(value) > 0)
  );
}

export function loadFeeSettings(): FeeSettings {
  let stored: Partial<FeeSettings> = {};
  try {
    stored = JSON.parse(localStorage.getItem(storageKey) ?? '{}') ?? {};
  } catch {
    // Fall back to environment defaults when storage is unavailable.
  }
  const settings = {} as FeeSettings;
  for (const field of Object.keys(feeFields) as FeeField[]) {
    const value = stored[field] ?? getRuntimeConfig(envKeys[field]);
    settings[field] =
      typeof value === 'string' && isValidFeeSetting(field, value)
        ? value.trim()
        : '';
  }
  return settings;
}

export function saveFeeSettings(settings: FeeSettings) {
  try {
    localStorage.setItem(storageKey, JSON.stringify(settings));
  } catch {
    // Keep the settings in memory when storage is unavailable.
  }
}

export function applyFeeSettings(
  defaults: StudioFeesDistribution,
  settings: FeeSettings,
): StudioFeesDistribution {
  const rotations = settings.rotations
    ? settings.rotations.split(',').map((value) => String(Number(value)))
    : undefined;
  const appealRounds =
    settings.appealRounds ||
    (rotations ? String(rotations.length - 1) : defaults.appealRounds);
  const length = Number(appealRounds) + 1;
  if (rotations && rotations.length !== length) {
    throw new Error(
      'Enter one rotation count for the initial round and each appeal round.',
    );
  }
  return {
    ...defaults,
    leaderTimeunitsAllocation:
      settings.leaderTimeunitsAllocation || defaults.leaderTimeunitsAllocation,
    validatorTimeunitsAllocation:
      settings.validatorTimeunitsAllocation ||
      defaults.validatorTimeunitsAllocation,
    appealRounds,
    rotations:
      rotations ??
      Array.from({ length }, (_, index) => defaults.rotations[index] ?? '0'),
  };
}
