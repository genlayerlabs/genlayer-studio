<template>
  <div
    v-bind:class="{ 'v-step--sticky': isSticky }"
    class="v-step bg-slate-300 dark:bg-zinc-700"
    :id="'v-step-' + hash"
    :ref="'v-step-' + hash"
    :data-testid="'tutorial-step-' + step.target"
  >
    <slot name="header">
      <div v-if="step.header" class="v-step__header text-sm font-semibold">
        <div v-if="step.header.title" v-html="step.header.title"></div>
      </div>
    </slot>

    <slot name="content">
      <div class="v-step__content text-sm">
        <div v-if="content" v-html="content"></div>
        <div v-else>
          This is a demo step! The id of this step is {{ hash }} and it targets
          {{ step.target }}.
        </div>
      </div>
    </slot>

    <slot name="actions">
      <div
        class="v-step__buttons flex flex-row items-center justify-center gap-2"
      >
        <Btn
          @click="skip"
          tiny
          secondary
          v-if="!isLast && isButtonEnabled('buttonSkip')"
          class="v-step__button v-step__button-skip"
          testId="tutorial-skip-btn"
        >
          {{ labels.buttonSkip }}
        </Btn>
        <!-- <Btn
          @click="previousStep"
          tiny
          secondary
          v-if="!isFirst && isButtonEnabled('buttonPrevious')"
          class="v-step__button v-step__button-previous"
        >
          {{ labels.buttonPrevious }}
        </Btn> -->
        <Btn
          @click="nextStep"
          tiny
          v-if="!isLast && isButtonEnabled('buttonNext')"
          class="v-step__button v-step__button-next"
        >
          {{ labels.buttonNext }}
        </Btn>
        <Btn
          @click="finish"
          tiny
          v-if="isLast && isButtonEnabled('buttonStop')"
          class="v-step__button v-step__button-stop"
        >
          {{ labels.buttonStop }}
        </Btn>
      </div>
    </slot>

    <div
      class="v-step__arrow"
      :class="{ 'v-step__arrow--dark': step.header && step.header.title }"
      data-popper-arrow
    ></div>
  </div>
</template>

<script>
import { createPopper } from '@popperjs/core';
import jump from 'jump.js';
import sum from 'hash-sum';
import { DEFAULT_STEP_OPTIONS, HIGHLIGHT } from './constants';

export default {
  name: 'TutorialStep',
  props: {
    step: {
      type: Object,
    },
    previousStep: {
      type: Function,
    },
    nextStep: {
      type: Function,
    },
    stop: {
      type: Function,
    },
    skip: {
      type: Function,
      default: function () {
        this.stop();
      },
    },
    finish: {
      type: Function,
      default: function () {
        this.stop();
      },
    },
    isFirst: {
      type: Boolean,
    },
    isLast: {
      type: Boolean,
    },
    labels: {
      type: Object,
    },
    enabledButtons: {
      type: Object,
    },
    highlight: {
      type: Boolean,
    },
    stopOnFail: {
      type: Boolean,
    },
    debug: {
      type: Boolean,
    },
  },
  data() {
    return {
      hash: sum(this.step.target),
      targetElement: document.querySelector(this.step.target),
    };
  },
  computed: {
    params() {
      return {
        ...DEFAULT_STEP_OPTIONS,
        ...{ highlight: this.highlight }, // Use global tour highlight setting first
        ...{ enabledButtons: Object.assign({}, this.enabledButtons) },
        ...this.step.params, // Then use local step parameters if defined
        placement: this.step.placement || 'bottom',
      };
    },
    /**
     * A step is considered sticky if it has no target.
     */
    isSticky() {
      return !this.step.target;
    },
    content() {
      if (typeof this.step.content === 'function') {
        return this.step.content();
      }
      return this.step.content;
    },
  },
  methods: {
    createStep() {
      console.log(
        '[Vue Tour] The target element ' +
          this.step.target +
          ' of .v-step[id="' +
          this.hash +
          '"] is:',
        this.targetElement,
      );
      if (this.debug) {
        console.log(
          '[Vue Tour] The target element ' +
            this.step.target +
            ' of .v-step[id="' +
            this.hash +
            '"] is:',
          this.targetElement,
        );
      }

      if (this.isSticky) {
        document.body.appendChild(this.$refs['v-step-' + this.hash]);
      } else {
        if (this.targetElement) {
          this.enableScrolling();
          this.createHighlight();

          createPopper(
            this.targetElement,
            this.$refs['v-step-' + this.hash],
            this.params,
          );
        } else {
          if (this.debug) {
            console.error(
              '[Vue Tour] The target element ' +
                this.step.target +
                ' of .v-step[id="' +
                this.hash +
                '"] does not exist!',
            );
          }
          this.$emit('targetNotFound', this.step);
          if (this.stopOnFail) {
            this.stop();
          }
        }
      }
    },
    enableScrolling() {
      if (this.params.enableScrolling) {
        if (this.step.duration || this.step.offset) {
          const jumpOptions = {
            duration: this.step.duration || 1000,
            offset: this.step.offset || 0,
            callback: undefined,
            a11y: false,
          };

          jump(this.targetElement, jumpOptions);
        } else {
          // Use the native scroll by default if no scroll options has been defined
          this.targetElement?.scrollIntoView();
        }
      }
    },
    isHighlightEnabled() {
      if (this.debug) {
        console.log(
          `[Vue Tour] Highlight is ${this.params.highlight ? 'enabled' : 'disabled'} for .v-step[id="${this.hash}"]`,
        );
      }
      return this.params.highlight;
    },
    createHighlight() {
      if (this.isHighlightEnabled()) {
        document.body.classList.add(HIGHLIGHT.classes.active);
        const transitionValue = window
          .getComputedStyle(this.targetElement)
          .getPropertyValue('transition');

        // Make sure our background doesn't flick on transitions
        if (transitionValue !== 'all 0s ease 0s' && this.targetElement) {
          this.targetElement.style.transition = `${transitionValue}, ${HIGHLIGHT.transition}`;
        }

        this.targetElement?.classList.add(HIGHLIGHT.classes.targetHighlighted);
        // The element must have a position, if it doesn't have one, add a relative position class
        if (!this.targetElement?.style.position) {
          this.targetElement?.classList.add(HIGHLIGHT.classes.targetRelative);
        }
      } else {
        document.body.classList.remove(HIGHLIGHT.classes.active);
      }
    },
    removeHighlight() {
      if (this.isHighlightEnabled()) {
        const target = this.targetElement;
        if (target) {
          const currentTransition = this.targetElement.style.transition;
          this.targetElement.classList.remove(
            HIGHLIGHT.classes.targetHighlighted,
          );
          this.targetElement.classList.remove(HIGHLIGHT.classes.targetRelative);
          // Remove our transition when step is finished.
          if (currentTransition.includes(HIGHLIGHT.transition)) {
            setTimeout(() => {
              target.style.transition = currentTransition.replace(
                `, ${HIGHLIGHT.transition}`,
                '',
              );
            }, 0);
          }
        }
      }
    },
    isButtonEnabled(name) {
      return this.params.enabledButtons[name]
        ? this.params.enabledButtons[name]
        : true;
    },
  },
  mounted() {
    this.createStep();
  },
  unmounted() {
    this.removeHighlight();
  },
};
</script>

<style scoped>
.v-step {
  /* color: #1a3851; */
  /* #ffc107, #35495e */
  max-width: 320px;
  border-radius: 3px;
  box-shadow:
    rgba(0, 0, 0, 0) 0px 0px 0px 0px,
    rgba(0, 0, 0, 0) 0px 0px 0px 0px,
    rgba(0, 0, 0, 0.1) 0px 4px 6px -1px,
    rgba(0, 0, 0, 0.06) 0px 2px 4px -1px;
  padding: 1rem;
  pointer-events: auto;
  text-align: center;
  z-index: 10000;
}

.v-step--sticky .v-step__arrow {
  display: none;
}

.v-step__arrow,
.v-step__arrow::before {
  position: absolute;
  width: 10px;
  height: 10px;
  background: inherit;
}

.v-step__arrow {
  visibility: hidden;
}

.v-step__arrow--dark::before {
}

.v-step__arrow::before {
  visibility: visible;
  content: '';
  transform: rotate(45deg);
  margin-left: -5px;
}

.v-step[data-popper-placement^='top'] > .v-step__arrow {
  bottom: -5px;
}

.v-step[data-popper-placement^='bottom'] > .v-step__arrow {
  top: -5px;
}

.v-step[data-popper-placement^='right'] > .v-step__arrow {
  left: -5px;
}

.v-step[data-popper-placement^='left'] > .v-step__arrow {
  right: -5px;
}

/* Custom */

.v-step__header {
  margin: -1rem -1rem 0.5rem;
  padding: 0.5rem;
  border-top-left-radius: 3px;
  border-top-right-radius: 3px;
}

.v-step__content {
  margin: 0 0 1rem 0;
}
</style>
