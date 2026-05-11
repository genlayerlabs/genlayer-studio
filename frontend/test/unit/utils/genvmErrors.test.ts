import { describe, expect, it } from 'vitest';
import {
  parseGenVMError,
  extractGenVMMessage,
} from '@/utils/genvmErrors';

describe('extractGenVMMessage', () => {
  it('extracts message from a clean JSON payload', () => {
    const raw = JSON.stringify({
      kind: 'VM_ERROR',
      message: 'invalid_contract absent_runner_comment',
    });
    expect(extractGenVMMessage(raw)).toBe(
      'invalid_contract absent_runner_comment',
    );
  });

  it('extracts message from a Python repr-style payload (the current backend format)', () => {
    const raw =
      "('execution failed', { 'stdout': '', 'stderr': '', " +
      "'genvm_log': [{'ts': '1774564096787', 'target': 'genvm_common'}], " +
      "'result': '{\"kind\": \"VM_ERROR\", \"message\": \"invalid_contract absent_runner_comment\"}' })";
    expect(extractGenVMMessage(raw)).toBe(
      'invalid_contract absent_runner_comment',
    );
  });

  it('falls back to a bare regex match on quoted message field', () => {
    const raw = 'execution failed: "message": "timeout" and other noise';
    expect(extractGenVMMessage(raw)).toBe('timeout');
  });

  it('returns undefined when no message field is present', () => {
    expect(extractGenVMMessage('totally unrelated error')).toBeUndefined();
  });

  it('handles empty input safely', () => {
    expect(extractGenVMMessage('')).toBeUndefined();
  });
});

describe('parseGenVMError', () => {
  describe('absent_runner_comment (the main case from issue #1609)', () => {
    const raw =
      "('execution failed', { 'genvm_log': [...], " +
      "'result': '{\"kind\": \"VM_ERROR\", \"message\": \"invalid_contract absent_runner_comment\"}' })";

    it('produces a recognized friendly error', () => {
      const result = parseGenVMError(raw);
      expect(result.recognized).toBe(true);
      expect(result.code).toBe('invalid_contract absent_runner_comment');
      expect(result.title).toBe('Could not load contract');
    });

    it('explains the missing runner comment', () => {
      const result = parseGenVMError(raw);
      expect(result.explanation).toMatch(/runner comment/i);
      expect(result.explanation).toMatch(/first line/i);
    });

    it('includes a fix snippet with the canonical runner comment', () => {
      const result = parseGenVMError(raw);
      expect(result.fix).toContain('# { "Depends": "py-genlayer:test" }');
    });

    it('preserves the raw payload for the technical-details panel', () => {
      const result = parseGenVMError(raw);
      expect(result.raw).toBe(raw);
    });
  });

  describe('other known VmError codes', () => {
    it.each<[string, RegExp]>([
      ['timeout', /timed out/i],
      ['OOM', /memory/i],
      ['validator_disagrees', /validator/i],
      ['version_too_big', /version/i],
    ])('maps %s to a recognized friendly error', (code, pattern) => {
      const raw = JSON.stringify({ kind: 'VM_ERROR', message: code });
      const result = parseGenVMError(raw);
      expect(result.recognized).toBe(true);
      expect(result.code).toBe(code);
      expect(result.title).toMatch(pattern);
    });

    it('matches generic invalid_contract <other>', () => {
      const raw = JSON.stringify({
        kind: 'VM_ERROR',
        message: 'invalid_contract some_unknown_reason',
      });
      const result = parseGenVMError(raw);
      expect(result.recognized).toBe(true);
      expect(result.title).toBe('Invalid contract');
    });

    it('matches exit_code with optional detail', () => {
      const raw = JSON.stringify({ kind: 'VM_ERROR', message: 'exit_code 1' });
      const result = parseGenVMError(raw);
      expect(result.recognized).toBe(true);
      expect(result.title).toMatch(/exited/i);
    });
  });

  describe('absent_runner_comment is matched before generic invalid_contract', () => {
    it('does not fall through to the bare invalid_contract mapping', () => {
      const raw = JSON.stringify({
        kind: 'VM_ERROR',
        message: 'invalid_contract absent_runner_comment',
      });
      const result = parseGenVMError(raw);
      expect(result.title).toBe('Could not load contract');
      // sanity: the generic mapping uses a different title
      expect(result.title).not.toBe('Invalid contract');
    });
  });

  describe('fallback for unknown errors', () => {
    it('returns a generic message but still preserves the raw payload', () => {
      const raw = 'something nobody has ever seen before';
      const result = parseGenVMError(raw);
      expect(result.recognized).toBe(false);
      expect(result.title).toBe('Something went wrong');
      expect(result.raw).toBe(raw);
    });

    it('still extracts and exposes the code when present, even if unrecognized', () => {
      const raw = JSON.stringify({
        kind: 'VM_ERROR',
        message: 'some_brand_new_error',
      });
      const result = parseGenVMError(raw);
      expect(result.recognized).toBe(false);
      expect(result.code).toBe('some_brand_new_error');
    });
  });

  describe('input normalization', () => {
    it('accepts an Error instance', () => {
      const err = new Error(
        JSON.stringify({ kind: 'VM_ERROR', message: 'timeout' }),
      );
      const result = parseGenVMError(err);
      expect(result.recognized).toBe(true);
      expect(result.code).toBe('timeout');
    });

    it('accepts a structured object with a message field', () => {
      const result = parseGenVMError({ message: 'OOM' });
      expect(result.recognized).toBe(true);
      expect(result.code).toBe('OOM');
    });

    it('handles null / undefined without throwing', () => {
      expect(() => parseGenVMError(null)).not.toThrow();
      expect(() => parseGenVMError(undefined)).not.toThrow();
      expect(parseGenVMError(null).recognized).toBe(false);
    });
  });
});
