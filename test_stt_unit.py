#!/usr/bin/env python3
"""Testes da decodificação de áudio em stt.py — sem modelo, sem rede.

Cobrem o caminho que substituiu o PyAV: WAV lido pela stdlib quando já está em
16 kHz mono, e conversão por ffmpeg quando não está.

    .venv/bin/python test_stt_unit.py
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from stt import read_wav

SAMPLE_RATE = 16000


def write_wav(path: Path, seconds: float, rate: int, channels: int) -> None:
    """Escreve um WAV s16 com um seno, para ter sinal não trivial."""
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
            packed = struct.pack("<h", v)
            frames += packed * channels
        wf.writeframes(bytes(frames))


class TestReadWav(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wav_16k_mono_pela_stdlib(self):
        p = self.tmp / "a.wav"
        write_wav(p, 1.0, SAMPLE_RATE, 1)
        a = read_wav(p)
        self.assertEqual(len(a), SAMPLE_RATE)
        self.assertEqual(str(a.dtype), "float32")
        # sinal normalizado para [-1, 1]
        self.assertLessEqual(float(abs(a).max()), 1.0)
        self.assertGreater(float(abs(a).max()), 0.05)

    def test_wav_48k_estereo_converte_para_16k_mono(self):
        p = self.tmp / "b.wav"
        write_wav(p, 1.0, 48000, 2)
        a = read_wav(p)
        # 1 s a 16 kHz, com folga para o resampler
        self.assertAlmostEqual(len(a) / SAMPLE_RATE, 1.0, delta=0.05)
        self.assertLessEqual(float(abs(a).max()), 1.0)

    def test_arquivo_vazio_nao_explode(self):
        p = self.tmp / "empty.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"")
        a = read_wav(p)
        self.assertEqual(len(a), 0)

    def test_arquivo_inexistente_levanta(self):
        with self.assertRaises(Exception):
            read_wav(self.tmp / "nao_existe.wav")

    def test_nao_e_wav_cai_para_ffmpeg_ou_levanta(self):
        p = self.tmp / "lixo.bin"
        p.write_bytes(b"nao sou audio")
        if shutil.which("ffmpeg"):
            with self.assertRaises(Exception):
                read_wav(p)
        else:
            self.skipTest("sem ffmpeg")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
