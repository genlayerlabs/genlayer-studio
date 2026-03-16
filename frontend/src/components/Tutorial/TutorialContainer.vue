<script setup lang="ts">
import { useRouter } from 'vue-router';
import { DEFAULT_OPTIONS, KEYS } from './constants';
import TutorialStep from './TutorialStep.vue';
import { useContractsStore, useTutorialStore, useUIStore } from '@/stores';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useConfig } from '@/hooks';

const contractsStore = useContractsStore();
const tutorialStore = useTutorialStore();
const uiStore = useUIStore();
const router = useRouter();
const { canUpdateValidators } = useConfig();

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const steps = ref([
  {
    target: '#tutorial-welcome',
    header: {
      title: 'Welcome to the GenLayer Studio!',
    },
    content: 'This tutorial will guide you through the basics.',

    onNextStep: async () => {
      await tutorialStore.addAndOpenContract();
    },
  },
  {
    target: `#contract-item-${tutorialStore.mockContractId}`,
    header: {
      title: 'Code Editor',
    },
    content: `Edit your Intelligent Contracts here.<br/><br/>This example contract is preloaded for you.`,
    onNextStep: async () => {
      router.push({ name: 'run-debug', query: { tutorial: 1 } });
      await sleep(200);
    },
  },
  {
    target: '#tutorial-how-to-deploy',
    header: {
      title: 'Deploying Contracts',
    },
    content:
      'Deploy contracts along with constructor inputs from here.<br/><br/>Click “Next” to automatically deploy the contract.',
    placement: 'right',
    onNextStep: async () => {
      await tutorialStore.deployMockContract();
    },
  },
  {
    target: '#tutorial-read-methods',
    header: {
      title: 'Reading contract state',
    },
    content: 'Here you can read the contract state by calling read methods.',
    placement: 'right',
  },
  {
    target: '#tutorial-write-methods',
    header: {
      title: 'Writing to a contract',
    },
    content:
      'Here you can interact with the contract by calling write methods.',
    placement: 'right',
  },
  {
    target: '#tutorial-tx-response',
    header: {
      title: 'Transaction Response',
    },
    content: 'See the results of your transactions in this area.',
    placement: 'right',
  },
  {
    target: '#tutorial-node-output',
    header: {
      title: 'Node Output',
    },
    content:
      'View real-time feedback as your transactions execute and debug any issues.',
    onNextStep: async () => {
      router.push({ name: 'contracts' });
      await sleep(200);
    },
  },
  {
    header: {
      title: 'Switching contracts',
    },
    target: '#tutorial-how-to-change-example',
    content:
      'Switch between different contracts to explore various features and functionalities.',
    placement: 'right',
    onNextStep: async () => {
      await router.push({ name: 'validators' });
    },
  },
  {
    header: {
      title: 'Validators',
    },
    target: '#tutorial-validators',
    content: canUpdateValidators
      ? 'Configure the number of validators and set up their parameters here.'
      : 'Here you can see the validators that are currently running on the network.',
    placement: 'right',
  },
  {
    header: {
      title: 'Congratulations!',
    },
    target: '#tutorial-end',
    content:
      "You've completed the GenLayer Studio tutorial. Feel free to revisit any step or start experimenting with your own contracts. Happy coding!",
  },
]);

const currentStep = ref(-1);
const options = ref(DEFAULT_OPTIONS);
const numberOfSteps = computed(() => steps.value.length);
const isFirst = computed(() => currentStep.value === 0);
const isLast = computed(() => currentStep.value === steps.value.length - 1);
const showTutorial = computed(() => uiStore.showTutorial);

function stop() {
  document.body.classList.remove('v-tour--active');
  currentStep.value = -1;
  tutorialStore.resetTutorialState();
  uiStore.showTutorial = false;
}

function skip() {
  stop();
}

function finish() {
  stop();
}

function isKeyEnabled(key: 'escape' | 'arrowRight' | 'arrowLeft'): boolean {
  if (
    options.value.enabledNavigationKeys &&
    options.value.enabledNavigationKeys[key]
  ) {
    return options.value.enabledNavigationKeys[key];
  }
  return false;
}

function handleKeyup(e: KeyboardEvent) {
  if (options.value.debug) {
    console.log('[Vue Tour] A keyup event occured:', e);
  }
  switch (e.keyCode) {
    case KEYS.ARROW_RIGHT:
      isKeyEnabled('arrowRight') && nextStep();
      break;
    case KEYS.ARROW_LEFT:
      isKeyEnabled('arrowLeft') && previousStep();
      break;
    case KEYS.ESCAPE:
      isKeyEnabled('escape') && stop();
      break;
  }
}
async function start(startStep?: number) {
  router.replace({ name: 'contracts' });
  contractsStore.setCurrentContractId('');
  // Register keyup listeners for this tour
  if (options.value.useKeyboardNavigation) {
    window.addEventListener('keyup', handleKeyup);
  }
  localStorage.setItem('genlayer.tutorial', new Date().getTime().toString());

  // Wait for the DOM to be loaded, then start the tour
  startStep = startStep ? parseInt(`${startStep}`, 10) : 0;
  const step = steps.value[startStep];

  const process = () =>
    new Promise((resolve) => {
      setTimeout(() => {
        currentStep.value = startStep;
        resolve(0);
      }, options.value.startTimeout);
    });

  if ((step as any).before) {
    try {
      await (step as any).before('start');
    } catch (e) {
      return Promise.reject(e);
    }
  }
  await process();

  return Promise.resolve();
}

async function previousStep() {
  const futureStep = currentStep.value - 1;

  const process = () =>
    new Promise((resolve) => {
      const cb = steps.value[futureStep]?.onNextStep;
      if (cb) {
        cb().then(() => {
          currentStep.value = futureStep;
          setTimeout(() => {
            resolve(0);
          }, 300);
        });
      } else {
        currentStep.value = futureStep;
        resolve(0);
      }
    });

  if (futureStep > -1) {
    const step = steps.value[futureStep];
    if ((step as any).before) {
      try {
        await (step as any).before('previous');
      } catch (e) {
        return Promise.reject(e);
      }
    }
    await process();
  }

  return Promise.resolve();
}

async function nextStep() {
  const futureStep = currentStep.value + 1;

  const process = () =>
    new Promise((resolve) => {
      const cb = steps.value[currentStep.value]?.onNextStep;
      if (cb) {
        cb().then(() => {
          currentStep.value = futureStep;
          setTimeout(() => {
            resolve(0);
          }, 300);
        });
      } else {
        currentStep.value = futureStep;
        resolve(0);
      }
    });

  if (futureStep < numberOfSteps.value && currentStep.value !== -1) {
    const step = steps.value[futureStep];
    if ((step as any).before) {
      try {
        await (step as any).before('next');
      } catch (e) {
        return Promise.reject(e);
      }
    }
    await process();
  }

  return Promise.resolve();
}

onMounted(() => {
  if (!localStorage.getItem('genlayer.tutorial')) {
    setTimeout(() => {
      start();
    }, 1000);
  }
});

onBeforeUnmount(() => {
  if (options.value.useKeyboardNavigation) {
    window.removeEventListener('keyup', handleKeyup);
  }
});

watch(showTutorial, () => {
  if (showTutorial.value) {
    start();
  } else {
    stop();
  }
});
</script>

<template>
  <div class="v-tour bg-slate-300 dark:bg-zinc-700">
    <slot
      :current-step="currentStep"
      :steps="steps"
      :previous-step="previousStep"
      :next-step="nextStep"
      :stop="stop"
      :skip="skip"
      :finish="finish"
      :is-first="isFirst"
      :is-last="isLast"
      :labels="options.labels"
      :enabled-buttons="options.enabledButtons"
      :highlight="options.highlight"
      :debug="options.debug"
    >
      <!--Default slot {{ currentStep }}-->
      <TutorialStep
        v-if="steps[currentStep]"
        :step="steps[currentStep]"
        :key="currentStep"
        :previous-step="previousStep"
        :next-step="nextStep"
        :stop="stop"
        :skip="skip"
        :finish="finish"
        :is-first="isFirst"
        :is-last="isLast"
        :labels="options.labels"
        :enabled-buttons="options.enabledButtons"
        :highlight="options.highlight"
        :stop-on-fail="options.stopOnTargetNotFound"
        :debug="options.debug"
        @targetNotFound="$emit('targetNotFound', $event)"
      >
        <!--<div v-if="index === 2" slot="actions">
            <a @click="nextStep">Next step</a>
          </div>-->
      </TutorialStep>
    </slot>
  </div>
</template>

<style>
body.v-tour--active {
  pointer-events: none;
}

.v-tour {
  pointer-events: auto;
}

.v-tour__target--highlighted::before {
  content: '';
  position: absolute;
  width: 100%;
  height: 100%;
  box-shadow: inset 0 0 0 4px #47c5ffd9;
  border-radius: 4px;
  overflow: hidden;
  pointer-events: auto;
  z-index: 9999;
  pointer-events: none; /* Ensure the highlight doesn't interfere with clicks */
}

.v-tour__target--relative {
  position: relative;
}
</style>
