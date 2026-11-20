import { describe, expect, it } from 'vitest';
import backendSchema from '../../../backend/node/create_nodes/providers_schema.json';
import frontendSchema from '../../src/assets/schemas/providers_schema.json';

function getProviderRule(schema: any, providerName: string) {
  return schema.allOf.find(
    (rule: any) => rule.if?.properties?.provider?.const === providerName,
  );
}

describe('providers schema sync', () => {
  it('keeps the chutesai provider definition aligned between backend and frontend', () => {
    expect(backendSchema.properties.provider.examples).toContain('chutesai');
    expect(frontendSchema.properties.provider.examples).toContain('chutesai');

    const backendRule = getProviderRule(backendSchema, 'chutesai');
    const frontendRule = getProviderRule(frontendSchema, 'chutesai');

    expect(backendRule?.then?.properties?.plugin?.const).toBe(
      frontendRule?.then?.properties?.plugin?.const,
    );
    expect(backendRule?.then?.properties?.model?.enum).toEqual(
      frontendRule?.then?.properties?.model?.enum,
    );
  });

  it('keeps the pearl provider definition aligned between backend and frontend', () => {
    expect(backendSchema.properties.provider.examples).toContain('pearl');
    expect(frontendSchema.properties.provider.examples).toContain('pearl');

    const backendRule = getProviderRule(backendSchema, 'pearl');
    const frontendRule = getProviderRule(frontendSchema, 'pearl');

    expect(backendRule?.then?.properties?.plugin?.const).toBe(
      'openai-compatible',
    );
    expect(frontendRule?.then?.properties?.plugin?.const).toBe(
      'openai-compatible',
    );
  });
});
