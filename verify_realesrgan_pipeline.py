import json
import os
import sys
import time
import traceback

import realesrgan_runner as runner


def _pick_demo_input() -> str:
    candidates = [
        os.path.join("realesrgan_runtime", "ncnn", "pkg_20260406_131158", "onepiece_demo.mp4"),
        os.path.join("realesrgan_runtime", "ncnn", "onepiece_demo.mp4"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("未找到演示视频 onepiece_demo.mp4")


def main() -> int:
    input_path = _pick_demo_input()
    output_path = os.path.abspath("tmp_verify_realesrgan_output.mp4")
    if os.path.isfile(output_path):
        os.remove(output_path)

    options = {
        "model_name": "realesr-animevideov3",
        "outscale": 2,
        "tile": 0,
        "device": "auto",
        "video_backend": "auto",
        "face_enhance": False,
    }
    plan = runner.get_realesrgan_execution_plan(options)
    logs = []
    started_at = time.time()
    try:
        runner.enhance_video(
            input_path,
            output_path,
            options,
            progress_cb=logs.append,
        )
        result = {
            "ok": True,
            "input": os.path.abspath(input_path),
            "output": output_path,
            "output_exists": os.path.isfile(output_path),
            "output_size": os.path.getsize(output_path) if os.path.isfile(output_path) else 0,
            "elapsed_seconds": round(time.time() - started_at, 2),
            "plan": {
                "selected_video_backend": plan.get("selected_video_backend"),
                "selected_encoder": plan.get("selected_encoder"),
                "gpu_vendors": plan.get("gpu_vendors"),
                "system_gpus": plan.get("system_gpus"),
            },
            "log_tail": logs[-20:],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "input": os.path.abspath(input_path),
            "output": output_path,
            "elapsed_seconds": round(time.time() - started_at, 2),
            "plan": {
                "selected_video_backend": plan.get("selected_video_backend"),
                "selected_encoder": plan.get("selected_encoder"),
                "gpu_vendors": plan.get("gpu_vendors"),
                "system_gpus": plan.get("system_gpus"),
            },
            "log_tail": logs[-20:],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
