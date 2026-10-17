# 會記 Huiji

**Windows 上的 AI 會議紀錄工具。** 按一下開始錄音（也可以連螢幕一起錄），會議結束後自動轉成逐字稿、分辨說話者，再請 Claude 或 Codex 整理出摘要、決議和待辦事項。

錄音、轉錄、分辨說話者都在你的電腦上完成，只有逐字稿文字會送給 AI，音檔和影片不會離開你的電腦。

*A Windows meeting recorder: records mic + system audio (and optionally the screen), transcribes locally with Faster-Whisper, identifies speakers, and turns the transcript into structured meeting notes with Claude Code or Codex. UI in Traditional Chinese.*

[**下載最新版**](../../releases/latest)

![會議紀錄](docs/images/summary.png)

## 為什麼做這個

原本開會的流程是：OBS 錄影 → 用 Whisper 轉逐字稿 → 把逐字稿貼給 AI 整理成紀錄。每一步都要手動，檔案也散在各處。

會記把這整串流程變成一個按鈕。介面與整理會議紀錄的方式參考自 macOS 的 [Shiyin 拾音](https://github.com/juinwei7/Shiyin)，這是 Windows 版的實作。

## 功能

**錄音與匯入**
- 三種錄音來源：線上會議（麥克風＋電腦聲音）、實體會議（只錄麥克風）、只錄電腦聲音
- 可以同時錄螢幕，輸出畫面＋聲音的 `recording.mp4`（有 NVIDIA／Intel 顯示卡會用硬體編碼）
- 錄音中可以暫停、標記重點（Ctrl+B），也可以邊錄邊記筆記，每換一行會自動加上錄音時間
- 匯入以前的錄音或錄影（m4a、mp3、wav、mp4、mkv…），例如 OBS 錄的檔案

**轉錄**
- 使用 [Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win)，逐段偵測語言，中英夾雜的會議不會漏掉英文句子
- 用 OpenCC 轉成台灣繁體用語，中文句子裡的半形標點會改成全形
- 用 pyannote 分辨說話者，點一下就能改名字
- 用「麥克風＋電腦聲音」錄的會議，會比對兩軌的音量，自動把你自己標成「我」
- 詞彙表：人名、產品名、專有名詞會當作轉錄提示，AI 整理時也會照這裡的寫法校正

**會議紀錄**
- 摘要、決議、待辦（負責人、期限）、未決問題、議題詳細
- 每一條都附時間點，點一下就跳到錄音的那一段
- 「下週三」這種說法會依會議日期換算成實際日期
- **待確認**：AI 不確定的人名、專有名詞、負責人、期限會列出來，回答之後 AI 會修正整份紀錄
- 範本：一般會議、一對一、訪談、課堂／演講、腦力激盪

**匯出**
- 會議紀錄（Markdown，可以只要紀錄，也可以附上逐字稿）
- 字幕（SRT）：含說話者名稱的版本，以及給 YouTube 用的版本（一句一行、不含說話者，時間軸跟錄影一致）
- 影片（mp4）、音檔（m4a）

| 逐字稿 | 待確認 |
|---|---|
| ![逐字稿](docs/images/transcript.png) | ![待確認](docs/images/review.png) |

| 首頁 | 設定 |
|---|---|
| ![首頁](docs/images/home.png) | ![設定](docs/images/settings.png) |

## 安裝

### 1. 安裝會記

到 [Releases](../../releases/latest) 下載 `Huiji-Setup-v<版本>.exe` 執行安裝。不需要系統管理員權限，也不需要安裝 Python。

安裝程式沒有數位簽章，Windows SmartScreen 可能會跳出「Windows 已保護您的電腦」，按「其他資訊 → 仍要執行」即可。

不想安裝的話，可以改下載 `Huiji-v<版本>-win64.zip`，解壓縮後直接執行 `Huiji\Huiji.exe`。

### 2. 準備轉錄工具

| 工具 | 用途 | 必要 |
|---|---|---|
| [Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win/releases) | 轉錄、分辨說話者 | 必要 |
| [OpenCC](https://github.com/BYVoid/OpenCC/releases) | 簡轉繁 | 選用（沒有的話逐字稿會是簡體） |
| [Claude Code](https://claude.com/claude-code) 或 [Codex CLI](https://github.com/openai/codex) | 整理會議紀錄 | 選用（沒有的話只產生逐字稿） |

- Faster-Whisper-XXL 下載後解壓縮到任意位置即可。有 NVIDIA 顯示卡會快很多，一小時的會議大約幾分鐘就轉完
- Claude Code 或 Codex 要先在終端機登入過一次，會記會直接使用你登入的帳號

### 3. 指定位置

打開會記，首頁的「環境」會顯示哪些工具已經找到。找不到的話，按旁邊的「設定」，填入 `faster-whisper-xxl.exe` 和 `opencc.exe` 的位置。

第一次轉錄時，Faster-Whisper-XXL 會下載語音模型和分辨說話者的模型，需要等比較久。

## 使用

1. 在首頁選錄音來源，需要的話打開「同時錄製螢幕」，按「開始錄音」
2. 開會。重要的地方按 Ctrl+B 標記，或在右邊的筆記欄記下重點
3. 按「停止並轉錄」，會記會依序合成影片、轉錄、分辨說話者、整理會議紀錄，這段時間可以去做別的事
4. 看「待確認」分頁，回答 AI 不確定的地方，按「依回答修正」
5. 從右上角的「⋯」選單匯出需要的檔案

### 上傳到 YouTube 並加上字幕

錄完後在「⋯」選單選「匯出影片」和「匯出字幕（.srt，YouTube 用）」。上傳影片後，到 YouTube Studio 的「字幕」上傳這個 SRT 檔即可。

### 快捷鍵

| 按鍵 | 功能 |
|---|---|
| Ctrl+Shift+R | 開始／停止錄音 |
| Ctrl+B | 錄音中標記重點 |
| 空白鍵 | 播放／暫停 |

## 資料與隱私

- 錄音、錄影、轉錄、分辨說話者都在本機執行
- 只有逐字稿、你的筆記、詞彙表和會議日期會送給你選擇的 AI（Claude Code 或 Codex），音檔和影片不會上傳
- 會記本身不連線、不收集任何資料

| 內容 | 位置 |
|---|---|
| 會議 | `文件\Huiji\meetings\<日期-時間>\`（可在設定中更改） |
| 設定 | `%APPDATA%\Huiji\settings.json` |
| 錯誤紀錄 | `%APPDATA%\Huiji\huiji.log` |

每場會議的資料夾裡有：

| 檔案 | 內容 |
|---|---|
| `mic.wav`、`system.wav` | 分軌的原始錄音 |
| `audio.m4a` | 混音後的音檔 |
| `recording.mp4` | 螢幕錄影＋聲音（有錄螢幕時） |
| `transcript.json`、`transcript.srt` | 逐字稿 |
| `notes.json` | 會議紀錄 |

## 常見問題

**轉錄很慢？**
沒有 NVIDIA 顯示卡時，Faster-Whisper 只能用 CPU 跑，大模型會很慢。可以到設定把模型改成 `medium` 或 `small`。

**分辨說話者不準？**
如果知道與會人數，可以在設定裡填最少／最多人數。聲音很小或很少說話的人，有時會被併到別人身上。

**沒有自動把我標成「我」？**
這個功能是比對麥克風和電腦聲音兩軌。如果你用喇叭而不是耳機，麥克風會收到對方的聲音，兩軌差異不夠明顯，就不會自動標記。

**英文句子不見了？**
請確認設定裡的「逐段偵測語言」有打開。只指定中文時，Whisper 有時會略過英文句子。

## 開發

需要 Python 3.10 以上。

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py                       # 開視窗
.venv\Scripts\python main.py --debug               # 開視窗＋開發者工具
.venv\Scripts\python main.py --dev                 # 不開視窗，用瀏覽器開 http://127.0.0.1:8765/?dev
.venv\Scripts\python -m unittest discover tests    # 測試
powershell -ExecutionPolicy Bypass -File build.ps1 # 打包成 dist\ 下的 zip 與安裝程式（安裝程式需要 Inno Setup 6）
```

也可以直接雙擊 `啟動會記.bat`，第一次執行會自動建立環境並安裝套件。

### 發布新版本

1. 改 `huiji/__init__.py` 的 `__version__`
2. commit 後打標籤並推送：`git tag v0.3.0 && git push origin main v0.3.0`
3. GitHub Actions 會自動打包，建立附上安裝程式與免安裝版的 Release（標籤要跟 `__version__` 一致，否則會失敗）

### 架構

```
main.py              進入點（pywebview 視窗）
huiji/
  recorder.py        WASAPI 錄音（麥克風＋loopback 分軌）
  screen.py          螢幕錄影（ffmpeg gdigrab）與合成 recording.mp4
  transcribe.py      ffmpeg 混音 → Faster-Whisper-XXL → OpenCC
  selfvoice.py       比對麥克風／電腦聲音兩軌，找出哪個說話者是「我」
  notes.py           提示詞、範本、呼叫 claude / codex CLI、匯出 Markdown
  jobs.py            背景工作佇列（合成影片 → 轉錄 → 整理）
  store.py           會議資料夾的讀寫、搜尋、垃圾桶
  api.py             介面呼叫的 API
  server.py          本機 HTTP 伺服器（介面檔案＋音檔、影片串流）
  ui/                介面（HTML／CSS／JS）
installer/           Inno Setup 安裝程式腳本
```

## 致謝

- [Shiyin 拾音](https://github.com/juinwei7/Shiyin)：介面設計、會議紀錄的提示詞與範本（MIT License，見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）
- [Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win)、[pyannote](https://github.com/pyannote/pyannote-audio)、[OpenCC](https://github.com/BYVoid/OpenCC)、[pywebview](https://github.com/r0x0r/pywebview)、[PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)、[Inno Setup](https://jrsoftware.org/isinfo.php)
