from __future__ import annotations

import struct
import wave
from pathlib import Path

from lib.speech.wav_header import finalize_streaming_wav

UNKNOWN = 0xFFFFFFFF


def _streaming_wav(path: Path, frames: int = 4800, rate: int = 48000) -> None:
    pcm = b"\x01\x00" * frames
    header = (
        b"RIFF"
        + struct.pack("<I", UNKNOWN)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", UNKNOWN)
    )
    path.write_bytes(header + pcm)


def test_fills_unknown_lengths_so_wave_reads_the_real_duration(tmp_path: Path):
    path = tmp_path / "a.wav"
    _streaming_wav(path, frames=4800)

    assert finalize_streaming_wav(path) is True

    with wave.open(str(path)) as reader:
        assert reader.getnframes() == 4800
        assert reader.getnframes() / reader.getframerate() == 0.1


def test_leaves_a_valid_file_untouched(tmp_path: Path):
    path = tmp_path / "ok.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 160)
    before = path.read_bytes()

    assert finalize_streaming_wav(path) is False
    assert path.read_bytes() == before


def test_ignores_non_wav_content(tmp_path: Path):
    path = tmp_path / "x.wav"
    path.write_bytes(b"not a wav file at all")

    assert finalize_streaming_wav(path) is False
    assert path.read_bytes() == b"not a wav file at all"
