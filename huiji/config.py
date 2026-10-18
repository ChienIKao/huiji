"""設定與路徑：設定檔放在 %APPDATA%\\Huiji\\settings.json"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Huiji"
SETTINGS_FILE = APP_DIR / "settings.json"


@dataclass
class Settings:
    data_dir: str = str(Path.home() / "Documents" / "Huiji")
    whisper_exe: str = r"D:\App\whisper\Faster-Whisper-XXL\faster-whisper-xxl.exe"
    whisper_model: str = "large-v2"
    whisper_language: str = "Chinese"
    multilingual: bool = True  # 逐段偵測語言：中英夾雜時不會漏掉英文句子
    diarize: bool = True
    min_speakers: int = 0
    max_speakers: int = 0
    opencc_exe: str = r"D:\App\whisper\OpenCC\bin\opencc.exe"
    opencc_config: str = r"D:\App\whisper\OpenCC\share\opencc\s2twp.json"
    llm_backend: str = "claude"  # claude | codex | none
    llm_model: str = ""
    mic_device: int = -1  # -1 = 系統預設
    loopback_device: int = -1  # -1 = 預設喇叭的 loopback
    # 錄影來源：{"kind": "monitor", "index": 0}（依 screen.list_monitors() 的順序，0 = 主螢幕）
    # 或 {"kind": "window", "hwnd": …, "title": …, "exe": …}
    screen_source: dict = field(default_factory=lambda: {"kind": "monitor", "index": 0})
    screen_fps: int = 15
    check_updates: bool = True
    glossary: list = field(default_factory=list)

    @property
    def ffmpeg_exe(self) -> str:
        bundled = Path(self.whisper_exe).with_name("ffmpeg.exe")
        if bundled.exists():
            return str(bundled)
        return shutil.which("ffmpeg") or "ffmpeg"

    @property
    def meetings_dir(self) -> Path:
        return Path(self.data_dir) / "meetings"

    @property
    def trash_dir(self) -> Path:
        return Path(self.data_dir) / "trash"

    @property
    def library_file(self) -> Path:
        return APP_DIR / "library.json"


def load() -> Settings:
    s = Settings()
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if "screen_monitor" in data and "screen_source" not in data:  # 0.3 以前只能選螢幕
                data["screen_source"] = {"kind": "monitor", "index": data["screen_monitor"]}
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
    return s


def save(s: Settings) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
