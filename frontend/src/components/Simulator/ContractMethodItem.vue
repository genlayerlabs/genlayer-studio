<script setup lang="ts">
import type { ContractMethod } from 'genlayer-js/types';
import { abi } from 'genlayer-js';
import { TransactionHashVariant } from 'genlayer-js/types';
import { ref } from 'vue';
import { Collapse } from 'vue-collapsed';
import { notify } from '@kyvg/vue3-notification';
import { ChevronDownIcon } from '@heroicons/vue/16/solid';
import { useEventTracking, useContractQueries } from '@/hooks';
import { unfoldArgsData, type ArgData } from './ContractParams';
import ContractParams from './ContractParams.vue';
import type { ExecutionMode, ReadStateMode } from '@/types';

const { callWriteMethod, callReadMethod, simulateWriteMethod, contract } =
  useContractQueries();
const { trackEvent } = useEventTracking();

const props = defineProps<{
  name: string;
  method: ContractMethod;
  methodType: 'read' | 'write';
  executionMode: ExecutionMode;
  consensusMaxRotations?: number;
  simulationMode?: boolean;
  readStateMode?: ReadStateMode;
}>();

const isExpanded = ref(false);
const isCalling = ref(false);
const responseMessage = ref('');
const responseMessageAccepted = ref('');
const responseMessageFinalized = ref('');

const calldataArguments = ref<ArgData>({ args: [], kwargs: {} });

const formatResponseIfNeeded = (response: string): string => {
  if (!response) {
    return '';
  }
  // Check if the string looks like a malformed JSON (starts with { and ends with })
  if (response.startsWith('{') && response.endsWith('}')) {
    try {
      // Try to parse it as-is first
      return JSON.stringify(JSON.parse(response), null, 2);
    } catch {
      // If parsing fails, try to add commas between properties
      const fixedResponse = response.replace(/"\s*"(?=[^:]*:)/g, '","');
      try {
        // Validate the fixed string can be parsed as JSON
        return JSON.stringify(JSON.parse(fixedResponse), null, 2);
      } catch {
        // If still can't parse, return original
        return response;
      }
    }
  }
  // Remove quotes if the response is just a quoted empty string
  if (response === '""') {
    return '';
  }
  return response;
};

const handleCallReadMethod = async () => {
  responseMessage.value = '';
  isCalling.value = true;

  try {
    const variant =
      props.readStateMode === 'FINALIZED'
        ? TransactionHashVariant.LATEST_FINAL
        : TransactionHashVariant.LATEST_NONFINAL;

    const result = await callReadMethod(
      props.name,
      unfoldArgsData(calldataArguments.value),
      variant,
    );

    responseMessage.value =
      result !== undefined
        ? formatResponseIfNeeded(abi.calldata.toString(result))
        : '';

    trackEvent('called_read_method', {
      contract_name: contract.value?.name || '',
      method_name: props.name,
    });
  } catch (error) {
    notify({
      title: 'Error',
      text: (error as Error)?.message || 'Error getting contract state',
      type: 'error',
    });
  } finally {
    isCalling.value = false;
  }
};

const handleCallWriteMethod = async () => {
  isCalling.value = true;

  try {
    if (props.simulationMode) {
      // Simulation mode - call simulateWriteMethod
      responseMessageAccepted.value = '';
      responseMessageFinalized.value = '';

      const result = await simulateWriteMethod({
        method: props.name,
        args: unfoldArgsData({
          args: calldataArguments.value.args,
          kwargs: calldataArguments.value.kwargs,
        }),
      });

      responseMessageAccepted.value = formatResponseIfNeeded(
        typeof result === 'string' ? result : JSON.stringify(result, null, 2),
      );

      notify({
        text: 'Simulation completed',
        type: 'success',
      });

      trackEvent('simulated_write_method', {
        contract_name: contract.value?.name || '',
        method_name: props.name,
      });
    } else {
      // Real transaction mode
      await callWriteMethod({
        method: props.name,
        executionMode: props.executionMode,
        consensusMaxRotations: props.consensusMaxRotations,
        args: unfoldArgsData({
          args: calldataArguments.value.args,
          kwargs: calldataArguments.value.kwargs,
        }),
      });

      notify({
        text: 'Write method called',
        type: 'success',
      });

      trackEvent('called_write_method', {
        contract_name: contract.value?.name || '',
        method_name: props.name,
      });
    }
  } catch (error) {
    notify({
      title: 'Error',
      text: (error as Error)?.message || 'Error getting contract state',
      type: 'error',
    });
  } finally {
    isCalling.value = false;
  }
};
</script>

<template>
  <div
    class="dark:bg-g flex flex-col overflow-hidden rounded-md bg-slate-100 dark:bg-gray-700"
  >
    <button
      class="flex grow flex-row items-center justify-between bg-slate-200 p-2 text-xs hover:bg-slate-300 dark:bg-slate-600 dark:hover:bg-slate-500"
      @click="isExpanded = !isExpanded"
      :data-testid="`expand-method-btn-${name}`"
    >
      <div class="truncate">
        {{ name }}
      </div>

      <ChevronDownIcon
        class="h-4 w-4 opacity-70 transition-all duration-300"
        :class="isExpanded && 'rotate-180'"
      />
    </button>

    <Collapse :when="isExpanded">
      <div class="flex flex-col items-start gap-2 p-2">
        <ContractParams
          :methodBase="props.method"
          @argsChanged="
            (v: ArgData) => {
              calldataArguments = v;
            }
          "
        />

        <div>
          <Btn
            v-if="methodType === 'read'"
            @click="handleCallReadMethod"
            tiny
            :data-testid="`read-method-btn-${name}`"
            :loading="isCalling"
            :disabled="isCalling"
            >{{ isCalling ? 'Calling...' : 'Call Contract' }}</Btn
          >

          <Btn
            v-if="methodType === 'write'"
            @click="handleCallWriteMethod"
            tiny
            :data-testid="`write-method-btn-${name}`"
            :loading="isCalling"
            :disabled="isCalling"
            >{{
              isCalling
                ? simulationMode
                  ? 'Simulating...'
                  : 'Sending...'
                : simulationMode
                  ? 'Simulate'
                  : 'Send Transaction'
            }}</Btn
          >
        </div>

        <!-- Read method response (single response based on mode) -->
        <div
          v-if="methodType === 'read' && responseMessage !== ''"
          class="w-full break-all text-sm"
        >
          <div class="mb-1 text-xs font-medium">
            Response ({{
              readStateMode === 'FINALIZED' ? 'Finalized' : 'Accepted'
            }}):
          </div>
          <div
            :data-testid="`method-response-${name}`"
            class="w-full whitespace-pre-wrap rounded bg-white p-1 font-mono text-xs dark:bg-slate-600"
          >
            {{ responseMessage }}
          </div>
        </div>

        <!-- Write method response (simulation result) -->
        <div
          v-if="methodType === 'write' && responseMessageAccepted !== ''"
          class="w-full break-all text-sm"
        >
          <div class="mb-1 text-xs font-medium">Simulation Result:</div>
          <div
            :data-testid="`method-response-${name}`"
            class="w-full whitespace-pre-wrap rounded bg-white p-1 font-mono text-xs dark:bg-slate-600"
          >
            {{ responseMessageAccepted }}
          </div>
        </div>
      </div>
    </Collapse>
  </div>
</template>
