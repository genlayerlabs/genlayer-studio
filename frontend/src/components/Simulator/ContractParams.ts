import { type CalldataEncodable } from 'genlayer-js/types';
import { parse as calldataParse } from '@/calldata/parser';
import { formatCalldataParseError } from '@/calldata/errors';
import { AnyFieldValue } from '../global/fields/AnyFieldValue';

export interface SingleArgData {
  val: CalldataEncodable | AnyFieldValue;
  key: number | string;
  /** Parameter name, used in error messages */
  name?: string;
}

export interface ArgData {
  args: SingleArgData[];
  kwargs: { [k: string]: SingleArgData };
}

export function unfoldArgsData(args: ArgData): {
  args: CalldataEncodable[];
  kwargs: { [key: string]: CalldataEncodable };
} {
  const unfoldOne = (x: SingleArgData) => {
    if (x.val instanceof AnyFieldValue) {
      try {
        return calldataParse(x.val.value);
      } catch (e) {
        const label = x.name ?? String(x.key);
        throw new Error(
          `Invalid value for parameter "${label}": ${formatCalldataParseError(e)}`,
        );
      }
    }
    return x.val;
  };
  return {
    args: args.args.map(unfoldOne),
    kwargs: Object.fromEntries(
      Object.entries(args.kwargs).map(([k, v]) => [k, unfoldOne(v)]),
    ),
  };
}
