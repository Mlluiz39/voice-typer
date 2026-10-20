#!/usr/bin/env python3
"""bench.py — mede latência de transcrição por modelo, com números reais.

Passa o áudio como numpy (ver stt.py), evitando o caminho quebrado do PyAV.

RTF = tempo_de_processamento / duração_do_áudio
  < 1,0  → mais rápido que tempo real (ditado utilizável)
  ~ 0,3  → 10 s de fala resolvidos em ~3 s

Uso:
    .venv/bin/python bench.py --audio amostra.wav --models small,large-v3-turbo
    .venv/bin/python bench.py --audio amostra.wav --models small --beams 1,5

Primeira execução de cada modelo faz download (small ~250 MB, large-v3-turbo ~1,6 GB).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stt import Transcriber, read_wav


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Benchmark de transcrição local.")
    ap.add_argument("--audio", required=True, help="arquivo de áudio (WAV 16k mono ideal)")
    ap.add_argument("--models", default="small", help="lista separada por vírgula")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--beams", default="5", help="beam sizes a testar")
    ap.add_argument("--language", default="pt")
    ap.add_argument("--no-vad", action="store_true")
    ap.add_argument("--threads", type=int, default=0)
    args = ap.parse_args(argv)

    audio = Path(args.audio).expanduser()
    if not audio.exists():
        print(f"arquivo não encontrado: {audio}", file=sys.stderr)
        return 1

    samples = read_wav(audio)
    dur = len(samples) / 16000
    print(f"amostra: {audio}  ({dur:.2f}s)")
    print(f"compute_type: {args.compute_type}  idioma: {args.language}\n")

    header = f"{'modelo':22} {'beam':>4} {'carga':>7} {'tempo':>7} {'RTF':>6}  texto"
    print(header)
    print("-" * len(header))

    for nome in [m.strip() for m in args.models.split(",") if m.strip()]:
        for beam in [int(b) for b in args.beams.split(",") if b.strip()]:
            try:
                t = Transcriber(
                    nome,
                    compute_type=args.compute_type,
                    language=args.language,
                    beam_size=beam,
                    cpu_threads=args.threads,
                    vad=not args.no_vad,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{nome:22} {beam:>4}  falha: {exc}")
                continue
            r = t.transcribe_samples(samples, audio_s=dur)
            print(f"{nome:22} {beam:>4} {t.load_s:>6.2f}s {r.elapsed_s:>6.2f}s {r.rtf:>6.2f}  {r.text}")

    print("\nRTF abaixo de 1,0 significa mais rápido que tempo real.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
