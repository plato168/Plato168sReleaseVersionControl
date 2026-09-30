#!/usr/bin/env node
// SYS 入口網站後端：列出 SYS 上一層目錄的檔案與資料夾，並儲存每個項目的備註。
// 只使用 Node.js 內建模組，不需要 npm install。
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

const PORT = Number(process.env.PORT) || 3000;
// 預設 0.0.0.0：區網其他電腦也能連線；只想本機使用時設 HOST=127.0.0.1
const HOST = process.env.HOST || '0.0.0.0';

const SYS_DIR = __dirname;
const PARENT_DIR = path.join(SYS_DIR, '..');
const SYS_NAME = path.basename(SYS_DIR);
const INDEX_FILE = path.join(SYS_DIR, 'index.html');
const NOTES_FILE = path.join(SYS_DIR, 'notes.json');
const CONFIG_FILE = path.join(SYS_DIR, 'config.json');
const PASSWORD_FILE = path.join(SYS_DIR, 'password.txt');
const HINT_FILE = path.join(SYS_DIR, 'password-hint.txt');
const MAX_BODY = 1024 * 1024;

// 密碼：優先使用環境變數 SYS_PASSWORD，否則讀取 SYS/password.txt 的第一行。
// 有設密碼時所有人都要登入；沒設密碼時只允許本機與區網連線。
function loadPassword() {
    if (process.env.SYS_PASSWORD) return process.env.SYS_PASSWORD;
    try {
        return fs.readFileSync(PASSWORD_FILE, 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)[0].trim();
    } catch (e) {
        return '';
    }
}
const PASSWORD = loadPassword();

// 登入失敗太多次就暫時封鎖該來源，防止猜密碼
const MAX_FAILURES = 10;
const LOCK_MS = 15 * 60 * 1000;
const TOKEN_SECONDS = 7 * 24 * 60 * 60;   // 登入後 7 天內不用再輸入密碼
const failures = new Map();

function isLoopback(ip) {
    return ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1';
}

function isPrivate(ip) {
    const v4 = ip.replace(/^::ffff:/, '');
    if (/^(127\.|10\.|192\.168\.|169\.254\.)/.test(v4)) return true;
    const m = v4.match(/^172\.(\d+)\./);
    if (m && Number(m[1]) >= 16 && Number(m[1]) <= 31) return true;
    return ip === '::1' || /^f[cd]/i.test(ip) || /^fe80:/i.test(ip);
}

// 取得真正的使用者 IP。只有請求來自本機（例如 Cloudflare Tunnel、ngrok 等轉發程式）
// 時才採信轉發標頭，避免外部使用者偽造。
function clientIp(req) {
    const remote = req.socket.remoteAddress || '';
    if (isLoopback(remote)) {
        const forwarded = req.headers['cf-connecting-ip'] || String(req.headers['x-forwarded-for'] || '').split(',')[0];
        if (forwarded && forwarded.trim()) return forwarded.trim();
    }
    return remote;
}

function loadHint() {
    try {
        return fs.readFileSync(HINT_FILE, 'utf8').replace(/^\uFEFF/, '').trim();
    } catch (e) {
        return '';
    }
}

function passwordMatches(given) {
    const a = crypto.createHash('sha256').update(given).digest();
    const b = crypto.createHash('sha256').update(PASSWORD).digest();
    return crypto.timingSafeEqual(a, b);
}

// 登入憑證：「到期時間.簽章」，以密碼當金鑰簽章。改密碼後舊的憑證全部失效。
function sign(expires) {
    return crypto.createHmac('sha256', PASSWORD).update(expires).digest('hex');
}

function makeToken() {
    const expires = String(Math.floor(Date.now() / 1000) + TOKEN_SECONDS);
    return `${expires}.${sign(expires)}`;
}

function tokenValid(token) {
    const [expires, signature] = String(token || '').split('.');
    if (!/^\d+$/.test(expires || '') || Number(expires) < Date.now() / 1000 || !signature) return false;
    const expected = Buffer.from(sign(expires));
    const given = Buffer.from(signature);
    return given.length === expected.length && crypto.timingSafeEqual(given, expected);
}

function blockedWithoutPassword(req) {
    return !PASSWORD && !isPrivate(clientIp(req));
}

// 沒設密碼時只允許本機與區網；有設密碼時需帶有效的登入憑證。
function authorize(req, res) {
    if (blockedWithoutPassword(req)) {
        sendJson(res, 403, { success: false, error: '尚未設定密碼，只允許區網連線。請在 SYS/password.txt 設定密碼。' });
        return false;
    }
    if (!PASSWORD || tokenValid(req.headers['x-sys-token'])) return true;
    sendJson(res, 401, { success: false, error: '請先登入' });
    return false;
}

function login(req, res, body) {
    const ip = clientIp(req);
    const record = failures.get(ip) || { count: 0, lockedUntil: 0 };
    if (record.lockedUntil > Date.now()) {
        return sendJson(res, 429, { success: false, error: '密碼錯誤次數過多，請 15 分鐘後再試。' });
    }
    const given = typeof body.password === 'string' ? body.password : '';
    if (PASSWORD && passwordMatches(given)) {
        failures.delete(ip);
        return sendJson(res, 200, { success: true, token: makeToken() });
    }
    const count = (record.lockedUntil ? 0 : record.count) + 1;
    failures.set(ip, { count, lockedUntil: count >= MAX_FAILURES ? Date.now() + LOCK_MS : 0 });
    if (count >= MAX_FAILURES) console.log(`[${new Date().toLocaleString()}] ${ip} 密碼錯誤 ${count} 次，封鎖 15 分鐘`);
    const left = MAX_FAILURES - count;
    return sendJson(res, 401, { success: false, error: left > 0 ? `密碼錯誤，還可以再試 ${left} 次` : '密碼錯誤次數過多，請 15 分鐘後再試。' });
}

function readJson(file) {
    try {
        const data = JSON.parse(fs.readFileSync(file, 'utf8'));
        return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
    } catch (e) {
        return {};
    }
}

// 先寫入暫存檔再改名，避免寫到一半中斷時損毀原檔。
function writeJson(file, data) {
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
    fs.renameSync(tmp, file);
}

function sendJson(res, status, data) {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(data));
}

function readBody(req) {
    return new Promise((resolve, reject) => {
        let size = 0;
        const chunks = [];
        req.on('data', chunk => {
            size += chunk.length;
            if (size > MAX_BODY) {
                reject(new Error('請求內容過大'));
                req.destroy();
                return;
            }
            chunks.push(chunk);
        });
        req.on('end', () => {
            try {
                resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {});
            } catch (e) {
                reject(new Error('JSON 格式錯誤'));
            }
        });
        req.on('error', reject);
    });
}

function listEntries(baseUrl) {
    const notes = readJson(NOTES_FILE);
    const prefix = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;

    return fs.readdirSync(PARENT_DIR, { withFileTypes: true })
        // 隱藏 SYS 自己與隱藏檔（例如 .git）
        .filter(entry => entry.name !== SYS_NAME && !entry.name.startsWith('.'))
        .map(entry => {
            let isDirectory = entry.isDirectory();
            if (entry.isSymbolicLink()) {
                try { isDirectory = fs.statSync(path.join(PARENT_DIR, entry.name)).isDirectory(); } catch (e) {}
            }
            return {
                name: entry.name,
                isDirectory,
                url: prefix + encodeURIComponent(entry.name) + (isDirectory ? '/' : ''),
                note: Object.prototype.hasOwnProperty.call(notes, entry.name) ? String(notes[entry.name]) : ''
            };
        })
        // 資料夾在前，再依名稱排序
        .sort((a, b) => (b.isDirectory - a.isDirectory) || a.name.localeCompare(b.name, 'zh-Hant'));
}

async function handleApi(req, res, pathname) {
    // 不需登入：告訴前端是否要顯示登入框，以及密碼提示
    if (req.method === 'GET' && pathname === '/api/auth-status') {
        return sendJson(res, 200, {
            success: true,
            required: Boolean(PASSWORD),
            hint: PASSWORD ? loadHint() : '',
            error: blockedWithoutPassword(req) ? '尚未設定密碼，只允許區網連線。請在 SYS/password.txt 設定密碼。' : ''
        });
    }
    if (req.method === 'POST' && pathname === '/api/login') return login(req, res, await readBody(req));
    if (!authorize(req, res)) return;

    if (req.method === 'GET' && pathname === '/api/config') {
        const config = readJson(CONFIG_FILE);
        return sendJson(res, 200, { success: true, baseUrl: typeof config.baseUrl === 'string' ? config.baseUrl : '' });
    }

    if (req.method !== 'POST') return sendJson(res, 405, { success: false, error: '不支援的請求方法' });
    const body = await readBody(req);

    if (pathname === '/api/get-links') {
        const baseUrl = typeof body.baseUrl === 'string' ? body.baseUrl.trim() : '';
        if (!baseUrl) return sendJson(res, 400, { success: false, error: '請提供基礎網址' });

        // 記住這次使用的基礎網址，下次開啟網頁時自動帶入
        const config = readJson(CONFIG_FILE);
        if (config.baseUrl !== baseUrl) writeJson(CONFIG_FILE, { ...config, baseUrl });

        try {
            return sendJson(res, 200, { success: true, parentDir: path.resolve(PARENT_DIR), data: listEntries(baseUrl) });
        } catch (e) {
            return sendJson(res, 500, { success: false, error: `無法讀取上層目錄：${e.message}` });
        }
    }

    if (pathname === '/api/save-note') {
        const { name, note } = body;
        if (typeof name !== 'string' || !name || name === '__proto__' || typeof note !== 'string') {
            return sendJson(res, 400, { success: false, error: '參數錯誤' });
        }
        const notes = readJson(NOTES_FILE);
        if (note.trim()) notes[name] = note;
        else delete notes[name];
        writeJson(NOTES_FILE, notes);
        return sendJson(res, 200, { success: true });
    }

    return sendJson(res, 404, { success: false, error: '找不到此 API' });
}

const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    // 前端呼叫 api.php?action=xxx（Apache 版），在 Node 版對應到 /api/xxx
    const pathname = url.pathname === '/api.php' ? `/api/${url.searchParams.get('action') || ''}` : url.pathname;
    try {

        if (pathname.startsWith('/api/')) return await handleApi(req, res, pathname);

        if (req.method === 'GET' && (pathname === '/' || pathname === '/index.html')) {
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            return fs.createReadStream(INDEX_FILE).pipe(res);
        }

        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('404 Not Found');
    } catch (e) {
        sendJson(res, 400, { success: false, error: e.message });
    }
});

// 列出本機在區網中的 IPv4 位址，方便告訴其他電腦要連哪個網址
function lanAddresses() {
    return Object.values(os.networkInterfaces())
        .flat()
        .filter(info => info && info.family === 'IPv4' && !info.internal)
        .map(info => info.address);
}

server.listen(PORT, HOST, () => {
    const shownHost = HOST === '0.0.0.0' ? 'localhost' : HOST;
    console.log(`SYS 入口網站已啟動：http://${shownHost}:${PORT}`);
    if (HOST === '0.0.0.0') {
        const addresses = lanAddresses();
        if (addresses.length) {
            console.log('區網其他電腦請開啟：');
            addresses.forEach(ip => console.log(`  http://${ip}:${PORT}`));
        }
    }
    console.log(`列出的目錄：${path.resolve(PARENT_DIR)}`);
    console.log(PASSWORD
        ? '已設定密碼：本機、區網、網際網路使用者都需要輸入密碼。'
        : '未設定密碼：只允許本機與區網連線。要開放網際網路請在 SYS/password.txt 設定密碼。');
});
