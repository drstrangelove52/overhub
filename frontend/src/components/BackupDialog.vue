<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const emit = defineEmits(["close", "started", "changed"]);

const data = ref(null);
const error = ref("");
const name = ref("");
const location = ref("");
const key = ref("");
const copied = ref(false);

async function load() {
  try {
    data.value = await api("/backup");
  } catch (e) {
    error.value = e.message;
  }
}
onMounted(load);

function useUsb() {
  name.value = "USB-Disk";
  location.value = data.value.usb_path;
}

async function addTarget() {
  error.value = "";
  try {
    await api("/backup/targets", { method: "POST", body: { name: name.value, location: location.value } });
    name.value = location.value = "";
    await load();
    emit("changed");
    if (!data.value.key_acknowledged) await showKey();
  } catch (e) {
    error.value = e.message;
  }
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
      <p class="text-gray-400">
        Jede Nacht um 03:00 sichert OverHub alle Apps und sich selbst auf die Ziele unten (behalten: 7 tägliche, 4 wöchentliche,
        6 monatliche Stände). Vor jedem Update sichert OverHub zusätzlich auf dem Gerät selbst und setzt ein misslungenes Update
        automatisch zurück.
      </p>

      <section>
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Ziele</h4>
        <p v-if="!data.targets.length" class="mb-2 text-gray-500">Noch kein Ziel eingerichtet.</p>
        <div v-for="t in data.targets" :key="t.id" class="mb-2 flex items-center justify-between gap-2 rounded-lg bg-gray-950 px-3 py-2">
          <div class="min-w-0">
            <div class="font-medium">{{ t.name }}</div>
            <div class="break-all text-xs text-gray-500">{{ t.location }}</div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <span class="rounded-full px-2 py-0.5 text-xs" :class="t.available ? 'bg-green-500/20 text-green-300' : 'bg-yellow-500/20 text-yellow-300'">
              {{ t.available ? "bereit" : "nicht verfügbar" }}
            </span>
            <button class="text-gray-500 hover:text-red-300" title="Ziel entfernen" @click="removeTarget(t)">✕</button>
          </div>
        </div>
        <form class="mt-3 space-y-2" @submit.prevent="addTarget">
          <div class="flex gap-2">
            <input v-model="name" class="input" placeholder="Name, z.B. USB-Disk" required />
            <button type="button" class="btn-secondary shrink-0" @click="useUsb">USB-Disk</button>
          </div>
          <input v-model="location" class="input" placeholder="Ordner auf dem Gerät, z.B. /mnt/overhub/backup" required />
          <p class="text-xs text-gray-500">
            USB-Disk: am Computer in <b>OVERHUB</b> umbenennen (Windows: Rechtsklick → Umbenennen), dann am Gerät einstecken.
            Sie erscheint automatisch unter {{ data.usb_path }}.
          </p>
          <div class="flex justify-end"><button class="btn-primary">Ziel hinzufügen</button></div>
        </form>
      </section>

      <section v-if="data.targets.length">
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Wiederherstellungs-Schlüssel</h4>
        <p class="mb-2 text-gray-400">
          Alle Sicherungen sind damit verschlüsselt. Ohne ihn lässt sich nach einem Defekt des Geräts nichts wiederherstellen.
          Im Passwort-Manager speichern oder ausdrucken.
        </p>
        <div v-if="key" class="space-y-2 rounded-lg border border-orange-700 bg-orange-900/20 p-3">
          <div class="flex items-center gap-2">
            <code class="min-w-0 flex-1 break-all rounded bg-gray-950 px-2 py-1">{{ key }}</code>
            <button class="btn-secondary shrink-0" @click="copyKey">{{ copied ? "Kopiert" : "Kopieren" }}</button>
          </div>
          <button v-if="!data.key_acknowledged" class="btn-primary" @click="ackKey">Ich habe den Schlüssel gespeichert</button>
        </div>
        <button v-else class="btn-secondary" @click="showKey">Schlüssel anzeigen</button>
        <span v-if="data.key_acknowledged && !key" class="ml-2 text-xs text-green-300">gespeichert bestätigt</span>
      </section>

      <section v-if="data.targets.length">
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Letzte Sicherung</h4>
        <div v-for="a in data.apps" :key="a.id" class="flex justify-between gap-2 border-b border-gray-800 py-1">
          <span>{{ a.name }}</span>
          <span :class="data.overdue.includes(a.id) ? 'text-yellow-300' : 'text-gray-400'">{{ when(a.last_success_at) }}</span>
          <span v-if="a.last_error" class="basis-full text-xs text-red-300">{{ a.last_error }}</span>
        </div>
        <div class="mt-3 flex justify-end">
          <button class="btn-primary" :disabled="data.running" @click="runNow">{{ data.running ? "Backup läuft …" : "Jetzt sichern" }}</button>
        </div>
      </section>

      <p v-if="error" class="text-red-400">{{ error }}</p>
    </div>
  </ModalShell>
</template>
