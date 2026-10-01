"""一次性语音识别：对一个或多个媒体文件逐句输出起止时间，结果以 JSON 打印到 stdout。

由 ArcReel 在检测配音开始时间时以子进程方式调用：模型只在本进程存活期间占用内存，批量文件共用一次加载，
进程退出即释放。日志与进度写 stderr，stdout 只有最终 JSON。

    uv run python detect.py --model large-v3-turbo --language zh a.mp4 b.mp4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"

#: 引导 Whisper 输出简体中文（不给提示时中文常以繁体输出，和剧本比对会大面积对不上）。
INITIAL_PROMPTS = {"zh": "以下是普通话的句子。"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    started = time.monotonic()
    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        download_root=str(MODELS_DIR),
    )
    print(f"model loaded in {time.monotonic() - started:.1f}s", file=sys.stderr)

    results: dict[str, object] = {}
    for file in args.files:
        began = time.monotonic()
        try:
            segments, info = model.transcribe(
                file,
                language=args.language,
                vad_filter=True,
                condition_on_previous_text=False,
                beam_size=5,
                # 多句对白常被合并成一个长分段，只有词级时间才能分辨句与句之间的停顿
                word_timestamps=True,
                initial_prompt=INITIAL_PROMPTS.get(args.language),
            )
            results[file] = {
                "duration": info.duration,
                "language": info.language,
                "segments": [
                    {
                        "start": round(s.start, 3),
                        "end": round(s.end, 3),
                        "text": s.text.strip(),
                        "words": [
                            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word}
                            for w in (s.words or [])
                        ],
                    }
                    for s in segments
                ],
            }
        except Exception as exc:  # 单个文件失败不拖垮整批
            results[file] = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"{Path(file).name} done in {time.monotonic() - began:.1f}s", file=sys.stderr)

    print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
