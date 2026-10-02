<script setup>
import { ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const emit = defineEmits(["close"]);
const current = ref("");
const next = ref("");
const message = ref("");
const ok = ref(false);

async function submit() {
  message.value = "";
  try {
    await api("/auth/me/password", { method: "PUT", body: { current_password: current.value, new_password: next.value } });
    ok.value = true;
    message.value = "Passwort geändert.";
  } catch (e) {
    message.value = e.message;
  }
}
</script>

<template>
  <ModalShell title="Passwort ändern" @close="emit('close')">
    <form class="space-y-4" @submit.prevent="submit">
      <input v-model="current" type="password" class="input" placeholder="Aktuelles Passwort" autocomplete="current-password" required />
      <input v-model="next" type="password" class="input" placeholder="Neues Passwort (min. 8 Zeichen)" autocomplete="new-password" minlength="8" required />
      <p v-if="message" class="text-sm" :class="ok ? 'text-green-300' : 'text-red-400'">{{ message }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="btn-secondary" @click="emit('close')">Schliessen</button>
        <button class="btn-primary" :disabled="ok">Ändern</button>
      </div>
    </form>
  </ModalShell>
</template>
