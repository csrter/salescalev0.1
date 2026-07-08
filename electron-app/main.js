const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let backendProcess = null;

function createWindow() {
    const mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js')
        }
    });

    // Load the frontend
    mainWindow.loadFile(path.join(__dirname, 'frontend', 'index.html'));

    // Start the backend executable
    startBackend();
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

    backendProcess = spawn(backendPath, [], {
        env: {
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
        },
    });

    backendProcess.stdout.on('data', (data) => {
        console.log(`Backend stdout: ${data}`);
    });

    backendProcess.stderr.on('data', (data) => {
        console.error(`Backend stderr: ${data}`);
    });
}

app.whenReady().then(() => {
    createWindow();

    app.on('activate', function () {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

app.on('window-all-closed', function () {
    if (process.platform !== 'darwin') {
        if (backendProcess) {
            backendProcess.kill();
        }
        app.quit();
    }
});

app.on('before-quit', () => {
    if (backendProcess) {
        backendProcess.kill();
    }
});
