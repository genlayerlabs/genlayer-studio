/**
 * Decode execution results from base64-encoded bytes.
 *
 * Ported from the studio frontend's calldata/jsonifier.ts so the explorer
 * can show the same human-readable result information.
 */
import { abi } from 'genlayer-js';

// ---------------------------------------------------------------------------
// Result codes (byte 0 of the result payload)
// ---------------------------------------------------------------------------

const RESULT_CODES = new Map<number, string>([
  [0, 'return'],
  [1, 'rollback'],
  [2, 'contract_error'],
  [3, 'error'],
  [4, 'none'],
  [5, 'no_leaders'],
]);

export type DecodedResult = {
  raw: unknown;
  status: string;
  payload?: unknown;
};

// ---------------------------------------------------------------------------
// Base64 helpers
// ---------------------------------------------------------------------------

function normalizeBase64(input: string): string | null {
  let s = input.trim().replace(/[\r\n\s]/g, '');
  if (s.length === 0) return null;
  s = s.replace(/-/g, '+').replace(/_/g, '/');
  const mod = s.length % 4;
  if (mod === 1) return null;
  if (mod === 2) s += '==';
  if (mod === 3) s += '=';
  return s;
}

function decodeBase64(s: string): string {
  if (typeof atob === 'function') return atob(s);
  if (typeof Buffer !== 'undefined')
    return Buffer.from(s, 'base64').toString('binary');
  throw new Error('No base64 decoder available');
}

export function b64ToArray(b64: unknown): Uint8Array {
  if (b64 instanceof Uint8Array) return b64;
  if (typeof b64 !== 'string') return new Uint8Array();
  const normalized = normalizeBase64(b64);
  if (!normalized) return new Uint8Array();
  try {
    const binary = decodeBase64(normalized);
    const out = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
    return out;
  } catch {
    return new Uint8Array();
  }
}

// ---------------------------------------------------------------------------
// Calldata decoder (uses genlayer-js ABI)
// ---------------------------------------------------------------------------

function calldataToUserFriendlyJson(cd: Uint8Array): { raw: number[]; readable: string } {
  return {
    raw: Array.from(cd),
    readable: abi.calldata.toString(abi.calldata.decode(cd)),
  };
}

// ---------------------------------------------------------------------------
// Main decoder
// ---------------------------------------------------------------------------

function looksLikeDecodedResult(x: unknown): x is DecodedResult {
  return (
    typeof x === 'object' &&
    x !== null &&
    'raw' in (x as Record<string, unknown>) &&
    'status' in (x as Record<string, unknown>)
  );
}

/**
 * Decode a raw execution result (base64 string, Uint8Array, or already-decoded
 * object) into a human-readable structure with status code and payload.
 */
export function decodeResult(input: unknown): DecodedResult {
  if (looksLikeDecodedResult(input)) return input;

  let bytes: Uint8Array;
  let rawB64: string | null = null;

  if (input instanceof Uint8Array) {
    bytes = input;
  } else if (typeof input === 'string') {
    const trimmed = input.trim();
    if (
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    ) {
      try {
        const parsed = JSON.parse(trimmed);
        if (looksLikeDecodedResult(parsed)) return parsed;
      } catch {
        // fall through
      }
    }
    rawB64 = normalizeBase64(input);
    bytes = b64ToArray(input);
  } else {
    return { raw: input, status: '<unknown>', payload: null };
  }

  if (bytes.length === 0) {
    return { raw: input, status: '<unknown>', payload: null };
  }

  const codeByte = bytes[0]!;
  const code = RESULT_CODES.get(codeByte);
  const status: string = code ?? '<unknown>';
  let payload: unknown = null;

  if (code !== undefined) {
    if (codeByte === 1 || codeByte === 2) {
      // rollback / contract_error → UTF-8 error message
      payload = new TextDecoder('utf-8').decode(bytes.slice(1));
    } else if (codeByte === 0) {
      // return → decode calldata payload
      try {
        payload = calldataToUserFriendlyJson(bytes.slice(1));
      } catch {
        // Fallback: show raw UTF-8 text
        payload = new TextDecoder('utf-8').decode(bytes.slice(1));
      }
    }
  }

  return { raw: rawB64 ?? input, status, payload };
}

/**
 * Human-readable label for a result status code.
 */
export function resultStatusLabel(status: string): string {
  switch (status) {
    case 'return':
      return 'Return';
    case 'rollback':
      return 'Rollback';
    case 'contract_error':
      return 'Contract Error';
    case 'error':
      return 'Error';
    case 'none':
      return 'None';
    case 'no_leaders':
      return 'No Leaders';
    default:
      return status;
  }
}
