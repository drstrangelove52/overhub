<script setup>
import { reactive, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const props = defineProps({ app: Object });
const emit = defineEmits(["close", "started"]);

const values = reactive(Object.fromEntries(props.app.settings.map((s) => [s.name, s.default])));
const components = reactive(
  Object.fromEntries(Object.entries(props.app.components).map(([k, c]) => [k, c.default])),
);
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const { job_id } = await api(`/apps/${props.app.id}/install`, {
      method: "POST",
      body: { settings: { ...values }, components: Object.keys(components).filter((k) => components[k]) },
    });
    emit("started", job_id);
  } catch (e) {
    error.value = e.message;
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell :title="`${app.name} ${app.catalog_version} installieren`" @close="emit('close')">
    <form class="space-y-4" @submit.prevent="submit">
      <p class="text-sm text-gray-400">{{ app.description }}</p>
      <p v-if="app.data_kept" class="rounded-lg border border-green-800 bg-green-900/20 p-3 text-sm text-green-300">
        Daten und Zugangsdaten einer früheren Installation sind vorhanden und werden übernommen.
      </p>
      <div v-for="s in app.data_kept ? [] : app.settings" :key="s.name">
        <label class="mb-1 block text-xs text-gray-400">{{ s.label }}</label>
        <input v-model="values[s.name]" class="input" required />
      </div>
      <label v-for="(c, key) in app.components" :key="key" class="flex items-start gap-2 text-sm">
        <input v-model="components[key]" type="checkbox" class="mt-1" />
        <span>
          <span class="font-medium">{{ c.label }}</span>
          <span class="block text-gray-400">{{ c.description }}</span>
        </span>
      </label>
      <p class="text-xs text-gray-500">Erreichbar danach im Tailnet unter Port {{ app.port }}. Der erste Start kann ein paar Minuten dauern.</p>
      <p v-if="error" class="text-sm text-red-400">{{ error }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="btn-secondary" @click="emit('close')">Abbrechen</button>
        <button class="btn-primary" :disabled="busy">Installieren</button>
      </div>
    </form>
  </ModalShell>
</template>
