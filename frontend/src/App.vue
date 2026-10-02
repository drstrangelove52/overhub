<script setup>
import { onMounted, ref } from "vue";
import { api, hooks } from "./api";
import LoginView from "./views/LoginView.vue";
import HomeView from "./views/HomeView.vue";
import PortalView from "./views/PortalView.vue";

const user = ref(null);
const ready = ref(false);

hooks.onUnauthorized = () => {
  user.value = null;
};

// Apps send users here as /?next=<their URL> (login) or /logout?next=… —
// only follow it back to this device (same host, any port), never elsewhere.
function safeNext() {
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next) return null;
  try {
    const url = new URL(next);
    return url.protocol === window.location.protocol && url.hostname === window.location.hostname ? url.href : null;
  } catch {
    return null;
  }
}

function onLoggedIn(u) {
  const next = safeNext();
  if (next) {
    window.location.href = next;
    return;
  }
  user.value = u;
  load();
}

async function load() {
  try {
    user.value = await api("/auth/me");
  } catch {
    user.value = null;
  }
}

onMounted(async () => {
  if (window.location.pathname === "/logout") {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    window.location.href = safeNext() || "/";
    return;
  }
  await load();
  if (user.value && safeNext()) {
    window.location.href = safeNext();
    return;
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
      <LoginView v-if="!user" @logged-in="onLoggedIn" />
      <HomeView v-else-if="user.is_admin" :user="user" @logout="logout" />
      <PortalView v-else :user="user" @logout="logout" />
    </template>
  </div>
</template>
