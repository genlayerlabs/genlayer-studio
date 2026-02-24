<script setup lang="ts">
import { useContractsStore } from '@/stores';
import { computed } from 'vue';
import HomeContractItem from '@/components/Simulator/HomeContractItem.vue';
import HomeFeatureItem from '@/components/Simulator/HomeFeatureItem.vue';
import { LINKS } from '@/constants/links';
import { DocumentTextIcon } from '@heroicons/vue/24/solid';
import { BrainCircuit, Earth, CodeXml } from 'lucide-vue-next';

const contractStore = useContractsStore();

const storageContract = computed(() => {
  return contractStore.contracts.find(
    (contract) => contract.name === 'storage.py',
  );
});

const wizardOfCoinContract = computed(() => {
  return contractStore.contracts.find(
    (contract) => contract.name === 'wizard_of_coin.py',
  );
});

const llmERC20Contract = computed(() => {
  return contractStore.contracts.find(
    (contract) => contract.name === 'llm_erc20.py',
  );
});

const startingContractId = computed(() => {
  return (
    contractStore.openedFiles[0] ||
    storageContract.value?.id ||
    wizardOfCoinContract.value?.id ||
    llmERC20Contract.value?.id ||
    contractStore.contracts[0]?.id
  );
});

const hasAnySampleContract = computed(() => {
  return (
    storageContract.value ||
    wizardOfCoinContract.value ||
    llmERC20Contract.value
  );
});
</script>

<template>
  <div class="h-full w-full overflow-y-auto @container">
    <div
      class="mx-auto flex h-full max-w-[1200px] flex-col gap-4 p-4 @[1024px]:flex-row @[1024px]:gap-8"
    >
      <div class="grid place-content-center">
        <div
          class="rounded-md bg-slate-50 p-4 @[1024px]:p-8 dark:bg-slate-600 dark:bg-opacity-10"
        >
          <h1 class="mb-4 text-lg font-semibold">
            Welcome to the GenLayer Studio
          </h1>

          <div class="text-sm">
            <p class="my-2">
              GenLayer is a blockchain with AI-powered smart contracts that
              natively connect to the internet and understand both code and
              natural language.
            </p>
            <p class="my-2">
              This Studio is an interactive sandbox for developers to explore
              GenLayer’s Intelligent Contracts. It mirrors the GenLayer
              network’s environment and consensus, allowing you to test ideas
              locally.
            </p>
            <p class="my-2">
              Your feedback and contributions are crucial in shaping GenLayer.
              Together we can create the next generation of AI-powered Smart
              Contracts!
            </p>
          </div>

          <h2 class="my-4 font-semibold">What you can do</h2>

          <div class="grid grid-cols-1 gap-4 @[800px]:grid-cols-3">
            <HomeFeatureItem
              title="Explore Intelligent Contracts"
              :icon="BrainCircuit"
            >
              Leverage LLMs like
              <a href="https://openai.com/research/gpt-4" target="_blank"
                >GPT-4</a
              >
              or
              <a href="https://llama.meta.com/llama3" target="_blank">Llama3</a>
              for natural language understanding and complex decision-making.
            </HomeFeatureItem>

            <HomeFeatureItem title="Access the Internet" :icon="Earth">
              GenLayer enables smart contracts to connect online without
              oracles.
            </HomeFeatureItem>

            <HomeFeatureItem title="Code in Python" :icon="CodeXml">
              Develop using a familiar language without the hassle of memory and
              string management.
            </HomeFeatureItem>
          </div>

          <div class="text-sm">
            <Alert info class="mt-4">
              The Studio currently does not support token transfers,
              contract-to-contract interactions, or gas consumption. These
              features will be added in future updates.
            </Alert>

            <div class="mt-4 flex flex-row items-center gap-2">
              <a :href="LINKS.docs" target="_blank">
                <Btn secondary :icon="DocumentTextIcon">View Docs</Btn>
              </a>
              <Btn
                primary
                v-if="startingContractId"
                :icon="CodeXml"
                @click="contractStore.openFile(startingContractId)"
              >
                Start coding
              </Btn>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="hasAnySampleContract"
        class="grid min-w-[300px] place-content-center gap-2"
      >
        <h2 class="font-semibold">Explore Templates</h2>

        <div
          class="grid grid-cols-1 gap-4 pb-4 @[800px]:grid-cols-3 @[1024px]:grid-cols-1 @[1024px]:pb-0"
        >
          <HomeContractItem
            v-if="storageContract"
            :contract="storageContract"
            title="Simple Storage"
          >
            Cover the basics of Intelligent Contracts. Learn how to store,
            retrieve, and update data.
          </HomeContractItem>

          <HomeContractItem
            v-if="wizardOfCoinContract"
            :contract="wizardOfCoinContract"
            title="Wizard of Coin"
          >
            Test your wit to see and convince the wizard to hand over the coin.
            Learn how Intelligent Contracts can manage interactions and asset
            control.
          </HomeContractItem>

          <HomeContractItem
            v-if="llmERC20Contract"
            :contract="llmERC20Contract"
            title="LLM ERC20"
          >
            Re-create the ERC20 standard and handle ownership of tokens using
            natural language.
          </HomeContractItem>
        </div>
      </div>
    </div>
  </div>
</template>
