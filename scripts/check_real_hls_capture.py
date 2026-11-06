# -*- coding: utf-8 -*-
"""真实网络验证：用 VDH 式捕获层抓一个公网 HLS（中间分片预览 + 清单富化 + 请求头）。

用法（PowerShell）：
    $env:TEMP='E:\\1（开发的视频下载器\\scripts\\.smoke_tmp'
    python scripts\\check_real_hls_capture.py
"""
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HLS_URL = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
CHROME = os.path.join(
    ROOT, "playwright_browsers", "chromium-1194", "chrome-win", "chrome.exe"
)

emitted = []


def emit(candidate, force_update=False):
    emitted.append(dict(candidate))
    print(f"[REAL] emit {candidate.get('type')} {candidate.get('url')} "
          f"force={force_update} res={((candidate.get('quality') or {}).get('resolution'))}")


async def main():
    from playwright.async_api import async_playwright
    from vdh_capture import CaptureRunner

    failures = []
    async with async_playwright() as p:
        launch_kwargs = {"headless": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
        if os.path.exists(CHROME):
            launch_kwargs["executable_path"] = CHROME
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        page = await context.new_page()
        runner = CaptureRunner(emit=emit)
        await runner.start(context, page, HLS_URL)
        try:
            # 直接在页面里 fetch 清单：m3u8 的响应头会走完整的 CDP 网络栈，
            # 不会像 page.goto() 那样变成文件下载。
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
            text_len = await page.evaluate(
                "url => fetch(url).then(r => r.text()).then(t => t.length)", HLS_URL
            )
            print(f"[REAL] fetch m3u8 text length={text_len}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"fetch m3u8: {exc}")
        await runner.after_navigation(page)
        await asyncio.sleep(4)
        await runner.stop()
        await browser.close()

    hls = [c for c in emitted if (c.get("type") or "").upper() in ("HLS", "M3U8")
           or ".m3u8" in str(c.get("url") or "").lower()]
    if not hls:
        failures.append("没有捕获到 HLS 候选")
    else:
        best = hls[-1]
        quality = best.get("quality") or {}
        manifest = best.get("quality", {}).get("manifest") if isinstance(quality, dict) else None
        variant_count = 0
        if isinstance(quality, dict) and isinstance(quality.get("manifest"), dict):
            variant_count = len(quality["manifest"].get("variants") or [])
        print(f"[REAL] HLS 候选 {len(hls)} 个，最后一个 url={best.get('url')}")
        print(f"[REAL] resolution={quality.get('resolution')} variants={variant_count} "
              f"headers={sorted((best.get('headers') or {}).keys())}")
        if not best.get("headers"):
            failures.append("HLS 候选没有请求头")
        if not quality.get("resolution") and not variant_count:
            failures.append("清单没有富化出分辨率/变体")

    if failures:
        print("[REAL] FAIL " + " | ".join(failures))
        return 1
    print(f"[REAL] PASS emitted={len(emitted)}")
    print("[REAL] 样例: " + json.dumps(emitted[-1], ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
