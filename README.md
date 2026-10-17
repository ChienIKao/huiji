# 會記 Huiji

Windows 上的會議紀錄工具：錄下會議（麥克風＋電腦聲音，也可以同時錄螢幕），自動轉成逐字稿、分辨說話者，再用 Claude Code 或 Codex 整理成會議紀錄。

錄音和轉錄都在本機跑，只有逐字稿文字會送給 AI，不會上傳音檔。

介面與會議紀錄的整理方式參考自 macOS 的 [Shiyin 拾音](https://github.com/juinwei7/Shiyin)。

## 功能

- **錄音**：麥克風＋電腦聲音（線上會議）、只錄麥克風（實體會議）、只錄電腦聲音。可以暫停、標記重點（Ctrl+B），也可以邊錄邊記筆記，每換一行會自動加上錄音時間
- **螢幕錄影**：在錄音按鈕旁的選單勾選「同時錄製螢幕」，就會連畫面一起錄成 `recording.mp4`（有 NVIDIA／Intel 顯示卡會用硬體編碼）。在設定裡選要錄哪個螢幕和影格率
- **YouTube 字幕**：匯出「字幕（.srt，YouTube 用）」，不含說話者名稱、一句一行，跟錄影的時間軸一致，可以直接上傳成 CC 字幕
- **匯入**：音檔或影片（m4a、mp3、wav、mp4、mkv…），可以拿以前用 OBS 錄的檔案
- **轉錄**：[Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win)，逐段偵測語言，中英夾雜也不會漏句；OpenCC 轉成台灣繁體
- **分辨說話者**：pyannote 3.1，點一下說話者就能改名。用「麥克風＋電腦聲音」錄的會議，會比對兩軌音量，自動把你自己標成「我」（AI 整理待辦時就知道哪些是你的）
- **會議紀錄**：摘要、決議、待辦（負責人、期限）、未決問題、議題詳細。每一條都附時間點，點一下就跳到錄音的那一段
- **待確認**：AI 不確定的人名、專有名詞、期限會列出來，回答後 AI 會修正整份紀錄
- **範本**：一般會議、一對一、訪談、課堂／演講、腦力激盪
- **匯出**：會議紀錄 Markdown、逐字稿、SRT 字幕、影片、音檔；也可以直接複製 Markdown
- **管理**：全文搜尋、垃圾桶、播放倍速、詞彙表

## 需求

- Windows 10/11
- [Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win)（有 NVIDIA 顯卡會快很多）
- [OpenCC](https://github.com/BYVoid/OpenCC)（選用，用來轉繁體）
- 已登入的 [Claude Code](https://claude.com/claude-code) 或 [Codex CLI](https://github.com/openai/codex)

## 安裝

到 [Releases](../../releases) 下載 `Huiji-Setup-v<版本>.exe` 執行安裝，不需要系統管理員權限，也不需要安裝 Python。安裝完成後，開始功能表和桌面會出現「會記 Huiji」，要移除時到「設定 → 應用程式」解除安裝，會議資料不會被刪除。

安裝程式沒有數位簽章，第一次執行時 Windows SmartScreen 可能會擋下，按「其他資訊 → 仍要執行」即可。

不想安裝的話，也可以下載 `Huiji-v<版本>-win64.zip`，解壓縮後直接執行 `Huiji\Huiji.exe`。

想直接從原始碼執行的話，先裝好 Python 3.10 以上，再雙擊 `啟動會記.bat`。第一次執行會自動建立 Python 環境並安裝套件。

## 使用

第一次開啟後，到右上角的設定確認：

- Faster-Whisper-XXL 的 exe 位置（預設 `D:\App\whisper\Faster-Whisper-XXL\faster-whisper-xxl.exe`）
- OpenCC 的位置
- AI 用 Claude Code 還是 Codex

第一次轉錄時，Faster-Whisper-XXL 會下載分辨說話者的模型，會比較久。

### 快捷鍵

| 按鍵 | 功能 |
|---|---|
| Ctrl+Shift+R | 開始／停止錄音 |
| Ctrl+B | 錄音中標記重點 |
| 空白鍵 | 播放／暫停 |

## 資料存放位置

- 會議：`文件\Huiji\meetings\<日期-時間>\`（可在設定中更改）
  - `mic.wav`、`system.wav`：分軌的原始錄音
  - `audio.m4a`：混音後的音檔
  - `recording.mp4`：螢幕錄影＋聲音（有錄螢幕時）
  - `transcript.json`、`transcript.srt`：逐字稿
  - `notes.json`：會議紀錄
- 設定：`%APPDATA%\Huiji\settings.json`
- 錯誤紀錄（exe 版）：`%APPDATA%\Huiji\huiji.log`

## 開發

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py           # 開視窗
.venv\Scripts\python main.py --debug   # 開視窗＋開發者工具
.venv\Scripts\python main.py --dev     # 不開視窗，用瀏覽器開 http://127.0.0.1:8765/?dev
.venv\Scripts\python -m unittest discover tests   # 測試
powershell -ExecutionPolicy Bypass -File build.ps1   # 打包成 dist\ 下的 zip 與安裝程式（安裝程式需要 Inno Setup 6）
```

### 發布新版本

1. 改 `huiji/__init__.py` 的 `__version__`
2. commit 後打標籤並推送：`git tag v0.1.0 && git push origin main v0.1.0`
3. GitHub Actions 會自動打包並建立 Release（標籤要跟 `__version__` 一致，否則會失敗）

```
main.py              進入點（pywebview 視窗）
huiji/
  recorder.py        WASAPI 錄音（麥克風＋loopback 分軌）
  transcribe.py      ffmpeg 混音 → Faster-Whisper-XXL → OpenCC
  notes.py           提示詞、範本、呼叫 claude / codex CLI、匯出 Markdown
  screen.py          螢幕錄影（ffmpeg gdigrab）與合成 recording.mp4
  selfvoice.py       比對麥克風／電腦聲音兩軌，找出哪個說話者是「我」
  jobs.py            背景工作佇列（轉錄 → 整理）
  store.py           會議資料夾的讀寫、搜尋、垃圾桶
  api.py             介面呼叫的 API
  server.py          本機 HTTP 伺服器（介面檔案＋音檔串流）
  ui/                介面（HTML／CSS／JS）
```
