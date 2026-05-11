/**
 * Translates raw GenVM / RPC errors into plain-English, actionable messages
 * for the Studio UI.
 *
 * Closes #1609 — "[UX] Replace raw GenVM JSON errors in Studio with
 * plain-English messages".
 *
 * The Studio's RPC layer surfaces errors as a single string that frequently
 * embeds a JSON-encoded GenVM result, e.g.
 *
 *   ('execution failed', { 'genvm_log': [...],
 *      'result': '{"kind": "VM_ERROR", "message": "invalid_contract absent_runner_comment"}' })
 *
 * The only signal a developer cares about is the `message` field. This module
 * extracts it (best-effort, defensive) and maps known codes to a friendly
 * `{ title, explanation, fix? }` triple. Unknown codes fall back to a generic
 * friendly message; the original payload is always preserved on `raw` so the
 * UI can offer a "Show technical details" toggle for bug reports.
 */

export interface FriendlyError {
  /** Short, human-readable headline. */
  title: string;
  /** One-paragraph explanation of what went wrong. */
  explanation: string;
  /** Optional code snippet or instruction the developer should apply. */
  fix?: string;
  /** The original error payload, preserved for the technical-details panel. */
  raw: string;
  /** The extracted GenVM message ("invalid_contract absent_runner_comment", etc.) when found. */
  code?: string;
  /** True when we matched a known error code, false on the generic fallback. */
  recognized: boolean;
}

const GENERIC_FALLBACK: Omit<FriendlyError, 'raw'> = {
  title: 'Something went wrong',
  explanation:
    "We couldn't process this request. Expand Technical Details below to see the raw error and share it when reporting a bug.",
  recognized: false,
};

/**
 * Best-effort extraction of the inner GenVM `message` field from a raw error
 * string. Tries, in order:
 *   1. Direct JSON parse (when the whole error body is JSON).
 *   2. Regex match on `"message": "<value>"` (single or double quotes,
 *      handles the Python repr() form the backend currently emits).
 *   3. Regex match on `'message': '<value>'`.
 * Returns the extracted message, or `undefined` if nothing matched.
 */
export function extractGenVMMessage(raw: string): string | undefined {
  if (!raw) return undefined;

  // 1. Pure JSON
  try {
    const parsed = JSON.parse(raw);
    const msg = findMessage(parsed);
    if (msg) return msg;
  } catch {
    /* not JSON — fall through */
  }

  // 2. Embedded JSON inside Python repr — look for a 'result': '<json>' chunk
  //    and try to parse it.
  const resultMatch = raw.match(/['"]result['"]\s*:\s*['"]({[^}]+})['"]/);
  if (resultMatch) {
    try {
      // Unescape doubled quotes that `repr()` produces.
      const inner = resultMatch[1].replace(/\\"/g, '"').replace(/\\'/g, "'");
      const parsed = JSON.parse(inner);
      const msg = findMessage(parsed);
      if (msg) return msg;
    } catch {
      /* fall through */
    }
  }

  // 3. Bare regex on "message": "<value>" or 'message': '<value>'
  const bareMatch =
    raw.match(/["']message["']\s*:\s*["']([^"']+)["']/) ?? undefined;
  if (bareMatch) return bareMatch[1];

  return undefined;
}

function findMessage(obj: unknown): string | undefined {
  if (!obj || typeof obj !== 'object') return undefined;
  const o = obj as Record<string, unknown>;
  if (typeof o.message === 'string') return o.message;
  // Some payloads nest the result one level deeper.
  if (o.result && typeof o.result === 'object') {
    const inner = (o.result as Record<string, unknown>).message;
    if (typeof inner === 'string') return inner;
  }
  return undefined;
}

interface Mapping {
  /** Exact match or RegExp against the extracted GenVM message. */
  match: string | RegExp;
  build: (matched: string) => Omit<FriendlyError, 'raw' | 'recognized'>;
}

const RUNNER_COMMENT_FIX = '# { "Depends": "py-genlayer:test" }';

/**
 * Known GenVM error codes → friendly messages.
 * Order matters: more specific patterns must come before generic ones
 * (e.g. `invalid_contract absent_runner_comment` before bare `invalid_contract`).
 */
const MAPPINGS: Mapping[] = [
  {
    match: /^invalid_contract\s+absent_runner_comment$/,
    build: (msg) => ({
      title: 'Could not load contract',
      explanation:
        'Your contract is missing the runner comment on the very first line. The runner comment tells GenVM which Python runtime to load.',
      fix: `Make sure the very first line of your contract is:\n\n${RUNNER_COMMENT_FIX}\n\nNo blank lines, no shebang, no other content above it.`,
      code: msg,
    }),
  },
  {
    match: /^invalid_contract(?:\s+(.+))?$/,
    build: (msg) => ({
      title: 'Invalid contract',
      explanation:
        "The contract source could not be parsed by GenVM. This usually means the file's header (runner comment / version) is malformed or your code has a syntax error before any function is reachable.",
      fix: `Check that the first line of your contract is a valid runner comment, e.g.:\n\n${RUNNER_COMMENT_FIX}`,
      code: msg,
    }),
  },
  {
    match: 'timeout',
    build: (msg) => ({
      title: 'Execution timed out',
      explanation:
        'The contract took longer to execute than the configured limit. This is often caused by an unbounded loop or by an LLM call that never returns.',
      fix: 'Review any `eq_principle` / LLM calls and add an explicit timeout, or reduce the workload of the entrypoint you ran.',
      code: msg,
    }),
  },
  {
    match: 'OOM',
    build: (msg) => ({
      title: 'Out of memory',
      explanation:
        'The contract exceeded the GenVM memory limit during execution.',
      fix: 'Avoid loading large blobs into memory at once — stream data, or split work across multiple transactions.',
      code: msg,
    }),
  },
  {
    match: 'validator_disagrees',
    build: (msg) => ({
      title: 'Validator disagreement',
      explanation:
        'A validator produced a result that disagreed with the leader. This typically happens when contract logic depends on non-deterministic input that was not declared via an equivalence principle.',
      fix: 'Wrap any non-deterministic operations (LLM calls, web requests, randomness) in `eq_principle.*` so validators can reach consensus.',
      code: msg,
    }),
  },
  {
    match: 'version_too_big',
    build: (msg) => ({
      title: 'Unsupported contract version',
      explanation:
        'The version declared in your contract is newer than what this GenVM build supports.',
      fix: 'Lower the version in the runner comment, or update the Studio image.',
      code: msg,
    }),
  },
  {
    match: /^exit_code(?:\s+.+)?$/,
    build: (msg) => ({
      title: 'Contract exited with an error',
      explanation:
        'GenVM finished running but the contract returned a non-zero exit code. The most common cause is an unhandled Python exception in your contract.',
      fix: 'Check the technical details below for stderr output, then add the missing import or fix the raised exception.',
      code: msg,
    }),
  },
];

/**
 * Convert any error-like value (string, Error, structured payload) into a
 * `FriendlyError`. Always returns something renderable — never throws.
 */
export function parseGenVMError(input: unknown): FriendlyError {
  const raw = stringifyError(input);
  // Prefer an extracted GenVM message; fall back to the trimmed raw string
  // so callers can pass already-bare codes like `"OOM"` directly.
  const code = extractGenVMMessage(raw) ?? raw.trim();

  if (code) {
    for (const m of MAPPINGS) {
      const matched =
        typeof m.match === 'string' ? code === m.match : m.match.test(code);
      if (matched) {
        return {
          ...m.build(code),
          raw,
          recognized: true,
        };
      }
    }
  }

  return {
    ...GENERIC_FALLBACK,
    code: extractGenVMMessage(raw),
    raw,
  };
}

function stringifyError(input: unknown): string {
  if (input == null) return '';
  if (typeof input === 'string') return input;
  if (input instanceof Error) return input.message || String(input);
  if (typeof input === 'object') {
    const o = input as Record<string, unknown>;
    if (typeof o.message === 'string') return o.message;
    try {
      return JSON.stringify(input);
    } catch {
      return String(input);
    }
  }
  return String(input);
}
