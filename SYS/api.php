<?php
// SYS 入口網站後端（Apache + PHP 版）：列出 SYS 上一層目錄的檔案與資料夾，並儲存每個項目的備註。
// 放在 Apache 網站中的 SYS 資料夾即可使用，不需要另外執行程式。

declare(strict_types=1);

const MAX_FAILURES = 10;
const LOCK_SECONDS = 15 * 60;
const TOKEN_SECONDS = 7 * 24 * 60 * 60;   // 登入後 7 天內不用再輸入密碼

$sysDir = __DIR__;
$parentDir = dirname($sysDir);
$sysName = basename($sysDir);
$notesFile = $sysDir . '/notes.json';
$configFile = $sysDir . '/config.json';
$passwordFile = $sysDir . '/password.txt';
$hintFile = $sysDir . '/password-hint.txt';
$failuresFile = $sysDir . '/login-failures.json';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function send_json(int $status, array $data): void
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function read_json(string $file): array
{
    if (!is_file($file)) return [];
    $data = json_decode((string) file_get_contents($file), true);
    return is_array($data) ? $data : [];
}

// 先寫入暫存檔再改名，避免寫到一半中斷時損毀原檔。
function write_json(string $file, array $data): void
{
    $tmp = $file . '.tmp';
    $json = json_encode($data ?: new stdClass(), JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if (file_put_contents($tmp, $json, LOCK_EX) === false || !rename($tmp, $file)) {
        send_json(500, ['success' => false, 'error' => '無法寫入 ' . basename($file) . '，請確認 Apache 對 SYS 資料夾有寫入權限']);
    }
}

// 密碼：SYS/password.txt 的第一行。有設密碼時所有人都要登入；沒設密碼時只允許本機與區網連線。
function load_password(string $file): string
{
    if (!is_file($file)) return '';
    $lines = preg_split('/\r?\n/', preg_replace('/^\xEF\xBB\xBF/', '', (string) file_get_contents($file)));
    return trim($lines[0] ?? '');
}

function is_private_ip(string $ip): bool
{
    return filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE) === false;
}

function load_hint(string $file): string
{
    if (!is_file($file)) return '';
    return trim(preg_replace('/^\xEF\xBB\xBF/', '', (string) file_get_contents($file)));
}

// 登入憑證：「到期時間.簽章」，以密碼當金鑰簽章。改密碼後舊的憑證全部失效。
function make_token(string $password): string
{
    $expires = (string) (time() + TOKEN_SECONDS);
    return $expires . '.' . hash_hmac('sha256', $expires, $password);
}

function token_valid(string $token, string $password): bool
{
    $parts = explode('.', $token, 2);
    if (count($parts) !== 2 || !ctype_digit($parts[0]) || (int) $parts[0] < time()) return false;
    return hash_equals(hash_hmac('sha256', $parts[0], $password), $parts[1]);
}

// 沒設密碼時只允許本機與區網；有設密碼時需帶有效的登入憑證。
function authorize(string $password): void
{
    if ($password === '') {
        if (is_private_ip($_SERVER['REMOTE_ADDR'] ?? '')) return;
        send_json(403, ['success' => false, 'error' => '尚未設定密碼，只允許區網連線。請在 SYS/password.txt 設定密碼。']);
    }
    if (token_valid((string) ($_SERVER['HTTP_X_SYS_TOKEN'] ?? ''), $password)) return;
    send_json(401, ['success' => false, 'error' => '請先登入']);
}

function login(string $password, string $failuresFile, array $body): void
{
    $ip = $_SERVER['REMOTE_ADDR'] ?? '';
    $failures = read_json($failuresFile);
    $record = $failures[$ip] ?? ['count' => 0, 'lockedUntil' => 0];
    if ($record['lockedUntil'] > time()) {
        send_json(429, ['success' => false, 'error' => '密碼錯誤次數過多，請 15 分鐘後再試。']);
    }

    $given = is_string($body['password'] ?? null) ? $body['password'] : '';
    if ($password !== '' && hash_equals(hash('sha256', $password), hash('sha256', $given))) {
        if (isset($failures[$ip])) {
            unset($failures[$ip]);
            write_json($failuresFile, $failures);
        }
        send_json(200, ['success' => true, 'token' => make_token($password)]);
    }

    $count = ($record['lockedUntil'] > 0 ? 0 : $record['count']) + 1;
    $failures[$ip] = ['count' => $count, 'lockedUntil' => $count >= MAX_FAILURES ? time() + LOCK_SECONDS : 0];
    write_json($failuresFile, $failures);
    $left = MAX_FAILURES - $count;
    send_json(401, ['success' => false, 'error' => $left > 0 ? "密碼錯誤，還可以再試 {$left} 次" : '密碼錯誤次數過多，請 15 分鐘後再試。']);
}

function list_entries(string $parentDir, string $sysName, string $baseUrl, array $notes): array
{
    $prefix = substr($baseUrl, -1) === '/' ? $baseUrl : $baseUrl . '/';
    $items = [];
    foreach (scandir($parentDir) ?: [] as $name) {
        // 隱藏 SYS 自己與隱藏檔（例如 .git、.htaccess）
        if ($name === $sysName || $name[0] === '.') continue;
        $isDirectory = is_dir($parentDir . DIRECTORY_SEPARATOR . $name);
        $items[] = [
            'name' => $name,
            'isDirectory' => $isDirectory,
            'url' => $prefix . rawurlencode($name) . ($isDirectory ? '/' : ''),
            'note' => isset($notes[$name]) ? (string) $notes[$name] : '',
        ];
    }
    // 資料夾在前，再依名稱排序
    usort($items, fn($a, $b) => ($b['isDirectory'] <=> $a['isDirectory']) ?: strnatcasecmp($a['name'], $b['name']));
    return $items;
}

$password = load_password($passwordFile);
$action = $_GET['action'] ?? '';
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

// 不需登入：告訴前端是否要顯示登入框，以及密碼提示
if ($method === 'GET' && $action === 'auth-status') {
    $blocked = $password === '' && !is_private_ip($_SERVER['REMOTE_ADDR'] ?? '');
    send_json(200, [
        'success' => true,
        'required' => $password !== '',
        'hint' => $password !== '' ? load_hint($hintFile) : '',
        'error' => $blocked ? '尚未設定密碼，只允許區網連線。請在 SYS/password.txt 設定密碼。' : '',
    ]);
}

if ($method === 'POST' && $action === 'login') {
    $body = json_decode((string) file_get_contents('php://input'), true);
    login($password, $failuresFile, is_array($body) ? $body : []);
}

authorize($password);

if ($method === 'GET' && $action === 'config') {
    $config = read_json($configFile);
    send_json(200, ['success' => true, 'baseUrl' => is_string($config['baseUrl'] ?? null) ? $config['baseUrl'] : '']);
}

if ($method !== 'POST') send_json(405, ['success' => false, 'error' => '不支援的請求方法']);

$body = json_decode((string) file_get_contents('php://input'), true);
if (!is_array($body)) send_json(400, ['success' => false, 'error' => 'JSON 格式錯誤']);

if ($action === 'get-links') {
    $baseUrl = is_string($body['baseUrl'] ?? null) ? trim($body['baseUrl']) : '';
    if ($baseUrl === '') send_json(400, ['success' => false, 'error' => '請提供基礎網址']);

    // 記住這次使用的基礎網址，下次開啟網頁時自動帶入
    $config = read_json($configFile);
    if (($config['baseUrl'] ?? null) !== $baseUrl) {
        $config['baseUrl'] = $baseUrl;
        write_json($configFile, $config);
    }

    if (!is_readable($parentDir)) send_json(500, ['success' => false, 'error' => '無法讀取上層目錄']);
    send_json(200, [
        'success' => true,
        'parentDir' => realpath($parentDir),
        'data' => list_entries($parentDir, $sysName, $baseUrl, read_json($notesFile)),
    ]);
}

if ($action === 'save-note') {
    $name = $body['name'] ?? null;
    $note = $body['note'] ?? null;
    if (!is_string($name) || $name === '' || !is_string($note)) {
        send_json(400, ['success' => false, 'error' => '參數錯誤']);
    }
    $notes = read_json($notesFile);
    if (trim($note) !== '') $notes[$name] = $note;
    else unset($notes[$name]);
    write_json($notesFile, $notes);
    send_json(200, ['success' => true]);
}

send_json(404, ['success' => false, 'error' => '找不到此 API']);
