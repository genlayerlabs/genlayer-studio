const PLAIN_INTEGER = /^[-+]?\d+$/;
const DECIMAL_NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/**
 * Turns the text typed into an integer parameter field into the value that is
 * sent to the contract.
 *
 * A JavaScript `number` is a double: integers above `Number.MAX_SAFE_INTEGER`
 * are silently rounded to the nearest double, so digits are turned into a
 * `bigint`, which is exact. Everything else keeps what it produced before:
 *
 * - empty text stays empty
 * - `1.5` and similar are returned as the number they are, which the calldata
 *   encoder refuses
 * - `1e5` is `100000`
 *
 * What can not be turned into an exact integer is returned as `NaN`, so the
 * encoder refuses it instead of the contract receiving a rounded value. That
 * is text that is not a number at all, a number too large for a double
 * (`1e400`), and an integer written in a form that needs a double (`1e30`):
 * enter it as plain digits to get it exactly.
 */
export function parseIntegerInput(text: string): bigint | number | '' {
  const trimmed = text.trim();
  if (trimmed === '') {
    return '';
  }
  if (PLAIN_INTEGER.test(trimmed)) {
    return BigInt(trimmed);
  }
  if (!DECIMAL_NUMBER.test(trimmed)) {
    return Number.NaN;
  }
  const value = Number(trimmed);
  if (
    !Number.isFinite(value) ||
    (Number.isInteger(value) && !Number.isSafeInteger(value))
  ) {
    return Number.NaN;
  }
  return value;
}
