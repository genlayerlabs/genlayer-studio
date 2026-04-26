import { describe, it, expect } from 'vitest';
import {
  parseGenvmError,
  isRawGenvmError,
  type FriendlyError,
} from '@/utils/genvmErrors';

describe('genvmErrors', () => {
  describe('parseGenvmError', () => {
    it('matches absent_runner_comment pattern', () => {
      const raw =
        '{"message": "invalid_contract absent_runner_comment", "code": -32000}';
      const result: FriendlyError = parseGenvmError(raw);

      expect(result.title).toBe('Missing runner comment');
      expect(result.reason).toContain('missing the required runner comment');
      expect(result.fix).toContain('# { "Depends": "py-genlayer:test" }');
      expect(result.rawError).toBe(raw);
    });

    it('matches invalid_runner_comment pattern', () => {
      const raw = '{"message": "invalid_runner_comment"}';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Invalid runner comment');
    });

    it('matches invalid_contract pattern', () => {
      const raw = 'Error: invalid_contract - syntax error at line 5';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Invalid contract');
    });

    it('matches execution failed pattern', () => {
      const raw = 'execution failed: RuntimeError in __init__';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Contract execution failed');
    });

    it('matches VM_ERROR pattern', () => {
      const raw = '{"kind": "VM_ERROR", "details": "segfault"}';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Virtual Machine error');
    });

    it('matches InsufficientFundsError pattern', () => {
      const raw = 'InsufficientFundsError: account 0x123 has 0 balance';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Insufficient funds');
    });

    it('matches InvalidAddressError pattern', () => {
      const raw = 'InvalidAddressError: 0xZZZ is not valid';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Invalid address');
    });

    it('matches timeout pattern', () => {
      const raw = 'Request timeout after 30s';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Request timed out');
    });

    it('returns generic fallback for unknown errors', () => {
      const raw = 'Some completely unknown error xyz';
      const result = parseGenvmError(raw);
      expect(result.title).toBe('Something went wrong');
      expect(result.rawError).toBe(raw);
    });

    it('handles empty string gracefully', () => {
      const result = parseGenvmError('');
      expect(result.title).toBe('Something went wrong');
    });

    it('preserves the raw error for all cases', () => {
      const raw = 'InsufficientFundsError: account is broke';
      const result = parseGenvmError(raw);
      expect(result.rawError).toBe(raw);
    });
  });

  describe('isRawGenvmError', () => {
    it('returns true for genvm_log messages', () => {
      expect(isRawGenvmError('genvm_log: some log output')).toBe(true);
    });

    it('returns true for VM_ERROR messages', () => {
      expect(isRawGenvmError('{"kind": "VM_ERROR"}')).toBe(true);
    });

    it('returns true for execution failed messages', () => {
      expect(isRawGenvmError('execution failed in contract')).toBe(true);
    });

    it('returns true for absent_runner_comment messages', () => {
      expect(isRawGenvmError('absent_runner_comment error')).toBe(true);
    });

    it('returns false for normal user-friendly messages', () => {
      expect(isRawGenvmError('Contract deployed successfully')).toBe(false);
    });

    it('returns false for empty string', () => {
      expect(isRawGenvmError('')).toBe(false);
    });

    it('returns false for null/undefined', () => {
      expect(isRawGenvmError(null as any)).toBe(false);
      expect(isRawGenvmError(undefined as any)).toBe(false);
    });
  });
});
