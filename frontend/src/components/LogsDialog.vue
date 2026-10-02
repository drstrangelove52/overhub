<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const props = defineProps({ app: Object });
const emit = defineEmits(["close"]);

const service = ref("");
const text = ref("");
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    const query = service.value ? `?service=${encodeURIComponent(service.value)}&tail=300` : "?tail=300";
    text.value = (await api(`/apps/${props.app.id}/logs${query}`)).logs;
  } catch (e) {
    text.value = e.message;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <ModalShell :title="`Logs: ${app.name}`" wide @close="emit('close')">
    <div class="space-y-3">
      <div class="flex gap-2">
        <select v-model="service" class="input max-w-xs" @change="load">
          <option value="">Alle Dienste</option>
          <option v-for="s in app.services" :key="s.service" :value="s.service">{{ s.service }}</option>
        </select>
        <button class="btn-secondary" :disabled="loading" @click="load">Aktualisieren</button>
      </div>
      <pre class="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-gray-950 p-3 text-xs text-gray-300">{{ text }}</pre>
    </div>
  </ModalShell>
</template>
