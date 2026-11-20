import { describe, it, expect } from 'vitest';
import {
  formatCalldataParseError,
  getCalldataParseError,
} from '@/calldata/errors';

describe('calldata parse errors', () => {
  it('returns null for valid calldata', () => {
    expect(getCalldataParseError('123')).toBeNull();
    expect(getCalldataParseError('"abc"')).toBeNull();
    expect(getCalldataParseError('[1, {a: b#ff}]')).toBeNull();
  });

  it('reports unexpected end of input', () => {
    expect(getCalldataParseError('[1,')).toBe('unexpected end of input');
    expect(getCalldataParseError('')).toBe('unexpected end of input');
  });

  it('strips the raw token position and reports the column', () => {
    expect(getCalldataParseError('abc')).toBe(
      'Unable to consume token: abc (at column 1)',
    );
  });

  it('reports the line for multi-line input', () => {
    expect(getCalldataParseError('[\n  1,\n  oops\n]')).toMatch(
      /^Unable to consume token: oops \(at line 3, column 3\)$/,
    );
  });

  it('passes through plain error messages', () => {
    expect(getCalldataParseError('b#0')).toBe('invalid hex length');
    expect(formatCalldataParseError('boom')).toBe('boom');
  });
});
