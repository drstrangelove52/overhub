<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";
import InstallDialog from "../components/InstallDialog.vue";
import JobDialog from "../components/JobDialog.vue";
import LogsDialog from "../components/LogsDialog.vue";
import SettingsDialog from "../components/SettingsDialog.vue";
import UninstallDialog from "../components/UninstallDialog.vue";
import BackupDialog from "../components/BackupDialog.vue";
import DataDialog from "../components/DataDialog.vue";
import ReplaceDialog from "../components/ReplaceDialog.vue";
import UsersDialog from "../components/UsersDialog.vue";
import EmergencyDialog from "../components/EmergencyDialog.vue";

defineProps({ user: Object });
defineEmits(["logout"]);

const apps = ref([]);
const system = ref(null);
const error = ref("");
const installing = ref(null);
const jobId = ref(null);
const logsFor = ref(null);
const showSettings = ref(false);
const removing = ref(null);
const showBackup = ref(false);
const dataFor = ref(null);
const showReplace = ref(false);
const showUsers = ref(false);
// By id: the app list reloads every 10 s and the dialog should see fresh state.
const emergencyId = ref(null);
const emergencyApp = computed(() => apps.value.find((a) => a.id === emergencyId.value));
const backupInfo = ref(null);
let timer;

async function load() {
  try {
    [apps.value, system.value, backupInfo.value] = await Promise.all([api("/apps"), api("/system"), api("/backup")]);
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
  if (!ts.state) return ["OverHub kann den Tailscale-Status nicht lesen. Adressen der Apps werden darum nicht angezeigt."];
  if (ts.state !== "Running") list.push(`Tailscale ist nicht verbunden (Status: ${ts.state}).`);
  else if (!ts.cert_domains?.length) list.push("HTTPS-Zertifikate sind im Tailnet nicht aktiviert (Tailscale-Adminkonsole → DNS).");
  if (ts.key_expiry) {
    const days = Math.floor((new Date(ts.key_expiry) - Date.now()) / 86400000);
    if (days < 30)
      list.push(
        `Der Tailscale-Schlüssel dieses Geräts läuft in ${days} Tagen ab. In der Tailscale-Adminkonsole bei diesem Gerät „Disable key expiry“ wählen, sonst ist OverHub danach nicht mehr erreichbar.`,
      );
  }
  const b = backupInfo.value;
  if (b?.targets.length) {
    for (const t of b.targets.filter((t) => !t.available))
      list.push(`Backup-Ziel „${t.name}“ ist nicht verfügbar (${t.location}). USB-Disk eingesteckt?`);
    if (b.overdue.length) {
      const names = b.apps.filter((a) => b.overdue.includes(a.id)).map((a) => a.name);
      list.push(`Kein erfolgreiches Backup in den letzten 2 Tagen: ${names.join(", ")}.`);
    }
    if (!b.key_acknowledged) list.push("Der Wiederherstellungs-Schlüssel für die Backups ist noch nicht gesichert (Backup → Schlüssel anzeigen).");
  }
  const noEmergency = installed.value.filter((a) => a.emergency_login && !a.emergency_login.confirmed_at).map((a) => a.name);
  if (noEmergency.length)
    list.push(`Notfall-Konto noch nicht eingerichtet: ${noEmergency.join(", ")}. Ohne es kommt man nicht in die App, wenn OverHub nicht läuft (App → Notfall-Konto).`);
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
  showReplace.value = false;
  installing.value = null;
  removing.value = null;
  dataFor.value = null;
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
          <span class="hidden text-gray-400 sm:inline">{{ user.username }}</span>
          <button class="btn-secondary" @click="showUsers = true">Benutzer</button>
          <button class="btn-secondary" @click="showBackup = true">Backup</button>
          <button class="btn-secondary" @click="showSettings = true">Einstellungen</button>
          <button class="btn-secondary" @click="$emit('logout')">Abmelden</button>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-4xl space-y-6 px-4 py-6">
      <div v-for="w in warnings" :key="w" class="rounded-lg border border-yellow-700 bg-yellow-900/30 p-3 text-sm text-yellow-200">{{ w }}</div>
      <div v-if="error" class="rounded-lg border border-red-800 bg-red-900/30 p-3 text-sm text-red-300">{{ error }}</div>
      <div v-if="backupInfo && !backupInfo.targets.length && installed.length" class="rounded-lg border border-gray-800 p-3 text-sm text-gray-400">
        Noch kein Backup-Ziel eingerichtet. <button class="text-orange-400 hover:underline" @click="showBackup = true">Jetzt einrichten</button>
      </div>

      <section>
        <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">Installiert</h2>
        <p v-if="!installed.length" class="text-sm text-gray-500">Noch keine App installiert.</p>
        <div v-if="apps.length && !installed.length" class="mt-3 rounded-lg border border-gray-800 p-3 text-sm text-gray-400">
          Ersetzt dieses Gerät ein altes OverHub?
          <button class="text-orange-400 hover:underline" @click="showReplace = true">Daten vom alten Gerät übernehmen</button>
        </div>
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
                  <span v-else class="text-sm text-gray-400">Port {{ app.port }}</span>
                </div>
              </div>
              <div class="flex flex-wrap gap-2">
                <button v-if="app.pending_credentials_job" class="btn bg-yellow-500 text-gray-950 hover:bg-yellow-400" @click="jobId = app.pending_credentials_job">
                  Zugangsdaten anzeigen
                </button>
                <button v-if="app.update_available" class="btn-primary" :disabled="app.busy" @click="action(app, 'update')">
                  Update auf {{ app.catalog_version }}
                </button>
                <button v-if="app.has_backup" class="btn-secondary" :disabled="app.busy" @click="dataFor = app">Daten</button>
                <button class="btn-secondary" @click="logsFor = app">Logs</button>
                <button v-if="app.emergency_login" class="btn-secondary" :disabled="app.busy || !app.healthy" @click="emergencyId = app.id">Notfall-Konto</button>
                <button v-if="app.healthy" class="btn-secondary" :disabled="app.busy" @click="action(app, 'stop')">Stoppen</button>
                <button v-else class="btn-secondary" :disabled="app.busy" @click="action(app, 'start')">Starten</button>
                <button class="btn-secondary text-red-300" :disabled="app.busy" @click="removing = app">Entfernen</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section>
        <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">Katalog</h2>
        <p v-if="apps.length && !available.length" class="text-sm text-gray-500">
          Alle Apps aus dem Katalog sind installiert. Neue Apps kommen mit neuen OverHub-Versionen (Installer auf dem Gerät erneut ausführen).
        </p>
        <div class="grid gap-3 sm:grid-cols-2">
          <div v-for="app in available" :key="app.id" class="card flex flex-col justify-between gap-3 p-4">
            <div class="flex items-start gap-3">
              <img v-if="app.has_icon" :src="`/api/apps/${app.id}/icon`" class="h-10 w-10 rounded-lg" alt="" />
              <div v-else class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gray-800 font-bold text-orange-400">{{ app.name.slice(4, 5) }}</div>
              <div>
                <div class="font-semibold">{{ app.name }} <span class="text-xs font-normal text-gray-500">{{ app.catalog_version }} · Port {{ app.port }}</span></div>
                <p class="text-sm text-gray-400">{{ app.description }}</p>
                <p v-if="app.data_kept" class="mt-1 text-xs text-green-300">Daten einer früheren Installation sind vorhanden und werden übernommen.</p>
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
    <SettingsDialog v-if="showSettings" :system="system" :user="user" @close="showSettings = false" />
    <BackupDialog v-if="showBackup" @close="showBackup = false" @changed="load" @started="(id) => { showBackup = false; jobId = id; }" />
    <EmergencyDialog v-if="emergencyApp" :app="emergencyApp" @close="emergencyId = null" @changed="load" />
    <UsersDialog v-if="showUsers" :apps="apps" :me="user" @close="showUsers = false" />
    <ReplaceDialog v-if="showReplace" @close="showReplace = false" @started="onJobStarted" />
    <DataDialog v-if="dataFor" :app="dataFor" @close="dataFor = null" @started="onJobStarted" />
    <UninstallDialog v-if="removing" :app="removing" @close="removing = null" @started="onJobStarted" />
  </div>
</template>
