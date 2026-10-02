<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";
import PassphraseFields from "./PassphraseFields.vue";

const props = defineProps({ app: Object });
const emit = defineEmits(["close", "started"]);

const tab = ref("export");
const passphrase = ref("");
const snapshots = ref(null);
const error = ref("");
const busy = ref(false);

const reasons = { scheduled: "nächtlich", manual: "von Hand", "pre-update": "vor Update", "pre-restore": "vor Wiederherstellen" };

async function loadSnapshots() {
  try {
    snapshots.value = await api(`/apps/${props.app.id}/snapshots`);
  } catch (e) {
    error.value = e.message;
    snapshots.value = [];
  }
}
onMounted(loadSnapshots);

async function startExport() {
  error.value = "";
  busy.value = true;
  try {
    emit("started", (await api(`/apps/${props.app.id}/export`, { method: "POST", body: { passphrase: passphrase.value } })).job_id);
  } catch (e) {
    error.value = e.message;
    busy.value = false;
  }
}

async function restore(s) {
  if (!confirm(`${props.app.name} auf den Stand vom ${when(s.time)} zurücksetzen?\n\nÄnderungen seither gehen verloren. Der aktuelle Stand wird vorher gesichert und lässt sich danach wieder herstellen.`)) return;
  error.value = "";
  try {
    emit("started", (await api(`/apps/${props.app.id}/restore`, { method: "POST", body: { repo: s.repo, snapshot_id: s.id } })).job_id);
  } catch (e) {
    error.value = e.message;
  }
}

function when(iso) {
  return new Date(iso).toLocaleString("de-CH", { dateStyle: "medium", timeStyle: "short" });
}
</script>

<template>
  <ModalShell :title="`Daten: ${app.name}`" wide @close="emit('close')">
    <div class="space-y-4 text-sm">
      <div class="flex gap-1 border-b border-gray-800">
        <button v-for="[key, label] in [['export', 'Exportieren'], ['restore', 'Wiederherstellen']]" :key="key"
                class="-mb-px border-b-2 px-3 py-2"
                :class="tab === key ? 'border-orange-400 text-orange-300' : 'border-transparent text-gray-400 hover:text-gray-200'"
                @click="tab = key">{{ label }}</button>
      </div>

      <form v-if="tab === 'export'" class="space-y-3" @submit.prevent="startExport">
        <p class="text-gray-400">
          Alle Daten von {{ app.name }} als eine Datei zum Herunterladen, z.B. um sie auf einem anderen Gerät mit OverHub
          zu importieren. Die Datei ist mit der Passphrase verschlüsselt — ohne sie lässt sie sich nicht öffnen.
        </p>
        <PassphraseFields v-model="passphrase" />
        <p v-if="error" class="text-red-400">{{ error }}</p>
        <div class="flex justify-end">
          <button class="btn-primary" :disabled="!passphrase || busy">Export erstellen</button>
        </div>
      </form>

      <div v-else class="space-y-3">
        <p class="text-gray-400">Gesicherte Stände von {{ app.name }}, der neueste zuoberst.</p>
        <p v-if="snapshots === null" class="text-gray-500">Lade …</p>
        <p v-else-if="!snapshots.length" class="text-gray-500">
          Noch keine Sicherung vorhanden. Unter „Backup“ ein Ziel einrichten oder „Jetzt sichern“ wählen.
        </p>
        <div v-for="s in snapshots" :key="s.repo + s.id" class="flex items-center justify-between gap-2 border-b border-gray-800 py-2">
          <div>
            <div>{{ when(s.time) }} <span class="text-xs text-gray-500">· {{ reasons[s.reason] || s.reason }}</span></div>
            <div class="text-xs text-gray-500">{{ s.repo_label }}</div>
          </div>
          <button class="btn-secondary" @click="restore(s)">Wiederherstellen</button>
        </div>
        <p v-if="error" class="text-red-400">{{ error }}</p>
      </div>
    </div>
  </ModalShell>
</template>
