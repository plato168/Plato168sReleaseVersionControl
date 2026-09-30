# SYS 核心入口網站

把 `SYS` 資料夾放進任何目錄，它會把 **SYS 的上一層目錄** 裡所有檔案與資料夾列成連結，並可為每一項輸入備註。

```
[網站目錄]/                    例如 C:\xampp\htdocs\myprojects
├── SYS/
│   ├── index.html   前端網頁
│   ├── api.php      後端（Apache + PHP 版）
│   ├── .htaccess    Apache 設定：禁止下載資料檔與密碼檔
│   ├── app.js       後端（Node.js 版，沒有 Apache 時使用）
│   ├── start.bat / start.sh   Node.js 版啟動檔
│   ├── password.txt       自行建立：登入密碼（第一行）
│   ├── password-hint.txt  自行建立：登入畫面顯示的密碼提示（可省略）
│   ├── notes.json   自動產生：備註
│   └── config.json  自動產生：記住的基礎網址
├── Project_A/
└── Project_B/
```

## 部署到 Apache 網站（建議）

需要 Apache 2.4 + PHP 7.4 以上（XAMPP、WampServer、AppServ 都已內建）。

1. 把整個 `SYS` 資料夾複製到網站目錄中，放在要列出的那一層底下，例如 `C:\xampp\htdocs\myprojects\SYS`。
2. 讓 Apache 可以寫入 `SYS` 資料夾（要存備註）。Linux：`sudo chown www-data SYS`（依系統的 Apache 帳號而定）；Windows 的 XAMPP 通常不必調整。
3. 確認該目錄允許 `.htaccess`：`httpd.conf` 中對應的 `<Directory>` 要有 `AllowOverride All`。
4. 瀏覽器開啟 `http://你的網址/myprojects/SYS/`。基礎網址會自動帶入上一層網址（`http://你的網址/myprojects`），按「套用並產生連結」即可。

不需要執行任何程式，Apache 開著就能用。

## 讓網際網路使用者連線

1. **設定密碼（必要）**：在 `SYS` 資料夾建立 `password.txt`，第一行寫密碼。
   - 沒有密碼時，只允許本機與區網（192.168.x.x、10.x.x.x 等）連線，網際網路的連線會被拒絕。
   - 有密碼後，開啟網頁會先出現登入框，輸入密碼才看得到清單與備註。登入後 7 天內同一台電腦不用再輸入，可按右上角「登出」。
   - 要顯示密碼提示，另外建立 `password-hint.txt`，內容就是提示文字（例如「公司分機後三碼」）。**提示會給所有人看到，請勿直接寫出密碼。**
   - 同一個 IP 密碼錯 10 次會封鎖 15 分鐘。
   - 修改 `password.txt` 後，所有人都要重新登入（Node.js 版需重新啟動）。
2. **讓外部連到 Apache**：
   - 在路由器設定「連接埠轉送 (Port Forwarding)」，把外部的 80 / 443 轉到 Apache 主機的區網 IP。
   - 在 Windows 防火牆允許 Apache（`httpd.exe`）通過。
   - 沒有固定 IP 時可申請 DDNS（例如 No-IP、DuckDNS）取得固定網址。
3. **基礎網址改成外部網址**：例如 `http://你的網域/myprojects`，否則外部使用者點連結會連不到。
4. **建議啟用 HTTPS**：使用 `http://` 時，密碼在網路上是明文傳送，可能被攔截。可用 Let's Encrypt（Windows 可用 win-acme，Linux 可用 certbot）替 Apache 申請免費憑證。

注意：這個密碼只保護 SYS 入口網站（清單與備註）。上一層目錄中的專案本身是由 Apache 直接提供，任何知道網址的人都能開啟；若專案也需要保護，請另外用 Apache 的 `AuthType Basic` 設定。

`password.txt`、`password-hint.txt`、`notes.json` 等檔案已由 `.htaccess` 禁止直接下載。

## 沒有 Apache 時：Node.js 版

1. 安裝 Node.js 14 以上。
2. 執行 `start.bat`（Windows）或 `./start.sh`，也可以直接 `node app.js`。
3. 開啟 <http://localhost:3000>，啟動視窗會列出區網其他電腦可用的網址。

| 環境變數 | 預設值 | 說明 |
| --- | --- | --- |
| `PORT` | `3000` | 連接埠 |
| `HOST` | `0.0.0.0` | 允許區網其他電腦連線；只想本機使用請設為 `127.0.0.1` |
| `SYS_PASSWORD` | （無） | 登入密碼；沒設時讀取 `password.txt` |

## 功能

- 每個連結是「基礎網址/名稱」，資料夾結尾加 `/`；中文與空白會自動編碼。
- 備註按 Enter 或點到別處自動儲存，清空即刪除。
- 基礎網址會被記住，下次打開網頁會自動產生連結。
- 列表隱藏 `SYS` 本身與 `.` 開頭的隱藏檔；資料夾排在前面。
- 上方篩選框可依名稱或備註搜尋。
