// Preload bridge: exposes a minimal, safe API to the (context-isolated)
// renderer. The web build has no `window.salescale`, so the frontend uses its
// presence to detect the desktop app.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('salescale', {
  isDesktop: true,
  // Open a URL in the user's default browser. Used for OAuth: the app UI is
  // file://, so navigating the window to an external auth URL would hijack the
  // app — instead the system browser handles login and the OAuth callback
  // returns to the local backend on 127.0.0.1:8000.
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
});
