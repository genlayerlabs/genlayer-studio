import { abi } from 'genlayer-js';

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

export function calldataToUserFriendlyJson(cd: Uint8Array): any {
  return {
    raw: Array.from(cd),
    readable: abi.calldata.toString(abi.calldata.decode(cd)),
  };
}

const RESULT_CODES = new Map([
  [0, 'return'],
  [1, 'rollback'],
  [2, 'contract_error'],
  [3, 'error'],
  [4, 'none'],
  [5, 'no_leaders'],
]);

function arrayToB64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i++)
    binary += String.fromCharCode(bytes[i]!);
  // btoa throws on non-Latin1; bytes are arbitrary here but safe for btoa
  return encodeBase64(binary);
}

type ResultLike = {
  raw: unknown;
  status: string;
  payload?: unknown;
};

function looksLikeResultLike(x: unknown): x is ResultLike {
  return (
    typeof x === 'object' &&
    x !== null &&
    'raw' in (x as any) &&
    'status' in (x as any)
  );
}

export function resultToUserFriendlyJson(input: unknown): any {
  if (looksLikeResultLike(input)) return input;

  let bytes: Uint8Array;
  let rawB64: string | null = null;

  if (input instanceof Uint8Array) {
    bytes = input;
    rawB64 = arrayToB64(bytes);
  } else if (typeof input === 'string') {
    // If it looks like JSON, try parse and return if already formatted
    const trimmed = input.trim();
    if (
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    ) {
      try {
        const parsed = JSON.parse(trimmed);
        if (looksLikeResultLike(parsed)) return parsed;
      } catch {
        // fall through to treat as base64
      }
    }
    rawB64 = normalizeBase64(input);
    bytes = b64ToArray(input);
  } else {
    return { raw: input, status: '<unknown>', payload: null };
  }

  const codeByte = bytes[0]!;
  const code = RESULT_CODES.get(codeByte);
  const status: string = code ?? '<unknown>';
  let payload: any = null;

  if (code !== undefined) {
    if (codeByte === 1 || codeByte === 2) {
      const text = new TextDecoder('utf-8').decode(bytes.slice(1));
      payload = text;
    } else if (codeByte === 0) {
      payload = calldataToUserFriendlyJson(bytes.slice(1));
    }
  }

  return {
    raw: rawB64 ?? input,
    status,
    payload,
  };
}

function decodeBase64(s: string): string {
  const globalObject = globalThis as { atob?: (value: string) => string };
  if (typeof globalObject?.atob === 'function') return globalObject.atob(s);
  if (typeof Buffer !== 'undefined')
    return Buffer.from(s, 'base64').toString('binary');
  throw new Error('No base64 decoder available in this environment');
}

function encodeBase64(binary: string): string {
  const globalObject = globalThis as { btoa?: (value: string) => string };
  if (typeof globalObject?.btoa === 'function')
    return globalObject.btoa(binary);
  if (typeof Buffer !== 'undefined')
    return Buffer.from(binary, 'binary').toString('base64');
  throw new Error('No base64 encoder available in this environment');
}
