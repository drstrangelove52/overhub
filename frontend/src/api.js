export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Set by App.vue: a 401 anywhere sends the user back to the login form.
export const hooks = { onUnauthorized: () => {} };

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && path !== "/auth/login") hooks.onUnauthorized();
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `Fehler ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return data;
}
