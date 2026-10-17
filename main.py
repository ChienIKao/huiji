import sys

from huiji import config, server
from huiji.api import Api


def _redirect_output() -> None:
    """打包成無主控台的 exe 時沒有 stdout／stderr：改寫到 %APPDATA%/Huiji/huiji.log"""
    if sys.stdout is None or sys.stderr is None:
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        log = open(config.APP_DIR / "huiji.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def main() -> None:
    _redirect_output()
    api = Api()

    if "--dev" in sys.argv:
        # 開發模式：不開視窗，用瀏覽器開 http://127.0.0.1:8765/?dev
        import time
        port = server.start(api._meetings_dir, dev_api=api, port=8765)
        print(f"http://127.0.0.1:{port}/?dev", flush=True)
        while True:
            time.sleep(3600)

    import webview

    port = server.start(api._meetings_dir)
    window = webview.create_window("會記 Huiji", f"http://127.0.0.1:{port}/", js_api=api,
                                   width=1280, height=820, min_size=(900, 600), background_color="#202020")
    api._attach(window)

    def on_closing():
        # 關閉視窗時還在錄音：先把檔案寫完，下次開啟會自動接著轉錄
        if api._recorder:
            api.stop_recording(process=False)

    window.events.closing += on_closing
    webview.start(debug="--debug" in sys.argv, private_mode=False)


if __name__ == "__main__":
    main()
