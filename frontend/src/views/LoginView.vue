<script setup>
import { ref } from "vue";
import { api } from "../api";

const emit = defineEmits(["logged-in"]);
const username = ref("admin");
const password = ref("");
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    emit("logged-in", await api("/auth/login", { method: "POST", body: { username: username.value, password: password.value } }));
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center p-4">
    <div class="w-full max-w-sm">
      <div class="mb-8 text-center">
        <h1 class="text-3xl font-bold text-orange-400">OverHub</h1>
        <p class="mt-1 text-sm text-gray-500">Over-Apps verwalten</p>
      </div>
      <form class="card space-y-4 p-6" @submit.prevent="submit">
        <div>
          <label class="mb-1 block text-xs text-gray-400">Benutzername</label>
          <input v-model="username" class="input" autocomplete="username" required />
        </div>
        <div>
          <label class="mb-1 block text-xs text-gray-400">Passwort</label>
          <input v-model="password" type="password" class="input" autocomplete="current-password" required />
        </div>
        <p v-if="error" class="text-sm text-red-400">{{ error }}</p>
        <button class="btn-primary w-full py-2" :disabled="busy">Anmelden</button>
      </form>
    </div>
  </div>
</template>
