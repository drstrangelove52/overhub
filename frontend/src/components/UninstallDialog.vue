<script setup>
import { computed, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const props = defineProps({ app: Object });
const emit = defineEmits(["close", "started"]);

const deleteData = ref(false);
const confirmName = ref("");
const error = ref("");
const busy = ref(false);

const allowed = computed(() => !deleteData.value || confirmName.value.trim() === props.app.name);

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const { job_id } = await api(`/apps/${props.app.id}/uninstall`, {
      method: "POST",
      body: { delete_data: deleteData.value },
    });
    emit("started", job_id);
  } catch (e) {
    error.value = e.message;
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell :title="`${app.name} entfernen`" @close="emit('close')">
    <form class="space-y-4" @submit.prevent="submit">
      <label class="flex items-start gap-2 text-sm">
        <input v-model="deleteData" type="radio" :value="false" class="mt-1" />
        <span>
          <span class="font-medium">Daten behalten</span>
          <span class="block text-gray-400">
            Die App wird gestoppt und ist nicht mehr erreichbar. Daten und Passwörter bleiben auf dem Gerät; bei einer neuen
            Installation ist alles wieder da.
          </span>
        </span>
      </label>
      <label class="flex items-start gap-2 text-sm">
        <input v-model="deleteData" type="radio" :value="true" class="mt-1" />
        <span>
          <span class="font-medium text-red-300">Alles löschen</span>
          <span class="block text-gray-400">Auch alle Daten der App (z.B. Rezepte, Bilder, Benutzer). Das lässt sich nicht rückgängig machen.</span>
        </span>
      </label>
      <div v-if="deleteData">
        <label class="mb-1 block text-xs text-gray-400">Zur Bestätigung „{{ app.name }}“ eintippen</label>
        <input v-model="confirmName" class="input" autocomplete="off" />
      </div>
      <p v-if="error" class="text-sm text-red-400">{{ error }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="btn-secondary" @click="emit('close')">Abbrechen</button>
        <button class="btn bg-red-600 text-white hover:bg-red-500" :disabled="busy || !allowed">Entfernen</button>
      </div>
    </form>
  </ModalShell>
</template>
