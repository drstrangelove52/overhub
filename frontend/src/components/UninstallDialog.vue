<script setup>
import { computed, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";
import PassphraseFields from "./PassphraseFields.vue";

const props = defineProps({ app: Object });
const emit = defineEmits(["close", "started"]);

// keep: data stays on the device | export: download a file, then delete | delete: delete everything
const mode = ref("keep");
const passphrase = ref("");
const confirmName = ref("");
const error = ref("");
const busy = ref(false);

const allowed = computed(() => {
  if (mode.value === "export") return !!passphrase.value;
  if (mode.value === "delete") return confirmName.value.trim() === props.app.name;
  return true;
});

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const body = { delete_data: mode.value !== "keep" };
    if (mode.value === "export") body.export_passphrase = passphrase.value;
    emit("started", (await api(`/apps/${props.app.id}/uninstall`, { method: "POST", body })).job_id);
  } catch (e) {
    error.value = e.message;
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell :title="`${app.name} entfernen`" @close="emit('close')">
    <form class="space-y-4 text-sm" @submit.prevent="submit">
      <label class="flex items-start gap-2">
        <input v-model="mode" type="radio" value="keep" class="mt-1" />
        <span>
          <span class="font-medium">Daten behalten</span>
          <span class="block text-gray-400">
            Die App ist danach nicht mehr erreichbar. Daten und Passwörter bleiben auf diesem Gerät; bei einer neuen
            Installation ist alles wieder da.
          </span>
        </span>
      </label>
      <label v-if="app.has_backup" class="flex items-start gap-2">
        <input v-model="mode" type="radio" value="export" class="mt-1" />
        <span>
          <span class="font-medium">Daten exportieren, dann alles löschen</span>
          <span class="block text-gray-400">
            Erstellt zuerst eine verschlüsselte Datei zum Herunterladen (z.B. für ein neues Gerät) und löscht danach alles
            auf diesem Gerät.
          </span>
        </span>
      </label>
      <div v-if="mode === 'export'" class="ml-6">
        <PassphraseFields v-model="passphrase" />
      </div>
      <label class="flex items-start gap-2">
        <input v-model="mode" type="radio" value="delete" class="mt-1" />
        <span>
          <span class="font-medium text-red-300">Alles löschen</span>
          <span class="block text-gray-400">Auch alle Daten der App (z.B. Rezepte, Bilder, Benutzer). Das lässt sich nicht rückgängig machen.</span>
        </span>
      </label>
      <div v-if="mode === 'delete'" class="ml-6">
        <label class="mb-1 block text-xs text-gray-400">Zur Bestätigung „{{ app.name }}“ eintippen</label>
        <input v-model="confirmName" class="input" autocomplete="off" />
      </div>
      <p v-if="error" class="text-red-400">{{ error }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="btn-secondary" @click="emit('close')">Abbrechen</button>
        <button class="btn bg-red-600 text-white hover:bg-red-500" :disabled="busy || !allowed">
          {{ mode === "export" ? "Exportieren und entfernen" : "Entfernen" }}
        </button>
      </div>
    </form>
  </ModalShell>
</template>
