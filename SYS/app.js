#!/usr/bin/env node
// SYS 入口網站後端：列出 SYS 上一層目錄的檔案與資料夾，並儲存每個項目的備註。
// 只使用 Node.js 內建模組，不需要 npm install。
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.PORT) || 3000;
const HOST = process.env.HOST || '127.0.0.1';

const SYS_DIR = __dirname;
const PARENT_DIR = path.join(SYS_DIR, '..');
const SYS_NAME = path.basename(SYS_DIR);
const INDEX_FILE = path.join(SYS_DIR, 'index.html');
const NOTES_FILE = path.join(SYS_DIR, 'notes.json');
const CONFIG_FILE = path.join(SYS_DIR, 'config.json');
const MAX_BODY = 1024 * 1024;

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
    const pathname = new URL(req.url, 'http://localhost').pathname;
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

server.listen(PORT, HOST, () => {
    const shownHost = HOST === '0.0.0.0' ? 'localhost' : HOST;
    console.log(`SYS 入口網站已啟動：http://${shownHost}:${PORT}`);
    console.log(`列出的目錄：${path.resolve(PARENT_DIR)}`);
});
