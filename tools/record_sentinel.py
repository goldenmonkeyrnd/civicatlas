#!/usr/bin/env python3
"""Capture actual commands in a PTY, then render their recorded terminal output.

Capture is a genuine executed demonstration with reading pauses. Video frames are
rendered from that capture, not invented success messages. No private path is saved.
Optional rendering needs ffmpeg with drawtext and libvpx-vp9.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/evidence"


def execute(command: str, env: dict[str, str], expected: int = 0) -> str:
    master, slave = pty.openpty()
    process = subprocess.Popen(["bash", "-c", command], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave)
    os.close(slave)
    chunks: list[bytes] = []
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    payload = os.read(master, 65536)
                except OSError:
                    break
                if not payload:
                    break
                chunks.append(payload)
            elif process.poll() is not None:
                break
        code = process.wait(timeout=30)
    finally:
        os.close(master)
    output = b"".join(chunks).decode("utf-8", errors="replace").replace("\r\n", "\n")
    if code != expected:
        raise RuntimeError(f"Unexpected exit {code} (wanted {expected}) from {command}: {output}")
    if str(ROOT) in output:
        raise RuntimeError("A private workspace path appeared in captured output")
    return f"$ {command}\n{output}\n[exit {code}]"


def capture() -> dict[str, Any]:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    checker_hash = hashlib.sha256((ROOT / "tools/sentinel.py").read_bytes()).hexdigest()
    pipeline_hash = hashlib.sha256((ROOT / "pipeline/build.py").read_bytes()).hexdigest()
    scenes: list[dict[str, Any]] = []
    cast: list[str] = []
    started = time.monotonic()
    cast.append(json.dumps({"version": 2, "width": 100, "height": 28, "timestamp": int(time.time()), "title": "CivicAtlas Sentinel — actual command execution", "env": {"TERM": "xterm-256color"}}))

    def scene(title: str, content: str, duration: float, note: str) -> None:
        timestamp = round(time.monotonic() - started, 3)
        scenes.append({"title": title, "start": timestamp, "duration": duration, "output": content, "note": note})
        cast.append(json.dumps([timestamp, "o", "\x1b[2J\x1b[H" + title + "\r\n\r\n" + content.replace("\n", "\r\n") + "\r\n\r\n" + note]))
        print(f"Captured: {title}", flush=True)
        time.sleep(duration)

    with tempfile.TemporaryDirectory(prefix="civicatlas-sentinel-demo-") as temporary:
        demo = Path(temporary)
        env = {**os.environ, "DEMO": str(demo), "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
        fixture_root = ROOT / "tests/python/fixtures/sentinel"
        scene("I use AI. I check its work.", "CivicAtlas / bounded static-analysis demonstration\n\nActual Python commands, captured in a terminal.\nTwo checks: local function references and required-validator reachability.\n\nThe examples below are deliberate failures in temporary fixture copies.\nThe production pipeline stays unchanged.", 8, "Static reachability is evidence about code structure, not runtime correctness.")
        for filename in ("app.py", "checks.py"):
            shutil.copy(fixture_root / "malformed" / filename, demo / filename)
        command = 'python tools/sentinel.py --root "$DEMO" --entry app:main --required checks:validate_records'
        bad_output = execute(command, env, expected=1)
        scene("1 / A malformed function reference fails", bad_output, 10, "validateRecords was imported; the module actually defines validate_records.")
        original = (demo / "app.py").read_text()
        fixed = original.replace("validateRecords", "validate_records")
        (demo / "app.py").write_text(fixed)
        diff = "".join(difflib.unified_diff(original.splitlines(True), fixed.splitlines(True), fromfile="app.py (before)", tofile="app.py (after)"))
        scene("2 / Fix the binding, then run the same check", diff + "\n" + execute(command, env), 10, "The repaired local import and direct call now agree.")
        for filename in ("app.py", "checks.py"):
            shutil.copy(fixture_root / "disconnected" / filename, demo / filename)
        command = 'python tools/sentinel.py --root "$DEMO" --entry app:main --required checks:validate'
        scene("3 / A defined validator can still be disconnected", execute(command, env, expected=1), 9, "An import is not a production call path; test-only references do not count.")
        (demo / "app.py").write_text('from checks import validate\n\ndef main():\n    validate()\n    return "validated demonstration"\n')
        scene("4 / Connect the production path", execute(command, env), 8, "This establishes a supported static call path. It does not prove every branch runs.")
        for filename in ("app.py", "checks.py"):
            shutil.copy(fixture_root / "dynamic" / filename, demo / filename)
        scene("5 / Dynamic lookup is unresolved", execute(command, env, expected=2), 10, "Unknown constructs never receive a reassuring PASS.")
        scene("6 / Check the real CivicAtlas pipeline", execute('python tools/sentinel.py', env) + "\n" + execute('python -m pipeline.build --check', env), 10, "The offline rebuild verifies source hashes and reproduces the published artifacts.")
        scene("7 / Run the acceptance controls", execute('python -m unittest discover -s tests/python -q', env), 9, "Suppression, missing values, tampering, geometry and deliberate AST failures are covered.")
        scene("Keep the claim as precise as the check.", "PASS means the supported local-function graph resolves\nand reaches the declared required validator.\n\nThird-party libraries and object methods are outside this checker.\nRuntime execution and data invariants are verified separately.\n\nPython + DuckDB → Parquet + D1 import artifacts\n24 Maryland jurisdictions / official 2024 sources", 8, "Pauline identified the engineering problem and directed agent-assisted investigation.")
    if checker_hash != hashlib.sha256((ROOT / "tools/sentinel.py").read_bytes()).hexdigest() or pipeline_hash != hashlib.sha256((ROOT / "pipeline/build.py").read_bytes()).hexdigest():
        raise RuntimeError("Source changed during the capture; record the complete demonstration again")
    structured_run = subprocess.run([sys.executable, "tools/sentinel.py", "--json"], cwd=ROOT, capture_output=True, text=True, check=True)
    (EVIDENCE / "sentinel-report.json").write_text(structured_run.stdout)
    duration = round(time.monotonic() - started, 3)
    cast.append(json.dumps([duration, "o", "\r\nEnd of captured demonstration.\r\n"]))
    (EVIDENCE / "sentinel.cast").write_text("\n".join(cast) + "\n")
    snapshot = json.loads((ROOT / "data/processed/snapshot.json").read_text())
    report = {"format_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "duration_seconds": duration, "dataset_revision": snapshot["dataset_revision"], "checker_sha256": checker_hash, "pipeline_sha256": pipeline_hash, "scenes": scenes, "capture": "Actual bash commands captured through a PTY, with deliberate pauses for reading. Video is rendered from this recording."}
    (EVIDENCE / "sentinel-recording.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    transcript = ["# Sentinel: recorded terminal demonstration", "", report["capture"], "", f"Dataset revision: `{report['dataset_revision']}`.", "", "`$DEMO` refers to a temporary copy of the public test fixtures. The demonstration repairs those copies; it does not modify the production pipeline.", ""]
    for part in scenes:
        transcript.extend([f"## {part['title']}", "", part["note"], "", "```text", part["output"], "```", ""])
    (EVIDENCE / "sentinel-transcript.md").write_text("\n".join(transcript))
    return report


def render(report: dict[str, Any]) -> None:
    # Render real captured output. Reading pauses are retained; results are not fabricated.
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    if not font.exists() or not shutil.which("ffmpeg"):
        raise RuntimeError("Video rendering needs ffmpeg and DejaVuSansMono.ttf; the cast/transcript remain usable")
    with tempfile.TemporaryDirectory(prefix="civicatlas-video-") as temporary:
        working = Path(temporary)
        clips: list[Path] = []
        captions: list[str] = ["WEBVTT", ""]
        cumulative = 0.0
        for index, part in enumerate(report["scenes"]):
            content = part["output"]
            lines: list[str] = []
            for line in content.splitlines():
                lines.extend(textwrap.wrap(line, width=94, replace_whitespace=False, drop_whitespace=False) or [""])
            if len(lines) > 21:
                raise ValueError(f"Scene {part['title']} exceeds the readable terminal frame")
            title = working / f"title-{index}.txt"
            text = working / f"output-{index}.txt"
            note = working / f"note-{index}.txt"
            title.write_text(part["title"])
            text.write_text("\n".join(lines))
            note.write_text("\n".join(textwrap.wrap(part["note"], width=102)))
            clip = working / f"clip-{index}.webm"
            filters = f"drawbox=x=24:y=86:w=1232:h=542:color=0x17232A:t=fill,drawtext=fontfile={font}:textfile={title}:expansion=none:fontcolor=0xD99532:fontsize=28:x=44:y=28,drawtext=fontfile={font}:textfile={text}:expansion=none:fontcolor=0xF5EBDD:fontsize=20:line_spacing=4:x=44:y=108,drawtext=fontfile={font}:textfile={note}:expansion=none:fontcolor=0x83C8E2:fontsize=18:line_spacing=3:x=44:y=650"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x202B33:s=1280x720:r=12", "-t", str(part["duration"]), "-vf", filters, "-c:v", "libvpx-vp9", "-threads", "2", "-cpu-used", "4", "-b:v", "0", "-crf", "36", "-pix_fmt", "yuv420p", str(clip)], check=True)
            clips.append(clip)
            start = cumulative
            cumulative += part["duration"]
            captions.extend([f"{vtt_time(start)} --> {vtt_time(cumulative)}", part["title"] + ". " + part["note"], ""])
        listing = working / "clips.txt"
        listing.write_text("".join(f"file '{clip}'\n" for clip in clips))
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(EVIDENCE / "sentinel.webm")], check=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(EVIDENCE / "sentinel.webm"), "-frames:v", "1", str(EVIDENCE / "sentinel-poster.webp")], check=True)
        (EVIDENCE / "sentinel-captions.vtt").write_text("\n".join(captions))
    print(f"Rendered {cumulative:.0f}s video from captured command output.")


def vtt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds_, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_:02}.{milliseconds:03}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-only", action="store_true", help="Render the existing captured output without executing it again")
    args = parser.parse_args()
    report = json.loads((EVIDENCE / "sentinel-recording.json").read_text()) if args.render_only else capture()
    render(report)


if __name__ == "__main__":
    main()
