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

export type FeePolicy = Record<string, string> | undefined;

const timeoutBounds = {
  leaderTimeunitsAllocation: ['minProposeTimeunits', 'maxProposeTimeunits'],
  validatorTimeunitsAllocation: ['minCommitTimeunits', 'maxCommitTimeunits'],
} as const;
type TimeoutField = keyof typeof timeoutBounds;

/** Returns an error message when the timeout is outside the active policy. */
export function feeTimeoutError(
  field: TimeoutField,
  input: string,
  policy: FeePolicy,
): string | null {
  const value = input.trim();
  if (!value || !policy) return null;
  const [minKey, maxKey] = timeoutBounds[field];
  const min = Number(policy[minKey]);
  const max = Number(policy[maxKey]);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  const n = Number(value);
  if (n >= min && n <= max) return null;
  return `${feeFields[field]} must be between ${min} and ${max}.`;
}

/** True when appeal rounds and the rotation schedule agree (or one is empty). */
export function isConsistentSchedule(settings: FeeSettings) {
  const rounds = settings.appealRounds.trim();
  const rotations = settings.rotations.trim();
  return (
    !rounds || !rotations || rotations.split(',').length === Number(rounds) + 1
  );
}

/**
 * Drops values the backend would reject, so a bad stored or environment
 * override cannot block every write and deployment. Returns what was dropped.
 */
export function sanitizeFeeSettings(
  settings: FeeSettings,
  policy?: FeePolicy,
): { settings: FeeSettings; dropped: string[] } {
  const clean = { ...settings };
  const dropped: string[] = [];
  for (const field of Object.keys(timeoutBounds) as TimeoutField[]) {
    if (feeTimeoutError(field, clean[field], policy)) {
      clean[field] = '';
      dropped.push(feeFields[field]);
    }
  }
  if (!isConsistentSchedule(clean)) {
    clean.appealRounds = '';
    clean.rotations = '';
    dropped.push(feeFields.appealRounds, feeFields.rotations);
  }
  return { settings: clean, dropped };
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
  return sanitizeFeeSettings(settings).settings;
}

/** Returns false when the browser refused the write. */
export function saveFeeSettings(settings: FeeSettings): boolean {
  try {
    localStorage.setItem(storageKey, JSON.stringify(settings));
    return true;
  } catch {
    // Keep the settings in memory when storage is unavailable.
    return false;
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
