#!/usr/bin/env python3
"""record.py — grava do microfone padrão em WAV mono 16 kHz.

Usável como CLI e como biblioteca (`Recorder`, para o daemon).

Duas decisões que vieram de medição, não de suposição:

1. **Descarta os primeiros 200 ms.** Placas USB baratas emitem um transiente
   (pop) quando o stream de captura abre — medido aqui com pico em -0,21 dB
   seguido de silêncio a -64 dB. Esse pico, além de disparar alarme falso de
   saturação, entra no áudio e pode confundir a transcrição.
2. **Grava em PCM cru e só depois empacota em WAV.** Se o gravador for
   interrompido, o cabeçalho WAV fica com tamanho errado; reinterpretar o arquivo
   como cru acabaria lendo o cabeçalho como se fosse áudio.

Uso:
    python3 record.py --seconds 10 --out amostra.wav
    python3 record.py --seconds 15 --out amostra.wav --device <node>
    python3 record.py --seconds 10 --out a.wav --trim 0.35
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RATE = 16000
CHANNELS = 1
DEFAULT_TRIM = 0.2

# Abaixo disto o áudio é mudo e o Whisper tende a alucinar.
SILENT_PEAK_DB = -50.0
# Saturação real exige pico no teto E energia alta: um transiente isolado não é
# saturação, é pop de abertura de stream.
CLIP_PEAK_DB = -1.0
CLIP_RMS_DB = -30.0


def pick_recorder() -> tuple[list[str], str]:
    """Gravador disponível, com o caminho de saída como último argumento.

    Ordem medida neste ambiente: `pw-record` e `arecord` escrevem corretamente;
    `parec` retornou 0 bytes tanto em stdout quanto em arquivo, então fica por
    último.
    """
    if shutil.which("pw-record"):
        return ["pw-record", "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "s16"], "pw-record"
    if shutil.which("arecord"):
        return ["arecord", "-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw"], "arecord"
    if shutil.which("parec"):
        return ["parec", "--format=s16le", f"--rate={RATE}", f"--channels={CHANNELS}", "--file-format=raw"], "parec"
    raise RuntimeError("nenhum gravador encontrado (pw-record, arecord ou parec)")


class Recorder:
    """Captura sob demanda: `start()` abre o stream, `stop()` fecha e devolve WAV."""

    def __init__(self, device: str | None = None, trim: float = DEFAULT_TRIM) -> None:
        self.trim = trim
        self.base, self.name = pick_recorder()
        if device:
            if self.name == "pw-record":
                self.base += ["--target", device]
            elif self.name == "arecord":
                self.base += ["-D", device]
            else:
                self.base += [f"--device={device}"]
        self._proc: subprocess.Popen | None = None
        self._raw: Path | None = None

    def start(self) -> None:
        if self._proc is not None:
            return
        self._raw = Path(tempfile.mkstemp(suffix=".raw", prefix="vt-rec-")[1])
        self._proc = subprocess.Popen(
            self.base + [str(self._raw)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self, out_wav: str | Path) -> Path:
        """Encerra a captura e empacota em WAV. Devolve o caminho do WAV."""
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        raw, self._raw = self._raw, None
        out = Path(out_wav)
        if raw is None or not raw.exists():
            raise RuntimeError("captura não produziu arquivo")
        try:
            if raw.stat().st_size == 0:
                raise RuntimeError("captura vazia (microfone inativo?)")
            if not to_wav(raw, out, self.trim):
                raise RuntimeError("falha ao converter a captura para WAV")
        finally:
            raw.unlink(missing_ok=True)
        return out

    def cancel(self) -> None:
        """Aborta a captura sem produzir WAV."""
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._raw is not None:
            self._raw.unlink(missing_ok=True)
            self._raw = None


def to_wav(raw_path: Path, wav_path: Path, trim: float) -> bool:
    """Empacota o PCM cru em WAV, descartando o transiente inicial."""
    if not shutil.which("ffmpeg"):
        return False
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS),
            "-i", str(raw_path),
            "-ss", str(trim),
            "-c:a", "pcm_s16le", str(wav_path),
        ],
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 44


def clip_stats(path: str | Path) -> tuple[int, int]:
    """Conta amostras em fundo de escala e sequências contíguas de clipping.

    Só o pico não distingue saturação real de um transiente isolado: um único
    pico em 0 dB e um trecho estourado produzem o mesmo `max_volume`. Contar
    corridas de amostras no teto separa os dois. Implementado com a stdlib para
    funcionar fora do venv.
    """
    import wave

    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2:
                return 0, 0
            bruto = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError, OSError):
        return 0, 0

    import array

    amostras = array.array("h")
    amostras.frombytes(bruto)
    no_teto = 0
    corridas = 0
    em_corrida = False
    seguidos = 0
    for v in amostras:
        if v >= 32700 or v <= -32700:
            no_teto += 1
            seguidos += 1
            if not em_corrida and seguidos >= 3:
                em_corrida = True
                corridas += 1
        else:
            seguidos = 0
            em_corrida = False
    return no_teto, corridas


def levels(path: Path) -> tuple[float | None, float | None]:
    """Pico e RMS em dB, via ffmpeg astats."""
    if not shutil.which("ffmpeg"):
        return None, None
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "astats", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False,
    )
    peak = rms = None
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if peak is None and "Peak level dB:" in line:
            try:
                peak = float(line.split("Peak level dB:")[1].strip())
            except ValueError:
                pass
        elif rms is None and "RMS level dB:" in line:
            try:
                rms = float(line.split("RMS level dB:")[1].strip())
            except ValueError:
                pass
    return peak, rms


def classify(peak: float | None, rms: float | None, corridas: int = 0) -> str:
    """'mudo', 'saturado' ou 'ok'.

    `corridas` é o número de sequências de 3+ amostras em fundo de escala, medido
    por `clip_stats`. Quando disponível, é o critério decisivo: pico alto SEM
    corridas é transiente (pop de abertura), não saturação.
    """
    if peak is None:
        return "ok"
    if peak < SILENT_PEAK_DB:
        return "mudo"
    # Corridas de 3+ amostras no teto são a assinatura física de saturação.
    # Pico alto sozinho não basta: transiente de abertura de stream também
    # encosta no teto, e no início esse alarme era um falso positivo.
    if corridas > 0:
        return "saturado"
    return "ok"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Grava do microfone em WAV 16 kHz mono.")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", default="amostra.wav")
    ap.add_argument("--device", help="nó de origem (padrão: o do sistema)")
    ap.add_argument("--trim", type=float, default=DEFAULT_TRIM,
                    help=f"segundos descartados do início (padrão {DEFAULT_TRIM})")
    args = ap.parse_args(argv)

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    rec = Recorder(device=args.device, trim=args.trim)
    print(f"gravando {args.seconds:g}s com {rec.name}")
    print("fale agora…")
    rec.start()
    import time

    time.sleep(args.seconds)
    try:
        rec.stop(out)
    except RuntimeError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    dur = max(0.0, (out.stat().st_size - 44) / (RATE * 2))
    peak, rms = levels(out)
    no_teto, corridas = clip_stats(out)
    print(f"pronto: {out}  ({dur:.1f}s, {out.stat().st_size / 1024:.0f} KB)")
    estado = classify(peak, rms, corridas)
    if peak is not None and rms is not None:
        print(f"pico: {peak:.1f} dB   RMS: {rms:.1f} dB")
        print(f"amostras no teto: {no_teto}   sequências de clipping: {corridas}")
    if estado == "mudo":
        print("PROBLEMA: áudio praticamente mudo.")
        print("  confira se o microfone certo é o padrão e se não está mutado")
        return 2
    if estado == "saturado":
        print("PROBLEMA: saturação real — a fala está estourando.")
        print("  reduza o ganho:  wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.5")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
