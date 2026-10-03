<script setup>
import { ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

// Local admin of an app for when OverHub is down. OverHub keeps no copy of
// the password, so it is shown once and confirmed like the recovery key.
const props = defineProps({ app: Object });
const emit = defineEmits(["close", "changed"]);

const step = ref("intro"); // intro | show | confirm | done
const account = ref(null); // { username, password }
const confirmChars = ref("");
const copied = ref(false);
const busy = ref(false);
const error = ref("");

async function generate() {
  if (props.app.emergency_login.confirmed_at &&
      !confirm("Das bisherige Notfall-Passwort funktioniert danach nicht mehr. Neues Passwort setzen?")) return;
  error.value = "";
  busy.value = true;
  try {
    account.value = await api(`/apps/${props.app.id}/emergency-login`, { method: "POST" });
    step.value = "show";
    emit("changed");
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function copy() {
  await navigator.clipboard.writeText(account.value.password);
  copied.value = true;
  setTimeout(() => (copied.value = false), 1500);
}

async function ack() {
  error.value = "";
  const typed = confirmChars.value.trim();
  if (typed.length < 6 || !account.value.password.endsWith(typed)) {
    error.value = "Stimmt nicht mit dem Ende des Passworts überein.";
    return;
  }
  try {
    await api(`/apps/${props.app.id}/emergency-login/ack`, { method: "POST" });
    account.value = null;
    step.value = "done";
    emit("changed");
  } catch (e) {
    error.value = e.message;
  }
}

function close() {
  if ((step.value === "show" || step.value === "confirm") &&
      !confirm("Das Passwort ist noch nicht bestätigt und wird nicht mehr angezeigt. Trotzdem schliessen?")) return;
  emit("close");
}
</script>

<template>
  <ModalShell :title="`Notfall-Konto — ${app.name}`" @close="close">
    <div class="space-y-3 text-sm">
      <p class="text-gray-400">
        Normalerweise meldet man sich in {{ app.name }} über OverHub an. Läuft OverHub nicht, geht das nur mit dem lokalen
        Konto <b class="text-gray-200">{{ app.emergency_login.username }}</b> (Admin). OverHub speichert das Passwort nicht:
        in den Passwort-Manager oder ausdrucken.
      </p>

      <template v-if="step === 'intro'">
        <p v-if="app.emergency_login.confirmed_at" class="text-green-300">
          ✓ Eingerichtet am {{ new Date(app.emergency_login.confirmed_at).toLocaleDateString() }}
        </p>
        <p v-else class="text-yellow-300">Noch nicht eingerichtet.</p>
        <button class="btn-primary" :disabled="busy" @click="generate">
          {{ busy ? "Setze Passwort …" : app.emergency_login.confirmed_at ? "Neues Passwort setzen" : "Passwort setzen" }}
        </button>
      </template>

      <div v-else-if="step === 'show'" class="space-y-2 rounded-lg border border-orange-700 bg-orange-900/20 p-3">
        <div>Benutzer: <code class="rounded bg-gray-950 px-2 py-0.5">{{ account.username }}</code></div>
        <div class="flex items-center gap-2">
          <code class="min-w-0 flex-1 break-all rounded bg-gray-950 px-2 py-1">{{ account.password }}</code>
          <button class="btn-secondary shrink-0" @click="copy">{{ copied ? "Kopiert" : "Kopieren" }}</button>
        </div>
        <button class="btn-primary" @click="step = 'confirm'">Gespeichert — weiter zur Kontrolle</button>
      </div>

      <form v-else-if="step === 'confirm'" class="space-y-2 rounded-lg border border-orange-700 bg-orange-900/20 p-3" @submit.prevent="ack">
        <p>Zur Kontrolle: die <b>letzten 6 Zeichen</b> des Passworts aus deinem Passwort-Manager eintippen.</p>
        <input v-model="confirmChars" class="input max-w-xs font-mono" maxlength="24" autocomplete="off" />
        <div class="flex gap-2">
          <button class="btn-primary" :disabled="confirmChars.trim().length < 6">Bestätigen</button>
          <button type="button" class="btn-secondary" @click="step = 'show'">Passwort nochmals anzeigen</button>
        </div>
      </form>

      <template v-else>
        <p class="text-green-300">✓ Notfall-Konto eingerichtet.</p>
        <button class="btn-secondary" @click="emit('close')">Schliessen</button>
      </template>

      <p v-if="error" class="text-red-400">{{ error }}</p>
    </div>
  </ModalShell>
</template>
