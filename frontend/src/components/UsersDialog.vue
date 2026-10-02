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
const resetFor = ref(null);
const resetPassword = ref("");

const installed = computed(() => props.apps.filter((a) => a.installed));

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
    // New users get "Benutzer" in every installed app; adjust below if needed.
    const roles = Object.fromEntries(installed.value.map((a) => [a.id, "user"]));
    await api("/users", { method: "POST", body: { username: form.username, password: form.password, roles } });
    info.value = `${form.username.toLowerCase()} angelegt. Benutzername und Passwort weitergeben — ändern kann man es danach selbst.`;
    form.username = form.password = "";
    showNew.value = false;
    await load();
  } catch (e) {
    error.value = e.message;
  }
}

async function doReset() {
  error.value = info.value = "";
  try {
    await api(`/users/${resetFor.value.id}`, { method: "PUT", body: { password: resetPassword.value } });
    info.value = `Passwort von ${resetFor.value.username} gesetzt, alle Anmeldungen beendet.`;
    resetFor.value = null;
    resetPassword.value = "";
  } catch (e) {
    error.value = e.message;
  }
}

async function remove(user) {
  if (!confirm(`${user.username} löschen? Die Daten in den Apps bleiben dort erhalten.`)) return;
  error.value = info.value = "";
  try {
    await api(`/users/${user.id}`, { method: "DELETE" });
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
      <div class="overflow-x-auto">
        <table class="w-full text-left">
          <thead class="text-xs text-gray-400">
            <tr>
              <th class="py-2 pr-3">Benutzer</th>
              <th class="py-2 pr-3">OverHub-Admin</th>
              <th v-for="a in installed" :key="a.id" class="py-2 pr-3">{{ a.name }}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id" class="border-t border-gray-800">
              <td class="py-2 pr-3 font-medium">{{ u.username }}</td>
              <td class="py-2 pr-3">
                <input type="checkbox" :checked="u.roles._overhub === 'admin'" :disabled="u.username === me.username && u.roles._overhub === 'admin'"
                       @change="(e) => setRole(u, '_overhub', e.target.checked ? 'admin' : null)" />
              </td>
              <td v-for="a in installed" :key="a.id" class="py-2 pr-3">
                <select class="rounded border border-gray-700 bg-gray-800 px-2 py-1" :value="u.roles[a.id] || ''"
                        @change="(e) => setRole(u, a.id, e.target.value)">
                  <option value="">kein Zugriff</option>
                  <option value="user">Benutzer</option>
                  <option value="admin">Admin</option>
                </select>
              </td>
              <td class="whitespace-nowrap py-2 text-right">
                <button class="mr-3 text-gray-400 hover:text-gray-200" @click="resetFor = u">Passwort setzen</button>
                <button v-if="u.username !== me.username" class="text-gray-500 hover:text-red-300" @click="remove(u)">Löschen</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <form v-if="resetFor" class="flex flex-wrap items-center gap-2 rounded-lg border border-gray-800 p-3" @submit.prevent="doReset">
        <span>Neues Passwort für <b>{{ resetFor.username }}</b>:</span>
        <input v-model="resetPassword" type="text" class="input max-w-xs" minlength="8" autocomplete="off" required />
        <button class="btn-primary">Setzen</button>
        <button type="button" class="btn-secondary" @click="resetFor = null">Abbrechen</button>
      </form>

      <form v-if="showNew" class="space-y-2 rounded-lg border border-gray-800 p-3" @submit.prevent="create">
        <div class="grid gap-2 sm:grid-cols-2">
          <input v-model="form.username" class="input" placeholder="Benutzername, z.B. tanja" required />
          <input v-model="form.password" type="text" class="input" placeholder="Start-Passwort (min. 8 Zeichen)" minlength="8" autocomplete="off" required />
        </div>
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
