import { parse as calldataParse } from './parser';

/**
 * Turns the raw typescript-parsec error message (which is prefixed with a
 * JSON-encoded token position or `<END-OF-FILE>`) into a readable message.
 */
export function formatCalldataParseError(error: unknown): string {
  const message =
    error instanceof Error ? error.message : String(error ?? 'unknown error');

  if (message.startsWith('<END-OF-FILE>')) {
    return 'unexpected end of input';
  }

  const match = /^(\{[^}]*\}): ([\s\S]*)$/.exec(message);
  if (match) {
    const [, rawPos = '', detail = ''] = match;
    try {
      const pos = JSON.parse(rawPos) as {
        rowBegin?: number;
        columnBegin?: number;
      };
      const where =
        pos.rowBegin && pos.rowBegin > 1
          ? `line ${pos.rowBegin}, column ${pos.columnBegin}`
          : `column ${pos.columnBegin}`;
      return `${detail} (at ${where})`;
    } catch {
      return detail;
    }
  }

  return message;
}

/**
 * Returns a readable error if `value` is not valid calldata text, else null.
 */
export function getCalldataParseError(value: string): string | null {
  try {
    calldataParse(value);
    return null;
  } catch (e) {
    return formatCalldataParseError(e);
  }
}
