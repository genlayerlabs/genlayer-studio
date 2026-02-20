import './assets/main.css';
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import Notifications from '@kyvg/vue3-notification';
import App from './App.vue';
import router from './router';
import { persistStorePlugin } from '@/plugins';
import { VueSpinnersPlugin } from 'vue3-spinners';
import registerGlobalComponents from '@/components/global/registerGlobalComponents';
import { VueQueryPlugin } from '@tanstack/vue-query';
import FloatingVue from 'floating-vue';
import 'floating-vue/dist/style.css';
import { createPlausible } from 'v-plausible/vue';
import { getRuntimeConfig } from '@/utils/runtimeConfig';
import { initAppKit, wagmiAdapterRef } from '@/hooks/useAppKit';
import { WagmiPlugin } from '@wagmi/vue';

async function bootstrap() {
  const app = createApp(App);
  const pinia = createPinia();

  pinia.use(persistStorePlugin);
  app.use(pinia);

  await initAppKit();

  if (wagmiAdapterRef.value?.wagmiConfig) {
    app.use(WagmiPlugin, { config: wagmiAdapterRef.value.wagmiConfig });
  }

  app.use(VueQueryPlugin);
  app.use(router);
  app.use(FloatingVue, {
    themes: {
      tooltip: {
        distance: 10,
        delay: {
          show: 0,
          hide: 0,
        },
      },
    },
  });
  app.use(Notifications);
  app.use(VueSpinnersPlugin);

  const plausible = createPlausible({
    init: {
      domain: getRuntimeConfig('VITE_PLAUSIBLE_DOMAIN', 'studio.genlayer.com'),
      trackLocalhost: true,
    },
    settings: {
      enableAutoPageviews: true,
    },
  });

  app.use(plausible);

  registerGlobalComponents(app);

  app.mount('#app');
}

bootstrap();
