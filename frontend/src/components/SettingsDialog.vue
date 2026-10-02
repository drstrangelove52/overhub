<script setup>
import { computed, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const props = defineProps({ system: Object, user: Object });
const emit = defineEmits(["close"]);

const current = ref("");
const next = ref("");
const message = ref("");
const ok = ref(false);

const expiry = computed(() => {
  const value = props.system?.tailscale?.key_expiry;
  if (!value) return "läuft nicht ab";
  return new Date(value).toLocaleDateString("de-CH");
});

async function submit() {
  message.value = "";
  ok.value = false;
  try {
    await api("/auth/me/password", { method: "PUT", body: { current_password: current.value, new_password: next.value } });
    ok.value = true;
    message.value = "Passwort geändert.";
    current.value = next.value = "";
  } catch (e) {
    message.value = e.message;
  }
}
</script>

<template>
  <ModalShell title="Einstellungen" @close="emit('close')">
    <div class="space-y-6">
      <section>
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">System</h4>
        <dl v-if="system" class="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
          <dt class="text-gray-400">OverHub</dt>
          <dd>{{ system.version }}</dd>
          <dt class="text-gray-400">Adresse</dt>
          <dd class="break-all">{{ system.tailscale.dns_name ? `https://${system.tailscale.dns_name}` : "unbekannt" }}</dd>
          <dt class="text-gray-400">Tailscale</dt>
          <dd>{{ system.tailscale.state || "unbekannt" }}</dd>
          <dt class="text-gray-400">Schlüssel</dt>
          <dd>{{ expiry }}</dd>
          <dt class="text-gray-400">Gerät</dt>
          <dd>{{ system.arch }}{{ system.ram_mb ? `, ${Math.round(system.ram_mb / 1024)} GB RAM` : "" }}</dd>
        </dl>
        <p class="mt-2 text-xs text-gray-500">
          OverHub aktualisieren und neue Apps in den Katalog holen: auf dem Gerät den Installer erneut ausführen.
        </p>
      </section>

      <section>
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Passwort für {{ user.username }} ändern</h4>
        <form class="space-y-3" @submit.prevent="submit">
          <input v-model="current" type="password" class="input" placeholder="Aktuelles Passwort" autocomplete="current-password" required />
          <input v-model="next" type="password" class="input" placeholder="Neues Passwort (min. 8 Zeichen)" autocomplete="new-password" minlength="8" required />
          <p v-if="message" class="text-sm" :class="ok ? 'text-green-300' : 'text-red-400'">{{ message }}</p>
          <div class="flex justify-end">
            <button class="btn-primary">Passwort ändern</button>
          </div>
        </form>
      </section>
    </div>
  </ModalShell>
</template>
