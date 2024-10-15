import './assets/main.css';
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import Notifications from '@kyvg/vue3-notification';
import App from './App.vue';
import router from './router';
import { persistStorePlugin, TransactionsListenerPlugin } from '@/plugins';
import { VueSpinnersPlugin } from 'vue3-spinners';
import registerGlobalComponents from '@/components/global/registerGlobalComponents';
import { VueQueryPlugin } from '@tanstack/vue-query';
import FloatingVue from 'floating-vue';
import 'floating-vue/dist/style.css';
import { createPlausible } from 'v-plausible/vue';

const app = createApp(App);
const pinia = createPinia();

pinia.use(persistStorePlugin);
app.use(pinia);
app.use(VueQueryPlugin);
app.use(router);
app.use(FloatingVue, {
  themes: {
    tooltip: {
      delay: {
        show: 0,
        hide: 0,
      },
    },
  },
});
app.use(Notifications);
app.use(VueSpinnersPlugin);
app.use(TransactionsListenerPlugin, {
  interval: 5000,
});

const plausible = createPlausible({
  init: {
    domain: import.meta.env.VITE_PLAUSIBLE_DOMAIN || 'simulator.genlayer.com',
    trackLocalhost: true,
  },
  settings: {
    enableAutoPageviews: true,
  },
});

app.use(plausible);

registerGlobalComponents(app);

app.mount('#app');
