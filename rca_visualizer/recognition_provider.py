import audioop
import json
import subprocess
import wave
from pathlib import Path

from .recognition_state import now_iso
from .recognition_types import RecognitionResult

SHAZAM_LOOKUP_SCRIPT = "/opt/rca-hdmi-visualizer/rca_visualizer/shazam_lookup.py"
SHAZAM_VENV_PYTHON = "/opt/rca-hdmi-visualizer/shazam-venv/bin/python"


def run(cmd, timeout=None):
    return subprocess.run(
        cmd,
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def wav_stats(path):
    with wave.open(str(path), "rb") as wav:
        width = wav.getsampwidth()
        frames = wav.getnframes()
        rate = wav.getframerate()
        total_rms = 0.0
        chunks = 0
        while True:
            data = wav.readframes(rate)
            if not data:
                break
            total_rms += audioop.rms(data, width)
            chunks += 1
    return total_rms / max(chunks, 1), float(frames) / float(rate or 1)


def identify_with_shazam(path):
    if not Path(SHAZAM_VENV_PYTHON).exists() or not Path(SHAZAM_LOOKUP_SCRIPT).exists():
        return RecognitionResult(
            status="error",
            provider="shazam",
            recognized_at=now_iso(),
            message="Shazam recognizer is not installed",
        )
    result = run([SHAZAM_VENV_PYTHON, SHAZAM_LOOKUP_SCRIPT, str(path)], timeout=90)
    if result.returncode != 0:
        return RecognitionResult(
            status="error",
            provider="shazam",
            recognized_at=now_iso(),
            message=(result.stderr.strip() or result.stdout.strip() or "Shazam lookup failed"),
        )
    data = json.loads(result.stdout)
    data["recognized_at"] = now_iso()
    return RecognitionResult(**data)
