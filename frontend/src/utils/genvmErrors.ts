/**
 * GenVM Error Parser
 *
 * Translates raw GenVM JSON error output into plain-English messages
 * that tell developers exactly what went wrong and how to fix it.
 *
 * Resolves: https://github.com/genlayerlabs/genlayer-studio/issues/1609
 */

export interface FriendlyError {
  /** Short, human-readable title (e.g. "Could not load contract") */
  title: string;
  /** Plain-English explanation of what went wrong */
  reason: string;
  /** Actionable fix suggestion (may contain code snippets) */
  fix: string;
  /** The original raw error string for advanced users / bug reports */
  rawError: string;
}

/**
 * Known GenVM error codes mapped to user-friendly messages.
 *
 * Each key is a substring that may appear in the raw error message.
 * The order matters: more specific patterns should come first.
 */
const ERROR_MAP: Array<{
  pattern: string | RegExp;
  title: string;
  reason: string;
  fix: string;
}> = [
  {
    pattern: 'absent_runner_comment',
    title: 'Missing runner comment',
    reason:
      'Your contract is missing the required runner comment on line 1. GenVM needs this comment to know which runtime to use.',
    fix: 'Add the following as the very first line of your contract (no blank lines above it):\n\n# { "Depends": "py-genlayer:test" }',
  },
  {
    pattern: 'invalid_runner_comment',
    title: 'Invalid runner comment',
    reason:
      'The runner comment on line 1 of your contract is malformed. It must be valid JSON inside a Python comment.',
    fix: 'Replace the first line of your contract with:\n\n# { "Depends": "py-genlayer:test" }',
  },
  {
    pattern: 'invalid_contract',
    title: 'Invalid contract',
    reason:
      'GenVM could not parse your contract. The contract file may have syntax errors or an unsupported structure.',
    fix: 'Check your contract for Python syntax errors. Make sure it starts with the runner comment and contains a valid class that inherits from gl.Contract.',
  },
  {
    pattern: 'execution failed',
    title: 'Contract execution failed',
    reason:
      'The GenVM virtual machine encountered an error while trying to execute your contract code.',
    fix: 'Check the technical details below for the specific error. Common causes include:\n• Missing imports (e.g. import gl)\n• Syntax errors in your Python code\n• Runtime exceptions in constructor or method logic',
  },
  {
    pattern: 'VM_ERROR',
    title: 'Virtual Machine error',
    reason:
      'The GenVM runtime reported an internal error while processing your contract.',
    fix: 'This is usually caused by a problem in your contract code. Check the technical details for the specific VM error message.',
  },
  {
    pattern: 'InsufficientFundsError',
    title: 'Insufficient funds',
    reason:
      'The account you are using does not have enough funds to complete this transaction.',
    fix: 'Fund your account with more tokens before retrying. You can do this from the Accounts panel in the Studio.',
  },
  {
    pattern: 'InvalidAddressError',
    title: 'Invalid address',
    reason:
      'The address provided is not in the correct format.',
    fix: 'Make sure you are using a valid Ethereum-style address (0x followed by 40 hexadecimal characters).',
  },
  {
    pattern: 'contract_not_found',
    title: 'Contract not found',
    reason:
      'No contract was found at the specified address. It may not have been deployed yet, or the address may be incorrect.',
    fix: 'Double-check the contract address. If you just deployed, wait for the transaction to be finalized before interacting.',
  },
  {
    pattern: 'timeout',
    title: 'Request timed out',
    reason:
      'The operation took too long to complete. This can happen when the network is under heavy load or validators are slow to respond.',
    fix: 'Try again in a few moments. If the issue persists, check that your validators are configured correctly and running.',
  },
];

/**
 * Attempts to extract the GenVM error code from a raw error string.
 *
 * Looks for patterns like: "message": "invalid_contract absent_runner_comment"
 * inside the raw JSON dump.
 */
function extractErrorCode(rawError: string): string | null {
  // Try to find the "message" field inside the result JSON
  const messageMatch = rawError.match(
    /"message"\s*:\s*"([^"]+)"/,
  );
  if (messageMatch) {
    return messageMatch[1];
  }

  // Try to find a "kind" field
  const kindMatch = rawError.match(/"kind"\s*:\s*"([^"]+)"/);
  if (kindMatch) {
    return kindMatch[1];
  }

  return null;
}

/**
 * Parse a raw GenVM error message into a user-friendly error object.
 *
 * If the error matches a known pattern, returns a FriendlyError with
 * clear title, reason, and fix. Otherwise returns a generic friendly
 * message with the raw error preserved for debugging.
 *
 * @param rawError - The raw error string from the backend/SDK
 * @returns A FriendlyError object with user-friendly messaging
 */
export function parseGenvmError(rawError: string): FriendlyError {
  const errorCode = extractErrorCode(rawError);

  // Check the error code first, then fall back to pattern matching on full string
  for (const entry of ERROR_MAP) {
    const pattern = entry.pattern;
    const matches =
      typeof pattern === 'string'
        ? (errorCode?.includes(pattern) || rawError.includes(pattern))
        : pattern.test(errorCode || '') || pattern.test(rawError);

    if (matches) {
      return {
        title: entry.title,
        reason: entry.reason,
        fix: entry.fix,
        rawError,
      };
    }
  }

  // Fallback: unknown error
  return {
    title: 'Something went wrong',
    reason:
      'An unexpected error occurred. Expand "Technical Details" below to see the raw error for bug reporting.',
    fix: 'If this keeps happening, please report it on the GenLayer Discord or GitHub with the technical details below.',
    rawError,
  };
}

/**
 * Check if a raw error string looks like it contains a GenVM JSON dump
 * (i.e., it's a raw technical error that should be translated).
 *
 * @param error - The error message to check
 * @returns true if the error looks like a raw GenVM error dump
 */
export function isRawGenvmError(error: string): boolean {
  if (!error || typeof error !== 'string') return false;

  return (
    error.includes('genvm_log') ||
    error.includes('VM_ERROR') ||
    error.includes('execution failed') ||
    error.includes('gen_getContractSchemaForCode') ||
    error.includes('absent_runner_comment') ||
    error.includes('invalid_contract') ||
    (error.includes('Unexpected error in') && error.includes('result'))
  );
}
