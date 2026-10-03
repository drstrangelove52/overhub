<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api";
import ModalShell from "./ModalShell.vue";

const props = defineProps({ apps: Array, me: Object });
const emit = defineEmits(["close"]);

const users = ref([]);
const error = ref("");
const info = ref("");
const showNew = ref(false);
const form = reactive({ username: "", password: "" });
const showFormPassword = ref(false);
// One user is open at a time; roles are listed one app per line so many apps still fit.
const openId = ref(null);
const resetPassword = ref("");
const showResetPassword = ref(false);

const installed = computed(() => props.apps.filter((a) => a.installed));
const roleLabel = { user: "Benutzer", admin: "Admin" };

function summary(u) {
  const parts = installed.value.filter((a) => u.roles[a.id]).map((a) => `${a.name}: ${roleLabel[u.roles[a.id]]}`);
  return parts.length ? parts.join(" · ") : "keine Apps";
}

function toggle(u) {
  openId.value = openId.value === u.id ? null : u.id;
  resetPassword.value = "";
  showResetPassword.value = false;
}

async function load() {
  try {
    users.value = await api("/users");
  } catch (e) {
    error.value = e.message;
  }
}
onMounted(load);

async function setRole(user, appId, role) {
  error.value = info.value = "";
  try {
    const updated = await api(`/users/${user.id}`, { method: "PUT", body: { roles: { [appId]: role || null } } });
    Object.assign(user, updated);
  } catch (e) {
    error.value = e.message;
    await load();
  }
}

async function create() {
  error.value = info.value = "";
  try {
    // New users get "Benutzer" in every installed app; adjust afterwards if needed.
    const roles = Object.fromEntries(installed.value.map((a) => [a.id, "user"]));
    const created = await api("/users", { method: "POST", body: { username: form.username, password: form.password, roles } });
    info.value = `${form.username.toLowerCase()} angelegt. Benutzername und Passwort weitergeben — ändern kann man es danach selbst.`;
    form.username = form.password = "";
    showFormPassword.value = false;
    showNew.value = false;
    await load();
    if (created?.id) openId.value = created.id;
  } catch (e) {
    error.value = e.message;
  }
}

async function doReset(user) {
  error.value = info.value = "";
  try {
    await api(`/users/${user.id}`, { method: "PUT", body: { password: resetPassword.value } });
    info.value = `Passwort von ${user.username} gesetzt, alle Anmeldungen beendet.`;
    resetPassword.value = "";
    showResetPassword.value = false;
  } catch (e) {
    error.value = e.message;
  }
}

async function remove(user) {
  if (!confirm(`${user.username} löschen? Die Daten in den Apps bleiben dort erhalten.`)) return;
  error.value = info.value = "";
  try {
    await api(`/users/${user.id}`, { method: "DELETE" });
    openId.value = null;
    await load();
  } catch (e) {
    error.value = e.message;
  }
}
</script>

<template>
  <ModalShell title="Benutzer" wide @close="emit('close')">
    <div class="space-y-4 text-sm">
      <p class="text-gray-400">
        Ein Konto für alle Apps: wer in OverHub angemeldet ist, ist es auch in den Apps. Pro App lässt sich festlegen, ob
        jemand sie nutzen darf. Heisst ein Benutzer gleich wie in der App, behält er dort seine Daten.
      </p>

      <ul class="divide-y divide-gray-800 rounded-lg border border-gray-800">
        <li v-for="u in users" :key="u.id">
          <button class="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-gray-800/50" @click="toggle(u)">
            <span class="font-medium">{{ u.username }}</span>
            <span v-if="u.roles._overhub === 'admin'" class="rounded bg-blue-900/60 px-1.5 py-0.5 text-xs text-blue-200">OverHub-Admin</span>
            <span class="min-w-0 flex-1 truncate text-xs text-gray-500">{{ summary(u) }}</span>
            <span class="text-gray-500">{{ openId === u.id ? "▴" : "▾" }}</span>
          </button>

          <div v-if="openId === u.id" class="space-y-3 border-t border-gray-800 bg-gray-900/40 px-3 py-3">
            <label class="flex items-center gap-2">
              <input type="checkbox" :checked="u.roles._overhub === 'admin'" :disabled="u.username === me.username && u.roles._overhub === 'admin'"
                     @change="(e) => setRole(u, '_overhub', e.target.checked ? 'admin' : null)" />
              OverHub-Admin <span class="text-xs text-gray-500">(Apps installieren, Benutzer und Backups verwalten)</span>
            </label>

            <div>
              <div class="mb-1 text-xs text-gray-400">Zugriff pro App</div>
              <div class="max-h-64 space-y-1 overflow-y-auto pr-1">
                <div v-for="a in installed" :key="a.id" class="flex items-center justify-between gap-3">
                  <span>{{ a.name }}</span>
                  <select class="rounded border border-gray-700 bg-gray-800 px-2 py-1" :value="u.roles[a.id] || ''"
                          @change="(e) => setRole(u, a.id, e.target.value)">
                    <option value="">kein Zugriff</option>
                    <option value="user">Benutzer</option>
                    <option value="admin">Admin</option>
                  </select>
                </div>
                <p v-if="!installed.length" class="text-xs text-gray-500">Noch keine Apps installiert.</p>
              </div>
            </div>

            <form class="flex flex-wrap items-center gap-2" @submit.prevent="doReset(u)">
              <input v-model="resetPassword" :type="showResetPassword ? 'text' : 'password'" class="input max-w-xs"
                     placeholder="Neues Passwort (min. 8 Zeichen)" minlength="8" autocomplete="new-password" required />
              <button type="button" class="text-xs text-gray-400 hover:text-gray-200" @click="showResetPassword = !showResetPassword">
                {{ showResetPassword ? "Verbergen" : "Anzeigen" }}
              </button>
              <button class="btn-secondary">Passwort setzen</button>
              <span class="flex-1"></span>
              <button v-if="u.username !== me.username" type="button" class="text-gray-500 hover:text-red-300" @click="remove(u)">Benutzer löschen</button>
            </form>
          </div>
        </li>
      </ul>

      <form v-if="showNew" class="space-y-2 rounded-lg border border-gray-800 p-3" @submit.prevent="create">
        <div class="grid gap-2 sm:grid-cols-2">
          <input v-model="form.username" class="input" placeholder="Benutzername" autocomplete="off" required />
          <div class="flex items-center gap-2">
            <input v-model="form.password" :type="showFormPassword ? 'text' : 'password'" class="input"
                   placeholder="Start-Passwort (min. 8 Zeichen)" minlength="8" autocomplete="new-password" required />
            <button type="button" class="text-xs text-gray-400 hover:text-gray-200" @click="showFormPassword = !showFormPassword">
              {{ showFormPassword ? "Verbergen" : "Anzeigen" }}
            </button>
          </div>
        </div>
        <p class="text-xs text-gray-500">Neue Benutzer erhalten in allen installierten Apps die Rolle „Benutzer“; danach anpassen.</p>
        <div class="flex justify-end gap-2">
          <button type="button" class="btn-secondary" @click="showNew = false">Abbrechen</button>
          <button class="btn-primary">Anlegen</button>
        </div>
      </form>
      <button v-else class="btn-secondary" @click="showNew = true">Benutzer hinzufügen</button>

      <p v-if="info" class="text-green-300">{{ info }}</p>
      <p v-if="error" class="text-red-400">{{ error }}</p>
    </div>
  </ModalShell>
</template>
