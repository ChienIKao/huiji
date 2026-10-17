# 會記 Huiji

Windows 上的會議紀錄工具：錄下會議（麥克風＋電腦聲音），自動轉成逐字稿、分辨說話者，再用 Claude Code 或 Codex 整理成會議紀錄。

錄音和轉錄都在本機跑，只有逐字稿文字會送給 AI，不會上傳音檔。

介面與會議紀錄的整理方式參考自 macOS 的 [Shiyin 拾音](https://github.com/juinwei7/Shiyin)。

## 功能

- **錄音**：麥克風＋電腦聲音（線上會議）、只錄麥克風（實體會議）、只錄電腦聲音。可以暫停、標記重點（Ctrl+B），也可以邊錄邊記筆記，每換一行會自動加上錄音時間
- **匯入**：音檔或影片（m4a、mp3、wav、mp4、mkv…），可以拿以前用 OBS 錄的檔案
- **轉錄**：[Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win)，逐段偵測語言，中英夾雜也不會漏句；OpenCC 轉成台灣繁體
- **分辨說話者**：pyannote 3.1，點一下說話者就能改名
- **會議紀錄**：摘要、決議、待辦（負責人、期限）、未決問題、議題詳細。每一條都附時間點，點一下就跳到錄音的那一段
- **待確認**：AI 不確定的人名、專有名詞、期限會列出來，回答後 AI 會修正整份紀錄
- **範本**：一般會議、一對一、訪談、課堂／演講、腦力激盪
- **匯出**：會議紀錄 Markdown、逐字稿、SRT 字幕、音檔；也可以直接複製 Markdown
- **管理**：全文搜尋、垃圾桶、播放倍速、詞彙表

## 需求

- Windows 10/11
- Python 3.10 以上
- [Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win)（有 NVIDIA 顯卡會快很多）
- [OpenCC](https://github.com/BYVoid/OpenCC)（選用，用來轉繁體）
- 已登入的 [Claude Code](https://claude.com/claude-code) 或 [Codex CLI](https://github.com/openai/codex)

## 使用

雙擊 `啟動會記.bat`。第一次執行會自動建立 Python 環境並安裝套件。

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
  - `transcript.json`、`transcript.srt`：逐字稿
  - `notes.json`：會議紀錄
- 設定：`%APPDATA%\Huiji\settings.json`

## 開發

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py           # 開視窗
.venv\Scripts\python main.py --debug   # 開視窗＋開發者工具
.venv\Scripts\python main.py --dev     # 不開視窗，用瀏覽器開 http://127.0.0.1:8765/?dev
```

```
main.py              進入點（pywebview 視窗）
huiji/
  recorder.py        WASAPI 錄音（麥克風＋loopback 分軌）
  transcribe.py      ffmpeg 混音 → Faster-Whisper-XXL → OpenCC
  notes.py           提示詞、範本、呼叫 claude / codex CLI、匯出 Markdown
  jobs.py            背景工作佇列（轉錄 → 整理）
  store.py           會議資料夾的讀寫、搜尋、垃圾桶
  api.py             介面呼叫的 API
  server.py          本機 HTTP 伺服器（介面檔案＋音檔串流）
  ui/                介面（HTML／CSS／JS）
```
