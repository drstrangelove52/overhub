<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const emit = defineEmits(["close", "started", "changed"]);

const data = ref(null);
const error = ref("");
const showFolderForm = ref(false);
const name = ref("");
const location = ref("");
const key = ref("");
const copied = ref(false);
const showDetails = ref(false);

async function load() {
  try {
    data.value = await api("/backup");
  } catch (e) {
    error.value = e.message;
  }
}
onMounted(load);

const hasUsb = computed(() => data.value?.targets.some((t) => t.location === data.value.usb_path));
const readyTargets = computed(() => data.value?.targets.filter((t) => t.available) || []);

// The oldest "last success" across all apps = when everything was last backed up together.
const lastComplete = computed(() => {
  const times = data.value?.apps.map((a) => a.last_success_at) || [];
  if (!times.length || times.some((t) => !t)) return null;
  return times.reduce((a, b) => (a < b ? a : b));
});

const nextRun = computed(() => {
  const now = new Date();
  return now.getHours() < 3 ? "heute um 03:00" : "morgen um 03:00";
});

async function addTarget(body) {
  error.value = "";
  try {
    await api("/backup/targets", { method: "POST", body });
    showFolderForm.value = false;
    name.value = location.value = "";
    await load();
    emit("changed");
    if (!data.value.key_acknowledged) await showKey();
  } catch (e) {
    error.value = e.message;
  }
}

function addUsb() {
  addTarget({ name: "USB-Disk", location: data.value.usb_path });
}

async function removeTarget(t) {
  if (!confirm(`Ziel „${t.name}“ entfernen? Die Sicherungen auf dem Ziel selbst bleiben erhalten.`)) return;
  await api(`/backup/targets/${t.id}`, { method: "DELETE" });
  await load();
  emit("changed");
}

async function showKey() {
  key.value = (await api("/backup/key")).key;
}

async function copyKey() {
  await navigator.clipboard.writeText(key.value);
  copied.value = true;
  setTimeout(() => (copied.value = false), 1500);
}

async function ackKey() {
  await api("/backup/key/ack", { method: "POST" });
  key.value = "";
  await load();
  emit("changed");
}

async function runNow() {
  error.value = "";
  try {
    emit("started", (await api("/backup/run", { method: "POST" })).job_id);
  } catch (e) {
    error.value = e.message;
  }
}

function when(iso) {
  return iso ? new Date(iso).toLocaleString("de-CH", { dateStyle: "short", timeStyle: "short" }) : "noch nie";
}
</script>

<template>
  <ModalShell title="Backup" wide @close="emit('close')">
    <div v-if="data" class="space-y-6 text-sm">
      <!-- 1. Status -->
      <section class="rounded-lg bg-gray-950 p-4">
        <template v-if="data.targets.length">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="space-y-1">
              <div>
                <span class="text-gray-400">Letzte Sicherung:</span>
                <span :class="data.overdue.length ? 'text-yellow-300' : 'text-green-300'"> {{ when(lastComplete) }}</span>
              </div>
              <div><span class="text-gray-400">Nächste Sicherung:</span> {{ nextRun }}</div>
              <div>
                <span class="text-gray-400">Ziele bereit:</span>
                <span :class="readyTargets.length === data.targets.length ? '' : 'text-yellow-300'">
                  {{ readyTargets.length }} von {{ data.targets.length }}
                </span>
              </div>
            </div>
            <button class="btn-primary px-4 py-2" :disabled="data.running || !readyTargets.length" @click="runNow">
              {{ data.running ? "Backup läuft …" : "Jetzt sichern" }}
            </button>
          </div>
        </template>
        <p v-else class="text-yellow-200">Noch kein Backup-Ziel eingerichtet — deine Daten werden noch nicht gesichert.</p>
      </section>

      <!-- 2. Ziele -->
      <section>
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Wohin wird gesichert?</h4>
        <div v-for="t in data.targets" :key="t.id" class="mb-2 flex items-center justify-between gap-2 rounded-lg border border-gray-800 px-3 py-2">
          <div class="min-w-0">
            <div class="font-medium">{{ t.name }}</div>
            <div class="break-all text-xs text-gray-500">{{ t.location }}</div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <span class="rounded-full px-2 py-0.5 text-xs" :class="t.available ? 'bg-green-500/20 text-green-300' : 'bg-yellow-500/20 text-yellow-300'">
              {{ t.available ? "bereit" : t.location === data.usb_path ? "nicht eingesteckt" : "nicht verfügbar" }}
            </span>
            <button class="text-gray-500 hover:text-red-300" title="Ziel entfernen" @click="removeTarget(t)">✕</button>
          </div>
        </div>

        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <div v-if="!hasUsb" class="rounded-lg border border-gray-800 p-3">
            <div class="mb-1 font-medium">USB-Disk</div>
            <p class="mb-3 text-xs text-gray-400">
              Disk am Computer in <b>OVERHUB</b> umbenennen (Windows: Explorer → Rechtsklick → Umbenennen) und am Gerät
              einstecken. Sie kann danach dauerhaft stecken bleiben.
            </p>
            <button class="btn-primary" @click="addUsb">USB-Disk verwenden</button>
          </div>
          <div class="rounded-lg border border-gray-800 p-3">
            <div class="mb-1 font-medium">Anderer Ordner</div>
            <p v-if="!showFolderForm" class="mb-3 text-xs text-gray-400">Ein Ordner auf dem Gerät, z.B. eine fest eingebaute zweite Disk.</p>
            <button v-if="!showFolderForm" class="btn-secondary" @click="showFolderForm = true">Ordner angeben …</button>
            <form v-else class="space-y-2" @submit.prevent="addTarget({ name, location })">
              <input v-model="name" class="input" placeholder="Name, z.B. Zweite Disk" required />
              <input v-model="location" class="input" placeholder="Pfad, z.B. /srv/backup" required />
              <div class="flex justify-end gap-2">
                <button type="button" class="btn-secondary" @click="showFolderForm = false">Abbrechen</button>
                <button class="btn-primary">Hinzufügen</button>
              </div>
            </form>
          </div>
        </div>
        <p class="mt-2 text-xs text-gray-500">NAS und Cloud als Ziel folgen in einer späteren Version.</p>
      </section>

      <!-- 3. Schlüssel -->
      <section v-if="data.targets.length">
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Wiederherstellungs-Schlüssel
          <span v-if="data.key_acknowledged" class="ml-2 normal-case text-green-300">✓ gespeichert</span>
          <span v-else class="ml-2 normal-case text-yellow-300">noch nicht gesichert</span>
        </h4>
        <p class="mb-2 text-gray-400">
          Alle Sicherungen sind damit verschlüsselt. Geht das Gerät kaputt, braucht es diesen Schlüssel, um die Daten auf einem
          neuen Gerät wiederherzustellen. Im Passwort-Manager speichern oder ausdrucken.
        </p>
        <div v-if="key" class="space-y-2 rounded-lg border border-orange-700 bg-orange-900/20 p-3">
          <div class="flex items-center gap-2">
            <code class="min-w-0 flex-1 break-all rounded bg-gray-950 px-2 py-1">{{ key }}</code>
            <button class="btn-secondary shrink-0" @click="copyKey">{{ copied ? "Kopiert" : "Kopieren" }}</button>
          </div>
          <div class="flex gap-2">
            <button v-if="!data.key_acknowledged" class="btn-primary" @click="ackKey">Ich habe den Schlüssel gespeichert</button>
            <button v-else class="btn-secondary" @click="key = ''">Ausblenden</button>
          </div>
        </div>
        <button v-else class="btn-secondary" @click="showKey">Schlüssel anzeigen</button>
      </section>

      <!-- 4. Details -->
      <section v-if="data.targets.length">
        <button class="text-xs text-gray-400 hover:text-gray-200" @click="showDetails = !showDetails">
          {{ showDetails ? "▾" : "▸" }} Details pro App
        </button>
        <div v-if="showDetails" class="mt-2">
          <div v-for="a in data.apps" :key="a.id" class="flex flex-wrap justify-between gap-2 border-b border-gray-800 py-1">
            <span>{{ a.name }}</span>
            <span :class="data.overdue.includes(a.id) ? 'text-yellow-300' : 'text-gray-400'">{{ when(a.last_success_at) }}</span>
            <span v-if="a.last_error" class="basis-full text-xs text-red-300">{{ a.last_error }}</span>
          </div>
          <p class="mt-2 text-xs text-gray-500">
            Aufbewahrt werden 7 tägliche, 4 wöchentliche und 6 monatliche Stände. Vor jedem Update sichert OverHub zusätzlich
            auf dem Gerät selbst und setzt ein misslungenes Update automatisch zurück.
          </p>
        </div>
      </section>

      <p v-if="error" class="text-red-400">{{ error }}</p>
    </div>
  </ModalShell>
</template>
