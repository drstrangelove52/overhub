<script setup>
import { computed, ref, watch } from "vue";

// Passphrase twice; emits the value only when both match and it is long enough.
const props = defineProps({ confirm: { type: Boolean, default: true } });
const emit = defineEmits(["update:modelValue"]);
const first = ref("");
const second = ref("");

const problem = computed(() => {
  if (!first.value) return "";
  if (first.value.length < 8) return "Mindestens 8 Zeichen";
  if (props.confirm && second.value && second.value !== first.value) return "Die beiden Eingaben stimmen nicht überein";
  return "";
});
const valid = computed(() => first.value.length >= 8 && (!props.confirm || first.value === second.value));
watch([first, second], () => emit("update:modelValue", valid.value ? first.value : ""));
</script>

<template>
  <div class="space-y-2">
    <input v-model="first" type="password" class="input" placeholder="Passphrase (min. 8 Zeichen)" autocomplete="new-password" />
    <input v-if="confirm" v-model="second" type="password" class="input" placeholder="Passphrase wiederholen" autocomplete="new-password" />
    <p v-if="problem" class="text-xs text-yellow-300">{{ problem }}</p>
  </div>
</template>
