import { fileURLToPath } from 'node:url';
import { mergeConfig, defineConfig, configDefaults } from 'vitest/config';
import viteConfig from './vite.config';

export default defineConfig((env) =>
  mergeConfig(
    viteConfig(env),
    defineConfig({
      test: {
        environment: 'jsdom',
        exclude: [...configDefaults.exclude, 'test/e2e/**'],
        root: fileURLToPath(new URL('./', import.meta.url)),
        alias: {
          '@/assets/examples/': new URL('./test/__mocks__/assets/examples/', import.meta.url)
            .pathname,
        },
      },
    }),
  ),
);
