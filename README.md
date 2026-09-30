# Plato168's Release Version Control

**vcdash：應用程式版本控制儀表板。** 把 `vcdash.py` 放進任何資料夾，它會管理**同一層目錄及其所有子目錄**裡的應用程式，功能如下：

- 找出每個應用程式，查出目前的版本
- 記錄每次的新增、修改、移除、復原
- 保存每個版本的檔案內容，可以隨時比對差異或還原舊版
- 產生 HTML 儀表板報告，以及可用 Excel 開啟的 CSV 修改歷程

只需要 Python 3.8 以上，不必安裝其他套件。

## 快速開始

1. 把 `vcdash.py`（Windows 使用者再加上 `vcdash.bat`）複製到放應用程式的資料夾。
2. 執行掃描：
   - **Windows**：雙擊 `vcdash.bat`。掃描完成後會自動開啟報告。
   - **macOS / Linux**：執行 `./vcdash.sh` 或 `python3 vcdash.py`。
3. 開啟 `vcdash-report/index.html` 查看儀表板。

之後每次修改應用程式，再執行一次就會記錄新版本。

## 指令

| 指令 | 用途 |
| --- | --- |
| `python vcdash.py` | 掃描並產生報告（等同 `scan --report`） |
| `python vcdash.py scan -m "說明"` | 記錄目前的變更，附上說明 |
| `python vcdash.py status` | 只查看尚未記錄的變更，不寫入紀錄 |
| `python vcdash.py list` | 列出所有應用程式、目前版本、修訂次數 |
| `python vcdash.py history 名稱` | 某個應用程式的修改歷程 |
| `python vcdash.py diff 名稱 [A] [B]` | 比對修訂版 A 與 B（預設為最後兩版） |
| `python vcdash.py restore 名稱 修訂號 [--to 資料夾]` | 把舊版檔案還原到 `vcdash-restore/`，不會覆蓋現有檔案 |
| `python vcdash.py report` | 重新產生 HTML 報告 |

所有指令都可以加 `--root 路徑`，改為管理別的資料夾。「名稱」只要輸入路徑的一部分即可，例如 `inventory`。

## 怎樣算一個「應用程式」

- **專案資料夾**：資料夾內有以下任一個標記檔時，整個資料夾（含子目錄）算一個應用程式：`package.json`、`pyproject.toml`、`setup.py`、`requirements.txt`、`Cargo.toml`、`go.mod`、`pom.xml`、`build.gradle`、`*.csproj`、`*.sln`、`composer.json`、`manifest.json`、`VERSION`、`version.txt`、`index.html`。
  - 巢狀的專案資料夾會各自獨立成一個應用程式。
- **單一檔案**：不在專案資料夾內的程式檔，每個檔案各算一個應用程式，例如 `.html`、`.py`、`.js`、`.ps1`、`.bat`、`.exe`、`.apk`、`.jar`、`.xlsm`、`.nc`。
- **不掃描的項目**：`.git`、`node_modules`、隱藏檔、`vcdash` 本身的檔案。

## 版本號從哪裡來

- **專案資料夾**：優先讀取 `package.json`、`pyproject.toml`、`*.csproj`、`pom.xml`、`VERSION` 等檔案裡的版本欄位。
- **單一檔案**：在檔案內容裡找版本號，以下寫法都認得：
  - `__version__ = "1.2.0"`
  - `APP_VERSION = "1.2.0"`
  - `<meta name="version" content="1.2.0">`
  - `@version 1.2.0`
  - `版本：1.2.0`
  - `<title>… v3</title>`
- **找不到版本號**：以修訂號（r1、r2…）區分各版本。

### 版本警告

以下情況會在報告中標示 ⚠：

- 內容改了，但版本號沒有更新
- 版本號倒退
- 原本有版本號，這一版卻找不到

## 自訂設定（選用）

在同一層目錄建立 `vcdash.json`。以 `extra_` 開頭的項目會附加到預設清單；沒有這個前綴的項目會直接取代預設值。

```json
{
  "extra_app_extensions": [".txt"],
  "extra_exclude_dirs": ["backup"],
  "max_blob_bytes": 20971520
}
```

可以設定的項目：

| 項目 | 說明 |
| --- | --- |
| `app_extensions` | 單一檔案應用程式的副檔名 |
| `project_markers` | 專案資料夾的標記檔 |
| `exclude_dirs` / `exclude_files` | 排除的資料夾與檔案 |
| `skip_hidden` | 是否略過隱藏檔 |
| `max_blob_bytes` | 超過這個大小的檔案不保存內容，只比對雜湊，因此無法還原（預設 5 MB） |
| `max_diff_lines` | 報告中每個檔案最多顯示的差異行數 |

## 產生的檔案

| 路徑 | 內容 |
| --- | --- |
| `.vcdash/db.json` | 所有版本紀錄 |
| `.vcdash/objects/` | 各版本檔案內容（gzip 壓縮，相同內容只存一份） |
| `vcdash-report/index.html` | 儀表板，可搜尋、篩選、排序，並列出每一版的差異 |
| `vcdash-report/history.csv` | 完整修改歷程，可用 Excel 開啟 |
| `vcdash-restore/` | `restore` 指令還原出來的舊版檔案 |

> 請備份 `.vcdash/` 資料夾，版本歷史全都在裡面。

## 開發

```sh
python -m unittest discover -s tests -v
```
