import { describe, expect, it } from 'vitest';
import pkg from '../../package.json';

// The e2e specs import describe/it from `node:test`. Under mocha those tests
// are invisible to the runner, so setup failures end as "0 passing" with exit 0.
// The scripts must use Node's own runner, which exits nonzero on failed or
// cancelled tests.
describe('e2e npm scripts', () => {
  for (const name of ['test:e2e', 'test:e2e:dev'] as const) {
    it(`${name} runs specs with the node:test runner`, () => {
      const script = pkg.scripts[name];
      expect(script).toContain('node --import tsx --test');
      expect(script).not.toContain('mocha');
    });
  }
});
