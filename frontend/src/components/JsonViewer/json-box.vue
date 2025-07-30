<script>
import JsonString from './types/json-string.vue';
import JsonUndefined from './types/json-undefined.vue';
import JsonNumber from './types/json-number.vue';
import JsonBoolean from './types/json-boolean.vue';
import JsonObject from './types/json-object.vue';
import JsonArray from './types/json-array.vue';
import JsonFunction from './types/json-function.vue';
import JsonDate from './types/json-date.vue';
import JsonRegexp from './types/json-regexp.vue';
import { h } from 'vue';
export default {
  name: 'JsonBox',
  inject: ['expandDepth', 'keyClick'],
  props: {
    value: {
      type: [Object, Array, String, Number, Boolean, Function, Date],
      default: null,
    },
    keyName: {
      type: String,
      default: '',
    },
    sort: Boolean,
    depth: {
      type: Number,
      default: 0,
    },
    previewMode: Boolean,
  },
  data() {
    return {
      expand: false,
    };
  },
  mounted() {
    // not used because we want to control the expand from the main props
    // this.expand = this.previewMode || (this.depth >= this.expandDepth ? false : true);
  },
  methods: {
    toggle() {
      this.expand = !this.expand;

      try {
        this.$el.dispatchEvent(new Event('resized'));
      } catch (e) {
        // handle IE not supporting Event constructor
        var evt = document.createEvent('Event');
        evt.initEvent('resized', true, false);
        this.$el.dispatchEvent(evt);
      }
    },
  },
  render() {
    let elements = [];
    let dataType;
    // We have to check if it's a complex type or not and assign a type
    // the dataType is used to know which component to render as a child
    if (this.value === null || this.value === undefined) {
      dataType = JsonUndefined;
    } else if (Array.isArray(this.value)) {
      dataType = JsonArray;
    } else if (Object.prototype.toString.call(this.value) === '[object Date]') {
      dataType = JsonDate;
    } else if (typeof this.value === 'object') {
      dataType = JsonObject;
    } else if (typeof this.value === 'number') {
      dataType = JsonNumber;
    } else if (typeof this.value === 'string') {
      dataType = JsonString;
    } else if (typeof this.value === 'boolean') {
      dataType = JsonBoolean;
    } else if (typeof this.value === 'function') {
      dataType = JsonFunction;
    }
    if (this.value?.constructor === RegExp) {
      // console.log("type", this.value.constructor === RegExp);
      // this.value=this.value.toString()
      dataType = JsonRegexp;
    }
    const complex =
      this.keyName &&
      this.value &&
      (Array.isArray(this.value) ||
        (typeof this.value === 'object' &&
          Object.prototype.toString.call(this.value) !== '[object Date]'));

    if (!this.previewMode && complex) {
      elements.push(
        h('span', {
          class: {
            'jv-toggle': true,
            open: !!this.expand,
          },
          onClick: this.toggle,
        }),
      );
    }

    if (this.keyName) {
      elements.push(
        h('span', {
          class: {
            'jv-key': true,
          },
          onClick: () => {
            this.keyClick(this.keyName);
          },
          innerText: `${this.keyName}:`,
        }),
      );
    }

    elements.push(
      h(dataType, {
        class: {
          'jv-push': true,
        },
        jsonValue: this.value,
        keyName: this.keyName,
        sort: this.sort,
        depth: this.depth,
        expand: this.expand,
        previewMode: this.previewMode,
        'onUpdate:expand': (value) => {
          this.expand = value;
        },
      }),
    );

    return h(
      'div',
      {
        class: {
          'jv-node': true,
          'jv-key-node': Boolean(this.keyName) && !complex,
          toggle: !this.previewMode && complex,
        },
      },
      elements,
    );
  },
};
</script>

<style>
.jv-node {
  position: relative;
}
.jv-node:after {
  content: ',';
}
.jv-node:last-of-type:after {
  content: '';
}
.jv-node.toggle {
  margin-left: 13px !important;
}
.jv-node .jv-node {
  margin-left: 25px;
}
</style>
