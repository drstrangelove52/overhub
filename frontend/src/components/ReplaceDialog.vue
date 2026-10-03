<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const emit = defineEmits(["close", "started"]);

const source = ref("disk"); // disk | nas
const location = ref("");
const nas = ref({ host: "", user: "", password: "", path: "" });
const showNasPassword = ref(false);
const key = ref("");
const snapshots = ref(null);
const chosen = ref("");
const error = ref("");
const busy = ref(false);

onMounted(async () => {
  try {
    location.value = (await api("/backup")).usb_path;
  } catch {
    location.value = "/mnt/overhub/backup";
  }
});

// Where the old backup lies, as the API expects it.
function sourceBody() {
  return source.value === "nas" ? { kind: "sftp", ...nas.value } : { kind: "dir", location: location.value };
}

function chooseSource(value) {
  source.value = value;
  snapshots.value = null;
  error.value = "";
}

async function scan() {
  error.value = "";
  busy.value = true;
  snapshots.value = null;
  try {
    snapshots.value = await api("/backup/replace/scan", { method: "POST", body: { ...sourceBody(), key: key.value } });
    chosen.value = snapshots.value[0]?.id || "";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function start() {
  if (!confirm("Daten des alten Geräts übernehmen?\n\nAm Ende wirst du abgemeldet und meldest dich mit dem Benutzer und Passwort des alten OverHub an.")) return;
  error.value = "";
  busy.value = true;
  try {
    const body = { ...sourceBody(), key: key.value, snapshot_id: chosen.value };
    emit("started", (await api("/backup/replace", { method: "POST", body })).job_id);
  } catch (e) {
    error.value = e.message;
    busy.value = false;
  }
}

function when(iso) {
  return new Date(iso).toLocaleString("de-CH", { dateStyle: "medium", timeStyle: "short" });
}
</script>

<template>
  <ModalShell title="Gerät ersetzen" wide @close="emit('close')">
    <div class="space-y-4 text-sm">
      <p class="text-gray-400">
        Übernimmt alles vom Backup eines alten OverHub: Apps mit ihren Daten, Benutzer, Backup-Ziele.
        Gib diesem Gerät in Tailscale am besten denselben Namen wie dem alten (das alte vorher aus dem Tailnet
        entfernen) — dann bleiben alle Adressen und Apps auf dem Homescreen gültig.
      </p>

      <form class="space-y-3" @submit.prevent="scan">
        <div>
          <div class="mb-2 text-xs text-gray-400">Wo liegt das Backup?</div>
          <div class="mb-3 flex gap-2">
            <button type="button" :class="source === 'disk' ? 'btn-primary' : 'btn-secondary'" @click="chooseSource('disk')">Backup-Disk</button>
            <button type="button" :class="source === 'nas' ? 'btn-primary' : 'btn-secondary'" @click="chooseSource('nas')">NAS</button>
          </div>
          <template v-if="source === 'disk'">
            <label class="mb-1 block text-xs text-gray-400">Backup-Disk mit dem Namen OVERHUB einstecken — sie erscheint unter /mnt/overhub/backup</label>
            <input v-model="location" class="input" required />
          </template>
          <div v-else class="grid gap-2 sm:grid-cols-2">
            <input v-model="nas.host" class="input" placeholder="Server, z.B. nas.local" autocomplete="off" required />
            <input v-model="nas.user" class="input" placeholder="Benutzer" autocomplete="off" required />
            <div class="flex items-center gap-2">
              <input v-model="nas.password" :type="showNasPassword ? 'text' : 'password'" class="input" placeholder="Passwort"
                     autocomplete="new-password" required />
              <button type="button" class="shrink-0 text-xs text-gray-400 hover:text-gray-200" @click="showNasPassword = !showNasPassword">
                {{ showNasPassword ? "Verbergen" : "Anzeigen" }}
              </button>
            </div>
            <input v-model="nas.path" class="input" placeholder="Ordner des alten Geräts, z.B. backup/geraetename" autocomplete="off" required />
          </div>
        </div>
        <div>
          <label class="mb-1 block text-xs text-gray-400">Wiederherstellungs-Schlüssel des alten Geräts</label>
          <input v-model="key" type="password" class="input" autocomplete="off" required />
        </div>
        <div class="flex justify-end">
          <button class="btn-secondary" :disabled="busy">Backup suchen</button>
        </div>
      </form>

      <div v-if="snapshots" class="space-y-2">
        <p v-if="!snapshots.length" class="text-yellow-300">Keine Sicherung eines OverHub gefunden.</p>
        <label v-for="s in snapshots" :key="s.id" class="flex items-start gap-2 rounded-lg border border-gray-800 p-3">
          <input v-model="chosen" type="radio" :value="s.id" class="mt-1" />
          <span>
            <span class="font-medium">{{ when(s.time) }}</span>
            <span class="text-xs text-gray-500"> · OverHub {{ s.overhub_version }}</span>
            <span class="block text-gray-400">
              {{ s.apps ? (s.apps.length ? s.apps.map((a) => `${a.id} ${a.version}`).join(", ") : "keine Apps") : "Apps: siehe nach dem Wiederherstellen" }}
            </span>
          </span>
        </label>
        <div v-if="snapshots.length" class="flex justify-end">
          <button class="btn-primary" :disabled="busy || !chosen" @click="start">Gerät ersetzen</button>
        </div>
      </div>

      <p v-if="error" class="text-red-400">{{ error }}</p>
    </div>
  </ModalShell>
</template>
