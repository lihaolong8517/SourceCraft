"""冒烟：清空捕获记录后不刷新页面也能重新捕获。

模拟 GUI「清空」按钮的浏览器侧动作：
1. 页面 JS 嗅探器上报一次媒体；
2. 调用 __pro_media_sniffer_clear_captured() 清空页面里的"已上报"缓存；
3. 调用 __pro_media_sniffer_refresh_capture() 重新扫描；
4. 断言同一个 URL 又被上报了一次（不需要刷新页面）。
"""

import asyncio
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMP = ROOT / "scripts" / ".smoke_tmp"
TEMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TEMP)
os.environ["TMP"] = str(TEMP)

CHROME = ROOT / "playwright_browsers" / "chromium-1194" / "chrome-win" / "chrome.exe"

failures = []


def check(name, ok, detail=""):
    print(("[OK]   " if ok else "[FAIL] ") + name + (" | " + str(detail) if detail else ""))
    if not ok:
        failures.append(name)


def main():
    from playwright.sync_api import sync_playwright

    import playwright_browser as pb

    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **kw: Handler(*a, directory=str(TEMP), **kw))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    page_html = TEMP / "clear_test.html"
    page_html.write_text(
        "<!doctype html><html><body>"
        f'<video id="v" src="{base}/clear_clip.mp4" muted></video>'
        "</body></html>",
        encoding="utf-8",
    )

    # 1 MB 的 mp4（SNIFFER_JS 对直链有大小门槛，这里用 fetch 拉全量）
    clip = TEMP / "clear_clip.mp4"
    if not clip.is_file() or clip.stat().st_size < 600 * 1024:
        import subprocess

        subprocess.run(
            [str(ROOT / "ffmpeg.exe"), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=20",
             "-pix_fmt", "yuv420p", "-b:v", "800k", str(clip)],
            capture_output=True,
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context()
        context.expose_binding("reportMedia", lambda source, data: requests.append(dict(data or {})))
        context.add_init_script(pb.SNIFFER_JS)
        page = context.new_page()
        page.goto(f"{base}/clear_test.html", wait_until="load")
        page.evaluate("() => fetch('/clear_clip.mp4').then(r => r.arrayBuffer())")
        page.wait_for_timeout(1200)
        first = len(requests)
        print(f"[INFO] 首次上报 {first} 条")

        # 清空 + 重新扫描（与 GUI 清空按钮的服务端动作一致）
        page.evaluate(
            """() => {
                if (typeof window.__pro_media_sniffer_clear_captured === 'function') {
                    window.__pro_media_sniffer_clear_captured();
                }
            }"""
        )
        page.wait_for_timeout(300)
        refreshed = page.evaluate(
            """() => {
                if (typeof window.__pro_media_sniffer_refresh_capture === 'function') {
                    return window.__pro_media_sniffer_refresh_capture();
                }
                return 'missing';
            }"""
        )
        page.wait_for_timeout(1500)
        second = len(requests) - first
        urls = [r.get("url", "") for r in requests]
        print(f"[INFO] 清空后重新上报 {second} 条, refresh 返回={refreshed}")

        check("页面存在清空/重扫函数", refreshed != "missing", refreshed)
        check("清空后重新捕获到媒体", second > 0, f"新增 {second} 条")
        check("重新捕获包含同一个媒体 URL",
              any("clear_clip.mp4" in u for u in urls[first:]), urls[first:][:3])

        context.close()
        browser.close()

    server.shutdown()
    print("[CLEAR] PASS" if not failures else "[CLEAR] FAIL " + " | ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
