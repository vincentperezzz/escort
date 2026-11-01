export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body === "string" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      detail = data?.detail || data?.error || "";
    } catch {
      /* body was not JSON */
    }
    const err = new Error(detail || `${response.status}`);
    err.status = response.status;
    throw err;
  }
  return response.json();
}

export async function apiUpload(path, file) {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(path, { method: "POST", body });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

export function post(path, payload = {}) {
  return api(path, { method: "POST", body: JSON.stringify(payload) });
}

export function formatEventWhen(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
