<script setup>
import { onMounted, ref } from "vue";
import { api, hooks } from "./api";
import LoginView from "./views/LoginView.vue";
import HomeView from "./views/HomeView.vue";

const user = ref(null);
const ready = ref(false);

hooks.onUnauthorized = () => {
  user.value = null;
};

onMounted(async () => {
  try {
    user.value = await api("/auth/me");
  } catch {
    user.value = null;
  }
  ready.value = true;
});

async function logout() {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  user.value = null;
}
</script>

<template>
  <div class="min-h-screen bg-gray-950 text-gray-100">
    <template v-if="ready">
      <LoginView v-if="!user" @logged-in="(u) => (user = u)" />
      <HomeView v-else :user="user" @logout="logout" />
    </template>
  </div>
</template>
