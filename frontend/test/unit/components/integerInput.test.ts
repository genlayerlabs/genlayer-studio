import { describe, expect, it } from 'vitest';
import { parseIntegerInput } from '@/components/global/fields/integerInput';

describe('parseIntegerInput', () => {
  it.each([
    ['0', 0n],
    ['42', 42n],
    ['-42', -42n],
    ['+7', 7n],
    ['007', 7n],
    ['  15  ', 15n],
    ['9007199254740991', 9007199254740991n],
    ['9007199254740992', 9007199254740992n],
    ['9007199254740993', 9007199254740993n],
    ['-9007199254740993', -9007199254740993n],
    ['12345678901234567891', 12345678901234567891n],
    [
      '115792089237316195423570985008687907853269984665640564039457584007913129639935',
      2n ** 256n - 1n,
    ],
  ])('turns the digits %s into the exact bigint', (text, expected) => {
    expect(parseIntegerInput(text)).toBe(expected);
  });

  it.each(['', ' ', '\t'])('keeps empty text %j empty', (text) => {
    expect(parseIntegerInput(text)).toBe('');
  });

  it.each([
    ['1.5', 1.5],
    ['-0.25', -0.25],
    ['.5', 0.5],
    ['1e5', 100000],
    ['2E3', 2000],
    ['1.5e3', 1500],
    ['5.', 5],
  ])('keeps the number %s that it was before', (text, expected) => {
    expect(parseIntegerInput(text)).toBe(expected);
  });

  it.each([
    '1e30',
    '9007199254740993e0',
    '1e400',
    '-1e30',
    '12345678901234567891.0',
  ])('refuses %s instead of rounding it', (text) => {
    expect(parseIntegerInput(text)).toBeNaN();
  });

  it.each([
    'abc',
    '12abc',
    '1 2',
    '--5',
    '0x10',
    '1_000',
    '1,000',
    'e5',
    '.',
    '-',
    '+',
  ])('returns NaN for %s', (text) => {
    expect(parseIntegerInput(text)).toBeNaN();
  });
});
