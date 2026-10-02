<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";
import InstallDialog from "../components/InstallDialog.vue";
import JobDialog from "../components/JobDialog.vue";
import LogsDialog from "../components/LogsDialog.vue";
import PasswordDialog from "../components/PasswordDialog.vue";

defineProps({ user: Object });
defineEmits(["logout"]);

const apps = ref([]);
const system = ref(null);
const error = ref("");
const installing = ref(null);
const jobId = ref(null);
const logsFor = ref(null);
const showPassword = ref(false);
let timer;

async function load() {
  try {
    [apps.value, system.value] = await Promise.all([api("/apps"), api("/system")]);
    error.value = "";
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(() => {
  load();
  timer = setInterval(load, 10000);
});
onBeforeUnmount(() => clearInterval(timer));

const installed = computed(() => apps.value.filter((a) => a.installed));
const available = computed(() => apps.value.filter((a) => !a.installed));

const warnings = computed(() => {
  const ts = system.value?.tailscale;
  if (!ts) return [];
  const list = [];
  if (ts.state !== "Running") list.push(`Tailscale ist nicht verbunden (Status: ${ts.state || "unbekannt"}).`);
  if (!ts.cert_domains?.length) list.push("HTTPS-Zertifikate sind im Tailnet nicht aktiviert (Tailscale-Adminkonsole → DNS).");
  if (ts.key_expiry) {
    const days = Math.floor((new Date(ts.key_expiry) - Date.now()) / 86400000);
    if (days < 30)
      list.push(
        `Der Tailscale-Schlüssel dieses Geräts läuft in ${days} Tagen ab. In der Tailscale-Adminkonsole bei diesem Gerät „Disable key expiry“ wählen, sonst ist OverHub danach nicht mehr erreichbar.`,
      );
  }
  return list;
});

async function action(app, name) {
  try {
    jobId.value = (await api(`/apps/${app.id}/${name}`, { method: "POST" })).job_id;
  } catch (e) {
    error.value = e.message;
  }
}

function startInstall(app) {
  installing.value = app;
}

function onJobStarted(id) {
  installing.value = null;
  jobId.value = id;
}

function onJobClosed() {
  jobId.value = null;
  load();
}

function statusOf(app) {
  if (app.busy) return { text: "Aktion läuft", cls: "bg-blue-500/20 text-blue-300" };
  if (app.healthy) return { text: "Läuft", cls: "bg-green-500/20 text-green-300" };
  if (!app.services?.length || app.services.every((s) => s.state !== "running"))
    return { text: "Gestoppt", cls: "bg-gray-700 text-gray-300" };
  return { text: "Problem", cls: "bg-red-500/20 text-red-300" };
}
</script>

<template>
  <div>
    <header class="sticky top-0 z-10 border-b border-gray-800 bg-gray-900">
      <div class="mx-auto flex max-w-4xl items-center justify-between px-4 py-3">
        <div>
          <span class="text-xl font-bold text-orange-400">OverHub</span>
          <span v-if="system" class="ml-2 text-xs text-gray-500">{{ system.version }} · {{ system.tailscale.dns_name }}</span>
        </div>
        <div class="flex items-center gap-2 text-sm">
          <button class="text-gray-400 hover:text-gray-200" @click="showPassword = true">{{ user.username }}</button>
          <button class="btn-secondary" @click="$emit('logout')">Abmelden</button>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-4xl space-y-6 px-4 py-6">
      <div v-for="w in warnings" :key="w" class="rounded-lg border border-yellow-700 bg-yellow-900/30 p-3 text-sm text-yellow-200">{{ w }}</div>
      <div v-if="error" class="rounded-lg border border-red-800 bg-red-900/30 p-3 text-sm text-red-300">{{ error }}</div>

      <section>
        <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">Installiert</h2>
        <p v-if="!installed.length" class="text-sm text-gray-500">Noch keine App installiert.</p>
        <div class="space-y-3">
          <div v-for="app in installed" :key="app.id" class="card p-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="flex min-w-0 items-center gap-3">
                <img v-if="app.has_icon" :src="`/api/apps/${app.id}/icon`" class="h-10 w-10 rounded-lg" alt="" />
                <div v-else class="flex h-10 w-10 items-center justify-center rounded-lg bg-gray-800 font-bold text-orange-400">{{ app.name.slice(4, 5) }}</div>
                <div class="min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="font-semibold">{{ app.name }}</span>
                    <span class="text-xs text-gray-500">{{ app.version }}</span>
                    <span class="rounded-full px-2 py-0.5 text-xs" :class="statusOf(app).cls">{{ statusOf(app).text }}</span>
                  </div>
                  <a v-if="app.url" :href="app.url" target="_blank" rel="noopener" class="break-all text-sm text-orange-400 hover:underline">{{ app.url }}</a>
                </div>
              </div>
              <div class="flex flex-wrap gap-2">
                <button v-if="app.update_available" class="btn-primary" :disabled="app.busy" @click="action(app, 'update')">
                  Update auf {{ app.catalog_version }}
                </button>
                <button class="btn-secondary" @click="logsFor = app">Logs</button>
                <button v-if="app.healthy" class="btn-secondary" :disabled="app.busy" @click="action(app, 'stop')">Stoppen</button>
                <button v-else class="btn-secondary" :disabled="app.busy" @click="action(app, 'start')">Starten</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="available.length">
        <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">Katalog</h2>
        <div class="grid gap-3 sm:grid-cols-2">
          <div v-for="app in available" :key="app.id" class="card flex flex-col justify-between gap-3 p-4">
            <div class="flex items-start gap-3">
              <img v-if="app.has_icon" :src="`/api/apps/${app.id}/icon`" class="h-10 w-10 rounded-lg" alt="" />
              <div v-else class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gray-800 font-bold text-orange-400">{{ app.name.slice(4, 5) }}</div>
              <div>
                <div class="font-semibold">{{ app.name }} <span class="text-xs font-normal text-gray-500">{{ app.catalog_version }}</span></div>
                <p class="text-sm text-gray-400">{{ app.description }}</p>
              </div>
            </div>
            <button class="btn-primary self-end" :disabled="app.busy" @click="startInstall(app)">Installieren</button>
          </div>
        </div>
      </section>
    </main>

    <InstallDialog v-if="installing" :app="installing" @close="installing = null" @started="onJobStarted" />
    <JobDialog v-if="jobId" :job-id="jobId" @close="onJobClosed" />
    <LogsDialog v-if="logsFor" :app="logsFor" @close="logsFor = null" />
    <PasswordDialog v-if="showPassword" @close="showPassword = false" />
  </div>
</template>
