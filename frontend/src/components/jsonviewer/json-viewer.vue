<template>
  <div :class="jvClass">
    <div v-if="copyable" :class="`jv-tooltip ${copyText.align || 'right'}`">
      <span ref="clip" class="jv-button" :class="{ copied }">
        <slot name="copy" :copied="copied">
          {{ copied ? copyText.copiedText : copyText.copyText }}
        </slot>
      </span>
    </div>
    <div class="jv-code" :class="{ open: expandCode, boxed }">
      <json-box
        ref="jsonBox"
        :value="value"
        :sort="sort"
        :preview-mode="previewMode"
      />
    </div>
    <div
      v-if="expandableCode && boxed"
      class="jv-more"
      @click="toggleExpandCode"
    >
      <span class="jv-toggle" :class="{ open: !!expandCode }" />
    </div>
  </div>
</template>

<script>
import JsonBox from './json-box.vue';
import Clipboard from 'clipboard';
import { debounce } from 'lodash-es';

export default {
  name: 'JsonViewer',
  components: {
    JsonBox,
  },
  props: {
    value: {
      type: [Object, Array, String, Number, Boolean, Function],
      required: true,
    },
    expanded: {
      type: Boolean,
      default: false,
    },
    expandDepth: {
      type: Number,
      default: 1,
    },
    copyable: {
      type: [Boolean, Object],
      default: false,
    },
    sort: {
      type: Boolean,
      default: false,
    },
    boxed: {
      type: Boolean,
      default: false,
    },
    theme: {
      type: String,
      default: 'light',
    },
    timeformat: {
      type: Function,
      default: (value) => value.toLocaleString(),
    },
    previewMode: {
      type: Boolean,
      default: false,
    },
  },
  provide() {
    return {
      expandDepth: this.expandDepth,
      timeformat: this.timeformat,
      keyClick: this.keyClick,
    };
  },
  data() {
    return {
      copied: false,
      expandableCode: false,
      expandCode: false,
    };
  },
  emits: ['onKeyClick'],
  computed: {
    jvClass() {
      return (
        'jv-container ' + 'jv-' + this.theme + (this.boxed ? ' boxed' : '')
      );
    },
    copyText() {
      const { copyText, copiedText, timeout, align } = this.copyable;

      return {
        copyText: copyText || 'copy',
        copiedText: copiedText || 'copied!',
        timeout: timeout || 2000,
        align,
      };
    },
  },
  watch: {
    value() {
      this.onResized();
    },
  },
  mounted: function () {
    this.debounceResized = debounce(this.debResized.bind(this), 200);
    if (this.boxed && this.$refs.jsonBox) {
      this.onResized();
      this.$refs.jsonBox.$el.addEventListener('resized', this.onResized, true);
    }
    if (this.copyable) {
      const clipBoard = new Clipboard(this.$refs.clip, {
        text: () => {
          return JSON.stringify(this.value, null, 2);
        },
      });
      clipBoard.on('success', (e) => {
        this.onCopied(e);
      });
    }
  },
  methods: {
    onResized() {
      this.debounceResized();
    },
    debResized() {
      this.$nextTick(() => {
        if (!this.$refs.jsonBox) return;
        if (this.$refs.jsonBox.$el.clientHeight >= 250) {
          this.expandableCode = true;
        } else {
          this.expandableCode = false;
        }
      });
    },
    keyClick(keyName) {
      this.$emit('onKeyClick', keyName);
    },
    onCopied(copyEvent) {
      if (this.copied) {
        return;
      }
      this.copied = true;
      setTimeout(() => {
        this.copied = false;
      }, this.copyText.timeout);
      this.$emit('copied', copyEvent);
    },
    toggleExpandCode() {
      this.expandCode = !this.expandCode;
    },
  },
};
</script>

<style>
.jv-container {
  box-sizing: border-box;
  position: relative;
  font-size: 12px;
  font-family: Consolas, Menlo, Courier, monospace;
  white-space: nowrap;
}
.jv-container.boxed {
  border: 1px solid #eee;
  border-radius: 6px;
}
.jv-container.boxed:hover {
  box-shadow: 0 2px 7px rgba(0, 0, 0, 0.15);
  border-color: transparent;
  position: relative;
}
.jv-container.jv-light {
  color: #525252;
}
.jv-container.jv-dark {
  color: #fff;
}
.jv-container.jv-light .jv-ellipsis {
  color: #999;
  background-color: #eee;
  border: 1px solid #dbdbdb;
  display: inline-block;
  line-height: 0.9;
  font-size: 0.9em;
  padding: 0px 4px 2px 4px;
  margin: 0 4px;
  border-radius: 3px;
  vertical-align: 2px;
  cursor: pointer;
  user-select: none;
}
.jv-container.jv-dark .jv-ellipsis {
  color: #f8f8f8;
  background-color: #2c3e50;
  display: inline-block;
  line-height: 0.9;
  font-size: 0.9em;
  padding: 0px 4px 2px 4px;
  margin: 0 4px;
  border-radius: 3px;
  vertical-align: 2px;
  cursor: pointer;
  user-select: none;
}
.jv-container.jv-light .jv-button {
  color: #49b3ff;
}
.jv-container.jv-dark .jv-button {
  color: #49b3ff;
}
.jv-container.jv-light .jv-key {
  color: #111111;
  margin-right: 4px;
}
.jv-container.jv-dark .jv-key {
  color: #fff;
  margin-right: 4px;
}

/**dark */
.jv-container.jv-dark .jv-item.jv-array {
  color: #111111;
}
.jv-container.jv-dark .jv-item.jv-array {
  color: #fff;
}
.jv-container.jv-dark .jv-item.jv-boolean {
  color: #fc1e70;
}
.jv-container.jv-dark .jv-item.jv-function {
  color: #067bca;
}
.jv-container.jv-dark .jv-item.jv-number {
  color: #fc1e70;
}
.jv-container.jv-dark .jv-item.jv-object {
  color: #fff;
}
.jv-container.jv-dark .jv-item.jv-undefined {
  color: #e08331;
}
.jv-container.jv-dark .jv-item.jv-string {
  color: #42b983;
  word-break: break-word;
  white-space: normal;
}
.jv-container.jv-dark .jv-item.jv-string .jv-link {
  color: #0366d6;
}
.jv-container.jv-dark .jv-code .jv-toggle:before {
  padding: 0px 2px;
  border-radius: 2px;
}
.jv-container.jv-dark .jv-code .jv-toggle:hover:before {
  background: #eee;
}
/**light */
.jv-container.jv-light .jv-item.jv-array {
  color: #111111;
}
.jv-container.jv-light .jv-item.jv-boolean {
  color: #fc1e70;
}
.jv-container.jv-light .jv-item.jv-function {
  color: #067bca;
}
.jv-container.jv-light .jv-item.jv-number {
  color: #fc1e70;
}
.jv-container.jv-light .jv-item.jv-object {
  color: #111111;
}
.jv-container.jv-light .jv-item.jv-undefined {
  color: #e08331;
}
.jv-container.jv-light .jv-item.jv-string {
  color: #42b983;
  word-break: break-word;
  white-space: normal;
}
.jv-container.jv-light .jv-item.jv-string .jv-link {
  color: #0366d6;
}
.jv-container.jv-light .jv-code .jv-toggle:before {
  padding: 0px 2px;
  border-radius: 2px;
}
.jv-container.jv-light .jv-code .jv-toggle:hover:before {
  background: #eee;
}
.jv-container .jv-code {
  overflow: hidden;
  padding: 30px 20px;
}
.jv-container .jv-code.boxed {
  max-height: 300px;
}
.jv-container .jv-code.open {
  max-height: initial !important;
  overflow: visible;
  overflow-x: auto;
  padding-bottom: 45px;
}
.jv-container .jv-toggle {
  background-image: url(./icon.svg);
  background-repeat: no-repeat;
  background-size: contain;
  background-position: center center;
  cursor: pointer;
  width: 10px;
  height: 10px;
  margin-right: 2px;
  display: inline-block;
  transition: transform 0.1s;
}
.jv-container .jv-toggle.open {
  transform: rotate(90deg);
}
.jv-container .jv-more {
  position: absolute;
  z-index: 1;
  bottom: 0;
  left: 0;
  right: 0;
  height: 40px;
  width: 100%;
  text-align: center;
  cursor: pointer;
}
.jv-container .jv-more .jv-toggle {
  position: relative;
  top: 40%;
  z-index: 2;
  color: #888;
  transition: all 0.1s;
  transform: rotate(90deg);
}
.jv-container .jv-more .jv-toggle.open {
  transform: rotate(-90deg);
}
.jv-container .jv-more:after {
  content: '';
  width: 100%;
  height: 100%;
  position: absolute;
  bottom: 0;
  left: 0;
  z-index: 1;
  background: linear-gradient(
    to bottom,
    rgba(0, 0, 0, 0) 20%,
    rgba(230, 230, 230, 0.3) 100%
  );
  transition: all 0.1s;
}
.jv-container .jv-more:hover .jv-toggle {
  top: 50%;
  color: #111;
}
.jv-container .jv-more:hover:after {
  background: linear-gradient(
    to bottom,
    rgba(0, 0, 0, 0) 20%,
    rgba(230, 230, 230, 0.3) 100%
  );
}
.jv-container .jv-button {
  position: relative;
  cursor: pointer;
  display: inline-block;
  padding: 5px;
  z-index: 5;
}
.jv-container .jv-button.copied {
  opacity: 0.4;
  cursor: default;
}
.jv-container .jv-tooltip {
  position: absolute;
}
.jv-container .jv-tooltip.right {
  right: 15px;
}
.jv-container .jv-tooltip.left {
  left: 15px;
}
.jv-container .j-icon {
  font-size: 12px;
}
</style>
