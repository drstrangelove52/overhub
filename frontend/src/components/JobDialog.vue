<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const props = defineProps({ jobId: Number });
const emit = defineEmits(["close"]);

const job = ref(null);
const credentials = ref(null);
const logEl = ref(null);
const copied = ref("");
let timer;

const actions = { install: "Installation", update: "Update", start: "Start", stop: "Stopp" };

async function poll() {
  try {
    const data = await api(`/jobs/${props.jobId}`);
    job.value = data;
    // Show-once: the server hands credentials out a single time, keep them here.
    if (data.credentials) credentials.value = data.credentials;
    await nextTick();
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight;
    if (data.status !== "running") clearInterval(timer);
  } catch {
    /* keep polling */
  }
}

onMounted(() => {
  poll();
  timer = setInterval(poll, 1500);
});
onBeforeUnmount(() => clearInterval(timer));

async function copy(key, value) {
  await navigator.clipboard.writeText(value);
  copied.value = key;
  setTimeout(() => (copied.value = ""), 1500);
}

function close() {
  if (credentials.value && !confirm("Die Zugangsdaten werden nicht noch einmal angezeigt. Hast du sie gespeichert?")) return;
  emit("close");
}
</script>

<template>
  <ModalShell :title="job ? `${actions[job.action] || job.action}: ${job.app_id}` : 'Aktion'" wide @close="close">
    <div class="space-y-4">
      <div class="flex items-center gap-2 text-sm">
        <span v-if="!job || job.status === 'running'" class="text-blue-300">Läuft …</span>
        <span v-else-if="job.status === 'success'" class="text-green-300">Erfolgreich abgeschlossen</span>
        <span v-else class="text-red-300">Fehlgeschlagen</span>
      </div>
      <pre ref="logEl" class="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-gray-950 p-3 text-xs text-gray-300">{{ job?.log }}</pre>

      <div v-if="credentials" class="rounded-lg border border-orange-700 bg-orange-900/20 p-3">
        <p class="mb-2 text-sm font-semibold text-orange-300">Zugangsdaten — werden nur jetzt angezeigt</p>
        <p class="mb-3 text-xs text-gray-400">Im Passwort-Manager speichern oder ausdrucken.</p>
        <div v-for="(value, key) in credentials" :key="key" class="mb-2 text-sm">
          <div class="text-xs text-gray-400">{{ key }}</div>
          <div class="flex items-center gap-2">
            <code class="min-w-0 flex-1 break-all rounded bg-gray-950 px-2 py-1">{{ value }}</code>
            <button class="btn-secondary shrink-0" @click="copy(key, value)">{{ copied === key ? "Kopiert" : "Kopieren" }}</button>
          </div>
        </div>
      </div>

      <div class="flex justify-end">
        <button class="btn-secondary" @click="close">Schliessen</button>
      </div>
    </div>
  </ModalShell>
</template>
