<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";
import ModalShell from "../components/ModalShell.vue";

defineProps({ user: Object });
defineEmits(["logout"]);

const apps = ref(null);
const showPassword = ref(false);
const current = ref("");
const next = ref("");
const message = ref("");
const ok = ref(false);

onMounted(async () => {
  apps.value = await api("/my-apps").catch(() => []);
});

async function changePassword() {
  message.value = "";
  try {
    await api("/auth/me/password", { method: "PUT", body: { current_password: current.value, new_password: next.value } });
    ok.value = true;
    message.value = "Passwort geändert.";
    current.value = next.value = "";
  } catch (e) {
    ok.value = false;
    message.value = e.message;
  }
}
</script>

<template>
  <div>
    <header class="border-b border-gray-800 bg-gray-900">
      <div class="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
        <span class="text-xl font-bold text-orange-400">OverHub</span>
        <div class="flex items-center gap-2 text-sm">
          <span class="hidden text-gray-400 sm:inline">{{ user.username }}</span>
          <button class="btn-secondary" @click="showPassword = true">Passwort</button>
          <button class="btn-secondary" @click="$emit('logout')">Abmelden</button>
        </div>
      </div>
    </header>
    <main class="mx-auto max-w-3xl px-4 py-6">
      <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">Meine Apps</h2>
      <p v-if="apps && !apps.length" class="text-sm text-gray-500">Für dich ist noch keine App freigegeben. Frag den Admin.</p>
      <div class="grid gap-3 sm:grid-cols-2">
        <a v-for="app in apps || []" :key="app.id" :href="app.url" class="card flex items-center gap-3 p-4 hover:border-orange-500">
          <img v-if="app.has_icon" :src="`/api/apps/${app.id}/icon`" class="h-10 w-10 rounded-lg" alt="" @error="(e) => (e.target.style.display = 'none')" />
          <div v-else class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gray-800 font-bold text-orange-400">{{ app.name.slice(4, 5) }}</div>
          <div>
            <div class="font-semibold">{{ app.name }}</div>
            <div class="text-sm text-gray-400">{{ app.description }}</div>
          </div>
        </a>
      </div>
    </main>

    <ModalShell v-if="showPassword" title="Passwort ändern" @close="showPassword = false">
      <form class="space-y-3" @submit.prevent="changePassword">
        <input v-model="current" type="password" class="input" placeholder="Aktuelles Passwort" autocomplete="current-password" required />
        <input v-model="next" type="password" class="input" placeholder="Neues Passwort (min. 8 Zeichen)" autocomplete="new-password" minlength="8" required />
        <p v-if="message" class="text-sm" :class="ok ? 'text-green-300' : 'text-red-400'">{{ message }}</p>
        <div class="flex justify-end"><button class="btn-primary">Ändern</button></div>
      </form>
    </ModalShell>
  </div>
</template>
