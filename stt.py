#!/usr/bin/env python3
"""stt.py — transcrição local com faster-whisper, sem PyAV no caminho.

Motivo de não usar `transcribe("arquivo.wav")`: nesse caminho o faster-whisper
chama `av.open(..., metadata_errors="ignore")`, e o PyAV 19 removeu esse
parâmetro. Versões antigas do PyAV, que ainda o aceitam, não têm wheel para
Python 3.14. Então decodificamos nós mesmos e passamos um `numpy.ndarray`
float32 mono 16 kHz — que é o formato que o Whisper consome de qualquer forma.

Isso também é o desenho certo para o produto: nós gravamos o áudio, então
nenhuma dependência externa decide o formato.

Uso como biblioteca:
    from stt import Transcriber
    t = Transcriber("small")
    texto, info = t.transcribe_file("amostra.wav")
"""

from __future__ import annotations

import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000

# Medido: sem prompt, o `small` truncou a última palavra ("pontua...") numa
# amostra de 11,8 s. Com um prompt genérico em português a transcrição saiu
# completa. Um prompt carregado de contexto específico piorou o resultado —
# então o padrão é deliberadamente genérico.
DEFAULT_PROMPT = "Ditado em português do Brasil, com pontuação e acentuação corretas."

# Medido neste i5-1135G7 (4 núcleos físicos, desktop carregado):
#   2 threads: 2,72 s   4 threads: 2,44 s   8 threads: 3,02 s
# Oversubscrição piora: as threads lógicas disputam CPU com o desktop.
DEFAULT_THREADS = 4


@dataclass
class Result:
    text: str
    audio_s: float
    elapsed_s: float
    language: str
    language_probability: float

    @property
    def rtf(self) -> float:
        """Fator de tempo real: < 1,0 é mais rápido que tempo real."""
        return self.elapsed_s / self.audio_s if self.audio_s > 0 else float("nan")


def read_wav(path: str | Path) -> "object":
    """Lê um WAV como float32 mono 16 kHz.

    Usa a stdlib quando o arquivo já está no formato esperado (o caso do nosso
    `record.py`) e cai para o ffmpeg quando precisa converter.
    """
    import numpy as np

    path = Path(path)
    try:
        with wave.open(str(path), "rb") as wf:
            if (
                wf.getframerate() == SAMPLE_RATE
                and wf.getnchannels() == 1
                and wf.getsampwidth() == 2
            ):
                frames = wf.readframes(wf.getnframes())
                return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except (wave.Error, EOFError, OSError):
        pass

    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            f"não consegui ler {path} com a stdlib e não há ffmpeg para converter"
        )
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(path),
            "-f", "f32le", "-acodec", "pcm_f32le",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


class Transcriber:
    """Carrega o modelo uma vez e transcreve várias amostras."""

    def __init__(
        self,
        model_size: str = "small",
        compute_type: str = "int8",
        language: str = "pt",
        beam_size: int = 5,
        cpu_threads: int = DEFAULT_THREADS,
        vad: bool = True,
        initial_prompt: str | None = DEFAULT_PROMPT,
        hotwords: list[str] | None = None,
    ) -> None:
        from faster_whisper import WhisperModel

        self.language = language
        self.beam_size = beam_size
        self.vad = vad
        self.initial_prompt = initial_prompt
        # hotwords enviesa o decodificador para um vocabulário; diferente do
        # initial_prompt, não tende a vazar para o texto final.
        self.hotwords = " ".join(hotwords) if hotwords else None
        self.model_size = model_size
        t0 = time.perf_counter()
        self.model = WhisperModel(
            model_size,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=1,
        )
        self.load_s = time.perf_counter() - t0

    def transcribe_samples(self, samples, *, audio_s: float | None = None) -> Result:
        import numpy as np

        samples = np.asarray(samples, dtype=np.float32)
        dur = audio_s if audio_s is not None else len(samples) / SAMPLE_RATE

        t0 = time.perf_counter()
        segments, info = self.model.transcribe(
            samples,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
            hotwords=self.hotwords,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        elapsed = time.perf_counter() - t0

        return Result(
            text=text,
            audio_s=dur,
            elapsed_s=elapsed,
            language=getattr(info, "language", self.language),
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        )

    def transcribe_file(self, path: str | Path) -> Result:
        samples = read_wav(path)
        dur = len(samples) / SAMPLE_RATE
        return self.transcribe_samples(samples, audio_s=dur)

    @property
    def model_id(self) -> str:
        return self.model_size
