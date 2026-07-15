const { app, BrowserWindow, shell, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let backendProcess = null;
let mainWindow = null;
let quitting = false;
let backendOutputTail = '';

const TAIL_MAX_CHARS = 4000;

function appendTail(chunk) {
    backendOutputTail = (backendOutputTail + chunk).slice(-TAIL_MAX_CHARS);
}

// Single-instance lock: a second launch sharing the same DATABASE_URL would
// spawn a second backend against the same DB (and try to bind the same
// port) — refuse it and just focus the already-running window instead.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
    app.quit();
} else {
    app.on('second-instance', () => {
        if (mainWindow) {
            if (mainWindow.isMinimized()) mainWindow.restore();
            mainWindow.focus();
        }
    });

    // Open an OAuth authorize URL in the user's default browser (see preload.js).
    // Restricted to http(s) so the renderer can never coerce the main process into
    // opening arbitrary schemes (file://, custom protocols, etc.).
    ipcMain.handle('open-external', (_event, url) => {
        if (typeof url === 'string' && /^https?:\/\//i.test(url)) {
            return shell.openExternal(url);
        }
        return undefined;
    });

    function createWindow() {
        mainWindow = new BrowserWindow({
            width: 1200,
            height: 800,
            webPreferences: {
                preload: path.join(__dirname, 'preload.js')
            }
        });

        // Anything the renderer tries to open as a new window (target=_blank,
        // window.open) goes to the system browser — the app has no use for a
        // second in-app window.
        mainWindow.webContents.setWindowOpenHandler(({ url }) => {
            if (/^https?:\/\//i.test(url)) {
                shell.openExternal(url);
            }
            return { action: 'deny' };
        });

        mainWindow.on('closed', () => {
            mainWindow = null;
        });

        // Load the frontend
        mainWindow.loadFile(path.join(__dirname, 'frontend', 'index.html'));
    }

    // Optional operator config, dropped in without a rebuild:
    //   ~/Library/Application Support/salescale-app/config.json
    //   {
    //     "databaseUrl": "postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres",
    //     "superadminEmails": "you@example.com"
    //   }
    function readConfig() {
        try {
            const cfgPath = path.join(app.getPath('userData'), 'config.json');
            return JSON.parse(fs.readFileSync(cfgPath, 'utf8')) || {};
        } catch {
            return {};
        }
    }

    function resolveDatabaseUrl(cfg) {
        // 1. An explicit env var wins (e.g. launched from a shell with it set).
        if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
        // 2. A Supabase (or other Postgres) URL from the config file.
        if (cfg.databaseUrl) return cfg.databaseUrl;
        // 3. Fallback: a local SQLite file under the user's app-data directory. A
        //    PyInstaller one-file binary runs from a read-only temp dir, so a
        //    relative sqlite path would not survive (or would fail to write).
        return `sqlite:///${path.join(app.getPath('userData'), 'salescale.db')}`;
    }

    function startBackend() {
        const backendPath = path.join(process.resourcesPath, 'backend', 'main'); // Assuming the executable is named 'main'
        const cfg = readConfig();

        // Only forward secrets that are actually configured, so we never overwrite
        // a real env value with an empty string (which would silence the backend's
        // own defaults/validation).
        const passthrough = (envKey, cfgKey) => {
            const v = process.env[envKey] || cfg[cfgKey];
            return v ? { [envKey]: v } : {};
        };

        let spawnEnv = {
            ...process.env,
            DATABASE_URL: resolveDatabaseUrl(cfg),
            DESKTOP_MODE: '1',
            // Platform super-admin allowlist (env var wins, else config file).
            SUPERADMIN_EMAILS:
                process.env.SUPERADMIN_EMAILS || cfg.superadminEmails || '',
            // Production secrets, if the operator configured them.
            ...passthrough('JWT_SECRET', 'jwtSecret'),
            ...passthrough('TOKEN_ENCRYPTION_KEY', 'tokenEncryptionKey'),
            // Email delivery (Resend) + where verification/reset links point.
            ...passthrough('RESEND_API_KEY', 'resendApiKey'),
            ...passthrough('EMAIL_DEFAULT_FROM_ADDRESS', 'emailFromAddress'),
            ...passthrough('APP_BASE_URL', 'appBaseUrl'),
            // Operator ad-platform app credentials, so the desktop app can run
            // the Meta/Google connect (OAuth opens in the system browser and
            // the callback returns to this local backend on 127.0.0.1:8000).
            ...passthrough('META_APP_ID', 'metaAppId'),
            ...passthrough('META_APP_SECRET', 'metaAppSecret'),
            ...passthrough('GOOGLE_CLIENT_ID', 'googleClientId'),
            ...passthrough('GOOGLE_CLIENT_SECRET', 'googleClientSecret'),
            ...passthrough('GOOGLE_DEVELOPER_TOKEN', 'googleDeveloperToken'),
            ...passthrough('GOOGLE_LOGIN_CUSTOMER_ID', 'googleLoginCustomerId'),
        };

        // A raw "env" object in config.json is a generic escape hatch for any
        // backend setting without a dedicated named field above (API_BASE_URL,
        // TWILIO_*, an AI provider key, ...) — applied last so it can override
        // any of the named passthroughs too.
        if (cfg.env && typeof cfg.env === 'object') {
            spawnEnv = { ...spawnEnv, ...cfg.env };
        }

        try {
            backendProcess = spawn(backendPath, [], { env: spawnEnv });
        } catch (err) {
            dialog.showErrorBox(
                'Salescale backend failed to start',
                `Could not launch the backend process:\n\n${err.message}`
            );
            return;
        }

        backendProcess.stdout.on('data', (data) => {
            appendTail(data.toString());
            console.log(`Backend stdout: ${data}`);
        });

        backendProcess.stderr.on('data', (data) => {
            appendTail(data.toString());
            console.error(`Backend stderr: ${data}`);
        });

        backendProcess.on('error', (err) => {
            dialog.showErrorBox(
                'Salescale backend failed to start',
                `Could not launch the backend process:\n\n${err.message}`
            );
        });

        backendProcess.on('exit', (code, signal) => {
            backendProcess = null;
            // A clean exit during app shutdown is expected — only surface a
            // dialog for a backend that died unexpectedly while the app is
            // meant to be running (e.g. a startup migration failure).
            if (quitting) return;
            if (code === 0) return;
            dialog.showErrorBox(
                'Salescale backend stopped unexpectedly',
                `The backend process exited (code ${code}${signal ? `, signal ${signal}` : ''}).\n\n` +
                    `Last output:\n${backendOutputTail || '(none captured)'}`
            );
        });
    }

    app.whenReady().then(() => {
        startBackend();
        createWindow();

        app.on('activate', function () {
            // macOS dock reactivate after all windows are closed — the backend
            // keeps running independently of window lifecycle, so only
            // recreate the window; spawning a second backend here would
            // orphan the first (and can double-send scheduled outreach).
            if (BrowserWindow.getAllWindows().length === 0) createWindow();
        });
    });

    app.on('window-all-closed', function () {
        if (process.platform !== 'darwin') {
            quitting = true;
            if (backendProcess) backendProcess.kill();
            app.quit();
        }
    });

    app.on('before-quit', () => {
        quitting = true;
        if (backendProcess) backendProcess.kill();
    });
}
