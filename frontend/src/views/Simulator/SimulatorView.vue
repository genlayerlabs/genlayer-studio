<script setup lang="ts">
import { ref } from 'vue';
import { RouterView } from 'vue-router';
import SimulatorMenu from '@/components/SimulatorMenu.vue';
import NodeLogs from '@/components/Simulator/NodeLogs.vue';
import ContractsPanel from '@/components/Simulator/ContractsPanel.vue';
import { Splitpanes, Pane } from 'splitpanes';
import 'splitpanes/dist/splitpanes.css';
import { useConfig } from '@/hooks';

const { isStudioNetwork } = useConfig();

const showLogsTerminal = ref<boolean>(true);

const handleLogsResize = (event: any) => {
  if (event[1]?.size <= 4) {
    showLogsTerminal.value = false;
  } else {
    showLogsTerminal.value = true;
  }
};
</script>

<template>
  <div class="flex w-full">
    <SimulatorMenu />

    <Splitpanes class="overflow-hidden">
      <Pane min-size="20" size="20" max-size="50">
        <RouterView v-slot="{ Component }">
          <KeepAlive>
            <component :is="Component" />
          </KeepAlive>
        </RouterView>
      </Pane>

      <Pane>
        <Splitpanes
          v-if="isStudioNetwork"
          horizontal
          @resize="handleLogsResize"
        >
          <Pane min-size="20" size="80" max-size="80">
            <ContractsPanel />
          </Pane>
          <Pane min-size="20" :size="20" max-size="80">
            <NodeLogs />
          </Pane>
        </Splitpanes>
        <ContractsPanel v-else />
      </Pane>
    </Splitpanes>
  </div>
</template>

<style>
.splitpanes__pane {
  display: flex;
  position: relative;
}
.splitpanes .splitpanes__pane {
  background-color: transparent;
}
.splitpanes__splitter {
  position: relative;
}
.splitpanes__splitter:before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  transition: opacity 0.15s;
  opacity: 0;
  z-index: 103;
}
.splitpanes__splitter:hover:before {
  opacity: 1;
}
.splitpanes--vertical > .splitpanes__splitter:before {
  left: -3px;
  right: -3px;
  height: 100%;
}
.splitpanes--horizontal > .splitpanes__splitter:before {
  top: -3px;
  bottom: -3px;
  width: 100%;
}

/* Customize colors here */

.splitpanes__splitter,
.splitpanes__splitter:before {
  background-color: #cbd5e1;
}

[data-mode='dark'] .splitpanes__splitter,
[data-mode='dark'] .splitpanes__splitter:before {
  background-color: #3f3f46;
}
</style>
