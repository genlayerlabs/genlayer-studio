import { describe, it, expect } from 'vitest';
import { lintDeterminism } from '@/agent/lints/determinismRules';

describe('agent determinism linter', () => {
  it('flags network and time usage', () => {
    const code = 'import time\nimport requests\nx = time.time()\nrequests.get(\"http://x\")\n';
    const d = lintDeterminism(code);
    expect(d.length).toBeGreaterThan(0);
  });
});


