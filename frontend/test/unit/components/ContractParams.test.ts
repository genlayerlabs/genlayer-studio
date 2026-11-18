import { describe, it, expect } from 'vitest';
import { unfoldArgsData } from '@/components/Simulator/ContractParams';
import { AnyFieldValue } from '@/components/global/fields/AnyFieldValue';

describe('unfoldArgsData', () => {
  it('parses any-typed values and keeps typed values as is', () => {
    expect(
      unfoldArgsData({
        args: [
          { key: 0, name: 'a', val: new AnyFieldValue('[1, 2]') },
          { key: 1, name: 'b', val: 'plain' },
        ],
        kwargs: { c: { key: 'c', name: 'c', val: new AnyFieldValue('true') } },
      }),
    ).toEqual({ args: [[1n, 2n], 'plain'], kwargs: { c: true } });
  });

  it('names the parameter that failed to encode', () => {
    expect(() =>
      unfoldArgsData({
        args: [
          { key: 0, name: 'amount', val: new AnyFieldValue('1') },
          { key: 1, name: 'recipient', val: new AnyFieldValue('not valid') },
        ],
        kwargs: {},
      }),
    ).toThrow(
      'Invalid value for parameter "recipient": Unable to consume token: not (at column 1)',
    );
  });

  it('falls back to the key when no name is known', () => {
    expect(() =>
      unfoldArgsData({
        args: [{ key: 0, val: new AnyFieldValue('') }],
        kwargs: {},
      }),
    ).toThrow('Invalid value for parameter "0": unexpected end of input');
  });
});
