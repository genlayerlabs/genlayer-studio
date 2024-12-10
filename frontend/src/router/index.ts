import { createRouter, createWebHistory } from 'vue-router';
import SimulatorView from '@/views/Simulator/SimulatorView.vue';
import ContractsView from '@/views/Simulator/ContractsView.vue';
import RunDebugView from '@/views/Simulator/RunDebugView.vue';
import ValidatorsView from '@/views/Simulator/ValidatorsView.vue';
import SettingsView from '@/views/Simulator/SettingsView.vue';

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: { name: 'contracts' },
      component: SimulatorView,
      children: [
        {
          path: 'contracts',
          name: 'contracts',
          component: ContractsView,
          props: true,
        },
        {
          path: 'run-debug',
          name: 'run-debug',
          component: RunDebugView,
          props: true,
        },
        {
          path: 'validators',
          name: 'validators',
          component: ValidatorsView,
          props: true,
        },
        {
          path: 'settings',
          name: 'settings',
          component: SettingsView,
          props: true,
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
});

export default router;
