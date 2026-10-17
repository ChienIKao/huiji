import sys

from huiji import server
from huiji.api import Api


def main() -> None:
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
