import { fileURLToPath, URL } from 'node:url';
import svgLoader from 'vite-svg-loader';

import { defineConfig, loadEnv, UserConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import vueJsx from '@vitejs/plugin-vue-jsx';
import VueDevTools from 'vite-plugin-vue-devtools';
import { nodePolyfills } from 'vite-plugin-node-polyfills';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd());
  const config: UserConfig = {
    base: '/',
    envDir: '..',
    plugins: [
      vue({
        template: {
          compilerOptions: {
            isCustomElement: (tag: string) => tag.startsWith('appkit-'),
          },
        },
      }),
      svgLoader(),
      vueJsx(),
      VueDevTools(),
      nodePolyfills({ globals: { Buffer: true } }),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    preview: {
      port: 8080,
      strictPort: true,
      allowedHosts: [
        '.genlayer.com', // match all genlayer.com sub-domains
        '.genlayerlabs.com', // match all genlayerlabs.com sub-domains
        '.genlayer.org', // match all genlayer.org sub-domains
      ],
    },
    server: {
      port: 8080,
      strictPort: true,
      host: true,
      origin: 'http://0.0.0.0:8080',
    },
  };

  return config;
});
