<script setup>
import { onBeforeUnmount, onMounted } from "vue";

defineProps({ title: String, wide: Boolean });
const emit = defineEmits(["close"]);

function onKey(e) {
  if (e.key === "Escape") emit("close");
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <!-- No close on a click beside the dialog: too easy to lose a half-filled form. -->
    <div class="fixed inset-0 z-20 flex items-center justify-center bg-black/70 p-4">
      <div class="card flex max-h-[90vh] w-full flex-col" :class="wide ? 'max-w-3xl' : 'max-w-md'">
        <div class="flex items-center justify-between border-b border-gray-800 px-4 py-3">
          <h3 class="font-semibold">{{ title }}</h3>
          <button class="text-xl leading-none text-gray-500 hover:text-gray-200" @click="emit('close')">×</button>
        </div>
        <div class="overflow-y-auto p-4">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>
