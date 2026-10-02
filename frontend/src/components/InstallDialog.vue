<script setup>
import { reactive, ref } from "vue";
import { api, upload } from "../api";
import ModalShell from "./ModalShell.vue";
import PassphraseFields from "./PassphraseFields.vue";

const props = defineProps({ app: Object });
const emit = defineEmits(["close", "started"]);

const values = reactive(Object.fromEntries(props.app.settings.map((s) => [s.name, s.default])));
const components = reactive(
  Object.fromEntries(Object.entries(props.app.components).map(([k, c]) => [k, c.default])),
);
const doImport = ref(false);
const file = ref(null);
const passphrase = ref("");
const error = ref("");
const busy = ref(false);
const status = ref("");

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const body = { settings: { ...values }, components: Object.keys(components).filter((k) => components[k]) };
    if (doImport.value) {
      status.value = "Datei wird hochgeladen …";
      body.import_id = (await upload("/imports", file.value)).import_id;
      body.import_passphrase = passphrase.value;
    }
    const { job_id } = await api(`/apps/${props.app.id}/install`, { method: "POST", body });
    emit("started", job_id);
  } catch (e) {
    error.value = e.message;
    busy.value = false;
    status.value = "";
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
      <div v-for="s in app.data_kept || doImport ? [] : app.settings" :key="s.name">
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

      <div v-if="app.has_backup && !app.data_kept" class="rounded-lg border border-gray-800 p-3 text-sm">
        <label class="flex items-start gap-2">
          <input v-model="doImport" type="checkbox" class="mt-1" />
          <span>
            <span class="font-medium">Daten aus einem Export übernehmen</span>
            <span class="block text-gray-400">Eine .overhub-Datei, die mit „Daten → Exportieren“ erstellt wurde, z.B. auf einem anderen Gerät.</span>
          </span>
        </label>
        <div v-if="doImport" class="mt-3 space-y-2">
          <input type="file" accept=".overhub" class="block w-full text-sm text-gray-300 file:mr-3 file:rounded-lg file:border-0 file:bg-gray-700 file:px-3 file:py-1.5 file:text-gray-100"
                 required @change="(e) => (file = e.target.files[0])" />
          <PassphraseFields v-model="passphrase" :confirm="false" />
          <p class="text-xs text-gray-500">Benutzer und Passwörter kommen aus dem Export.</p>
        </div>
      </div>

      <p class="text-xs text-gray-500">Erreichbar danach im Tailnet unter Port {{ app.port }}. Der erste Start kann ein paar Minuten dauern.</p>
      <p v-if="status" class="text-sm text-blue-300">{{ status }}</p>
      <p v-if="error" class="text-sm text-red-400">{{ error }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="btn-secondary" @click="emit('close')">Abbrechen</button>
        <button class="btn-primary" :disabled="busy || (doImport && (!file || !passphrase))">Installieren</button>
      </div>
    </form>
  </ModalShell>
</template>
