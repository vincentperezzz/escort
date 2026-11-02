// Google's Drive file chooser (Picker), loaded only when the user presses "Pick from Drive".
// The page never sees the OAuth client secret: the backend hands over an API key, the App ID
// (the Cloud project number) and a short-lived drive.file access token.
import { MOCK } from "./api.js";

const GAPI_SRC = "https://apis.google.com/js/api.js";
const LOAD_TIMEOUT_MS = 15000;

// What Escort can read. The chooser hides everything else (images, archives, folders).
export const PICKABLE_MIME_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
  "text/x-markdown",
  "application/vnd.google-apps.document",
  "application/vnd.google-apps.spreadsheet",
  "application/vnd.google-apps.presentation",
];

export class PickerLoadError extends Error {
  constructor() {
    super("Could not load Google's file chooser. Check your internet connection, then try again.");
    this.code = "picker_unavailable";
  }
}

let loading = null;

function loadScript() {
  return new Promise((resolve, reject) => {
    if (window.gapi?.load) return resolve(window.gapi);
    const script = document.createElement("script");
    script.src = GAPI_SRC;
    script.async = true;
    const timer = setTimeout(() => reject(new PickerLoadError()), LOAD_TIMEOUT_MS);
    script.onload = () => {
      clearTimeout(timer);
      window.gapi?.load ? resolve(window.gapi) : reject(new PickerLoadError());
    };
    script.onerror = () => {
      clearTimeout(timer);
      script.remove(); // so the next press tries a fresh load
      reject(new PickerLoadError());
    };
    document.head.appendChild(script);
  });
}

/** Resolves with google.picker; rejects with PickerLoadError when offline or blocked. */
export function loadPicker() {
  if (window.google?.picker?.PickerBuilder) return Promise.resolve(window.google.picker);
  loading ??= loadScript()
    .then(
      (gapi) =>
        new Promise((resolve, reject) => {
          gapi.load("picker", {
            callback: () => (window.google?.picker ? resolve(window.google.picker) : reject(new PickerLoadError())),
            onerror: () => reject(new PickerLoadError()),
            timeout: LOAD_TIMEOUT_MS,
            ontimeout: () => reject(new PickerLoadError()),
          });
        }),
    )
    .catch((err) => {
      loading = null;
      throw err;
    });
  return loading;
}

/**
 * Open the chooser with the config from GET /api/google/picker-config.
 * Resolves with {id, mimeType, name} for the picked file, or null when the user cancels.
 */
export async function pickDriveFile(config) {
  if (MOCK) {
    // ?mock: no network. ?picker=offline shows the load failure.
    await new Promise((r) => setTimeout(r, 600));
    if (config.mock_offline) throw new PickerLoadError();
    return { id: "mock_drive_file_0001", mimeType: "application/pdf", name: "BayaniPay SOW.pdf" };
  }
  const picker = await loadPicker();
  return new Promise((resolve) => {
    const view = new picker.DocsView(picker.ViewId.DOCS)
      .setIncludeFolders(true)
      .setSelectFolderEnabled(false)
      .setMimeTypes(PICKABLE_MIME_TYPES.join(","));
    const built = new picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(config.access_token)
      .setDeveloperKey(config.api_key)
      .setAppId(config.app_id)
      .setOrigin(window.location.origin)
      .setTitle("Pick a file for Escort")
      .setCallback((data) => {
        const action = data[picker.Response.ACTION];
        if (action === picker.Action.PICKED) {
          const doc = (data[picker.Response.DOCUMENTS] || [])[0] || {};
          resolve({ id: doc[picker.Document.ID], mimeType: doc[picker.Document.MIME_TYPE] || "", name: doc[picker.Document.NAME] || "" });
        } else if (action === picker.Action.CANCEL) {
          resolve(null);
        }
      })
      .build();
    built.setVisible(true);
  });
}
