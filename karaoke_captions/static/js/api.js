async function request(method, url, body) {
  const options = { method, headers: {} };
  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(url, options);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data.detail) message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {}
    throw new Error(message);
  }
  const type = res.headers.get("content-type") || "";
  if (type.includes("application/json")) return res.json();
  if (type.startsWith("image/")) return res.blob();
  return res.text();
}

/** Upload with progress events (fetch can't report upload progress). */
function upload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
      else reject(new Error(xhr.response?.detail || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload failed. Is the server still running?"));
    xhr.send(formData);
  });
}

export const api = {
  health: () => request("GET", "/api/health"),
  styleDefaults: () => request("GET", "/api/style/defaults"),

  projects: () => request("GET", "/api/projects"),
  project: (id) => request("GET", `/api/projects/${id}`),
  createProject: (formData, onProgress) => upload("/api/projects", formData, onProgress),
  updateProject: (id, patch) => request("PATCH", `/api/projects/${id}`, patch),
  deleteProject: (id) => request("DELETE", `/api/projects/${id}`),
  retranscribe: (id, options) => request("POST", `/api/projects/${id}/transcribe`, options),
  snapshot: (id, body) => request("POST", `/api/projects/${id}/snapshot`, body),
  render: (id, body) => request("POST", `/api/projects/${id}/render`, body),
  renders: (id) => request("GET", `/api/projects/${id}/renders`),
  deleteRender: (id, renderId) => request("DELETE", `/api/projects/${id}/renders/${renderId}`),
  mediaUrl: (id) => `/api/projects/${id}/media`,

  job: (id) => request("GET", `/api/jobs/${id}`),
  cancelJob: (id) => request("POST", `/api/jobs/${id}/cancel`),

  fonts: () => request("GET", "/api/fonts"),
  uploadFont: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("POST", "/api/fonts", fd);
  },
  fontFaceUrl: (faceId) => `/api/fonts/face/${faceId}`,

  templates: () => request("GET", "/api/templates"),
  uploadTemplate: (file, onProgress) => {
    const fd = new FormData();
    fd.append("file", file);
    return upload("/api/templates", fd, onProgress);
  },
  deleteTemplate: (id) => request("DELETE", `/api/templates/${id}`),
  templateUrl: (id) => `/api/templates/${id}/video`,

  presets: () => request("GET", "/api/presets"),
  savePreset: (name, style) => request("POST", "/api/presets", { name, style }),
  deletePreset: (id) => request("DELETE", `/api/presets/${encodeURIComponent(id)}`),
};
