#!/usr/bin/env python3
"""dictate.py — passo 3: ditado completo (atalho → gravação → transcrição → injeção).

Fluxo, disparado por `Pause` (keycode 119):

    aperta  ->  começa a gravar (som de início)
    aperta  ->  para, transcreve localmente, injeta no campo focado (som de fim)

Escuta o teclado por `evdev` em `/dev/input/event*`, então funciona com qualquer
janela em foco: WhatsApp, Slack, VS Code, Antigravity, ChatGPT, DSH e os TUIs.

Uso:
    python3 dictate.py                        # daemon
    python3 dictate.py --once                 # um ciclo
    python3 dictate.py --model small          # modelo Whisper
    python3 dictate.py --terminal              # alvo é terminal
    python3 dictate.py --audio-file a.wav     # testa a transcrição sem microfone
    python3 dictate.py --text "oi"            # injeta texto fixo, sem transcrever
    python3 dictate.py --dry-run              # não injeta

Decisões que vieram de medição (ver README):
  - modelo `small` em int8: turbo ficou ~3x mais lento e não serve para ditado;
  - `cpu_threads=4`: 8 threads disputam CPU com o desktop e pioram;
  - `initial_prompt` genérico em pt-BR, que corrigiu truncamento de palavras.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from detect_key import EVENT_FMT, EVENT_SIZE, EV_KEY, is_button, keyboard_devices, key_name
from inject import paste_text, sanitize, type_text
from correct import apply_corrections, load_corrections
from record import Recorder, classify, clip_stats, levels
from refine import Refiner, needs_refine
from stt import Transcriber

PAUSE = 119
SOUND_START = "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"
SOUND_STOP = "/usr/share/sounds/freedesktop/stereo/complete.oga"

MIN_TOGGLE_INTERVAL_S = 0.30
# Gravações mais curtas que isto são clique acidental, não fala.
MIN_DICTATION_S = 0.40


class ToggleGate:
    """Converte eventos de tecla em alternâncias, ignorando autorepeat e bounce."""

    def __init__(self, code: int, min_interval_s: float = MIN_TOGGLE_INTERVAL_S) -> None:
        self.code = code
        self.min_interval_s = min_interval_s
        self._held = False
        self._last_toggle = float("-inf")

    def feed(self, code: int, value: int, now: float) -> bool:
        if code != self.code:
            return False
        if value == 0:
            self._held = False
            return False
        if value in (2,):
            return False
        if value != 1 or self._held:
            return False
        self._held = True
        if now - self._last_toggle < self.min_interval_s:
            return False
        self._last_toggle = now
        return True

    def reset(self) -> None:
        self._held = False


def have(exe: str) -> bool:
    from shutil import which

    return which(exe) is not None


def play(path: str) -> None:
    for player in ("paplay", "pw-play"):
        if not have(player):
            continue
        try:
            subprocess.run(
                [player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False, start_new_session=True,
            )
            return
        except (OSError, subprocess.SubprocessError):
            continue


def notify(summary: str, body: str = "") -> None:
    """Notificação de desktop: não rouba foco, ao contrário de uma janela."""
    if not have("notify-send"):
        return
    try:
        subprocess.run(
            ["notify-send", "-a", "voice-typer", "-t", "2500", summary, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False, start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def open_devices(paths: list[str]) -> dict[int, object]:
    """Abre os nós de evento em modo NÃO bloqueante.

    Dois detalhes que já causaram bug aqui:

    - guardar a referência do objeto de arquivo como valor: usar só
      `open(...).fileno()` deixaria o temporário ser coletado e fecharia o
      descritor;
    - abrir com `O_NONBLOCK`: em modo bloqueante, qualquer `read()` sem evento
      pendente trava o processo indefinidamente — foi o que travou o `drain()`
      por 16 s até o timeout.
    """
    handles: dict[int, object] = {}
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        handles[fd] = os.fdopen(fd, "rb", buffering=0)
    return handles


def drain(handles: dict[int, object], max_bytes: int = 1 << 16) -> None:
    """Descarta eventos acumulados enquanto o processo estava ocupado.

    Sem isto, teclas apertadas durante os segundos de transcrição seriam lidas de
    uma vez ao voltar ao loop e alternariam o estado de forma errada. A leitura
    para quando `select` com timeout zero não indica dados disponíveis, e há um
    teto de bytes para não girar sem fim num dispositivo que não drena.
    """
    fds = list(handles)
    lidos = 0
    while fds and lidos < max_bytes:
        ready, _, _ = select.select(fds, [], [], 0)
        if not ready:
            return
        for fd in ready:
            try:
                chunk = handles[fd].read(EVENT_SIZE)  # type: ignore[union-attr]
            except OSError:
                fds.remove(fd)
                continue
            if not chunk:
                fds.remove(fd)
                continue
            lidos += len(chunk)


STATE_DIR = Path.home() / ".local" / "state" / "voice-typer"
DEFAULT_LOG = STATE_DIR / "dictations.jsonl"
STATUS_FILE = STATE_DIR / "status.json"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voice-typer"
# Na instalação via pacote o código fica em /opt (somente leitura), então a
# configuração editável mora no diretório do usuário. O arquivo do projeto fica
# como fallback, para desenvolvimento.
SETTINGS_CANDIDATOS = (
    CONFIG_DIR / "settings.json",
    Path(__file__).resolve().parent / "settings.json",
)
# Vocabulário e correções: o arquivo do usuário complementa o do projeto, para
# que termos pessoais não precisem ser versionados junto do código.
def _candidatos(nome: str) -> tuple[Path, Path]:
    return (CONFIG_DIR / nome, Path(__file__).resolve().parent / nome)
SETTINGS_FILE = SETTINGS_CANDIDATOS[0]

DEFAULT_SETTINGS = {
    "key": PAUSE,
    "model": "small",
    "language": "pt",
    "refine": "never",
    "paste_above": 80,
    "method": "auto",
    "terminal": False,
    "no_sound": False,
    "delay": 15,
}


def resolver_vocabulario(caminho: str | Path | None = None) -> list[str]:
    """Termos de hotwords: arquivo explícito, ou usuário + projeto combinados.

    Centralizado aqui porque o daemon e o `--doctor` precisam concordar. Uma vez
    divergiram: o doctor usava um caminho relativo único e reportava 0 termos
    enquanto o daemon carregava 7.
    """
    fontes = [Path(caminho)] if caminho else list(_candidatos("vocabulary.txt"))
    termos: list[str] = []
    for arq in fontes:
        if not arq.exists():
            continue
        for ln in arq.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and ln not in termos:
                termos.append(ln)
    return termos


def resolver_regras(caminho: str | Path | None = None) -> list:
    """Regras de correção: arquivo explícito, ou usuário + projeto combinados."""
    fontes = [Path(caminho)] if caminho else list(_candidatos("corrections.txt"))
    regras: list = []
    for arq in fontes:
        regras.extend(load_corrections(arq))
    return regras


def load_settings(path: Path | None = None) -> dict:
    """Configuração persistida, em ordem de precedência.

    Sem `path`, usa o primeiro candidato que existir
    (~/.config/voice-typer/settings.json na instalação, o do projeto em dev).
    """
    cfg = dict(DEFAULT_SETTINGS)
    candidatos = (path,) if path else SETTINGS_CANDIDATOS
    for cand in candidatos:
        if cand and cand.exists():
            try:
                cfg.update(json.loads(cand.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
            break
    return cfg


def write_status(state: str, **extra) -> None:
    """Publica o estado para a interface gráfica.

    Arquivo minúsculo em vez de socket: a GUI só precisa saber se está gravando,
    e não queremos um canal de controle para manter. Também serve de feedback
    visual sem janela — janela roubaria foco e quebraria a injeção.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        dados = {"state": state, "ts": time.time(), "pid": os.getpid()}
        dados.update(extra)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATUS_FILE)
    except OSError:
        pass


def write_log(path: Path | None, record: dict) -> None:
    """Anexa um registro em JSONL.

    O log existe para separar dois problemas que parecem iguais: erro de
    transcrição e perda de caracteres na injeção. Se o texto aqui está completo e
    o aplicativo mostra faltando letra, a perda foi na injeção.
    """
    if path is None:
        return
    import json

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def resolve_method(requested: str, text: str, paste_above: int) -> str:
    """Decide entre digitar e colar.

    Digitar gera ~125 eventos de teclado por segundo no `wtype`. Apps Electron e
    WebViews podem perder eventos nessa taxa, e o sintoma é letra faltando no meio
    da palavra. Colar é atômico: não existe taxa de eventos para perder.
    """
    if requested != "auto":
        return requested
    return "paste" if len(text) > paste_above else "type"


def emit_text(text: str, method: str, terminal: bool, delay: int, dry_run: bool) -> int:
    clean = sanitize(text, keep_newlines=False)
    if not clean:
        return 0
    if dry_run:
        print(f"[dry-run] injetaria em {'terminal' if terminal else 'app'}: {clean!r}")
        return 0
    if method in ("auto", "type") and type_text(clean, delay, dry_run=False):
        return 0
    if method == "type":
        return 3
    if paste_text(clean, terminal, dry_run=False, restore_clipboard=True):
        return 0
    return 2


# Configuração de LLM em ordem de precedência: diretório do projeto (desenvolvimento)
# e depois ~/.config/voice-typer (instalação via pacote, onde /opt é somente leitura).
LLM_ENV_CANDIDATOS = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voice-typer" / "llm.env",
    Path(__file__).resolve().parent / "llm.env",
)


def load_env_file(path: Path | None = None) -> None:
    """Carrega KEY=VALUE de llm.env, sem sobrescrever o que já está no ambiente.

    Sem `path`, tenta os candidatos em ordem. Na instalação via pacote o código
    fica em /opt (somente leitura), então a configuração do usuário mora em
    ~/.config/voice-typer/llm.env.
    """
    if path is None:
        for cand in LLM_ENV_CANDIDATOS:
            if cand.exists():
                path = cand
                break
        else:
            return
    if not path.exists():
        return
    for linha in path.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave, valor = chave.strip(), valor.strip().strip('"').strip("'")
        if chave and chave not in os.environ:
            os.environ[chave] = valor


def maybe_reexec_in_input_group() -> None:
    """Re-executa com o grupo `input` quando ele não está no conjunto atual.

    Um serviço systemd de usuário herda os grupos do gerenciador de sessão, que
    pode ter iniciado antes de o usuário entrar no grupo `input`. Nesse caso ler
    /dev/input falha com EACCES. `sg input -c ...` resolve sem privilégio extra:
    o processo é re-executado com o grupo como grupo efetivo.

    `SupplementaryGroups=input` na unidade não serve: o gerenciador não tem o
    grupo no próprio conjunto e falha com "Operation not permitted".
    """
    import shutil

    try:
        if "input" in os.getgroups() or "input" in _group_names():
            return
    except OSError:
        return
    if os.environ.get("VT_INPUT_GROUP") == "1":
        return  # já tentamos; não entrar em laço
    sg = shutil.which("sg") or "/usr/bin/sg"
    if not os.path.exists(sg):
        print("aviso: grupo `input` ausente e `sg` indisponível", file=sys.stderr)
        return
    print("(re-executando com o grupo `input` via sg)")
    # -u explícito: sem ele o processo re-executado bufferiza o stdout e o log
    # fica invisível no journal até o encerramento, o que torna o serviço
    # impossível de diagnosticar.
    cmd = " ".join(["-u", "'" + sys.executable + "'", "'" + str(Path(__file__).resolve()) + "'"]
                   + ["'" + a + "'" for a in sys.argv[1:]])
    env = dict(os.environ, VT_INPUT_GROUP="1")
    os.execve(sg, [sg, "input", "-c", cmd], env)


def _group_names() -> set[str]:
    """Nomes dos grupos do processo atual, via stdlib."""
    import grp

    nomes = set()
    for gid in os.getgroups():
        try:
            nomes.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            continue
    return nomes


def maybe_reexec_in_venv() -> None:
    """Re-executa no Python do .venv, onde o faster-whisper está instalado.

    Assim `python3 dictate.py` funciona sem o usuário precisar lembrar de
    `.venv/bin/python`. A variável VT_REEXEC impede laço infinito.
    """
    import os

    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("VT_REEXEC") == "1":
        return
    venv_py = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if not venv_py.exists() or Path(sys.executable).resolve() == venv_py.resolve():
        return
    print(f"(reiniciando no venv: {venv_py})")
    env = dict(os.environ, VT_REEXEC="1")
    os.execve(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]], env)


def doctor(args) -> int:
    """Verifica a stack inteira em um comando. Não carrega o modelo Whisper."""
    import tempfile

    ok = True

    def linha(nome: str, estado: bool, detalhe: str = "") -> None:
        nonlocal ok
        if not estado:
            ok = False
        print(f"  {'OK   ' if estado else 'FALHA'} {nome:22} {detalhe}")

    print("1) entrada de teclado")
    devs = keyboard_devices()
    linha("teclados", bool(devs), f"{len(devs)} dispositivo(s)")
    linha("/dev/uinput", os.access("/dev/uinput", os.W_OK), "escrita para testes")

    print("\n2) áudio")
    try:
        rec = Recorder()
        linha("gravador", True, rec.name)
        tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="vt-doctor-")[1])
        rec.start()
        time.sleep(1.5)
        rec.stop(tmp)
        peak, rms = levels(tmp)
        no_teto, corridas = clip_stats(tmp)
        estado = classify(peak, rms, corridas)
        tmp.unlink(missing_ok=True)
        linha("nível capturado", estado != "mudo",
              f"pico {peak:.1f} dB, RMS {rms:.1f} dB" if peak is not None else "sem medição")
        linha("saturação", estado != "saturado",
              f"{corridas} sequência(s) de clipping" if corridas else "sem clipping")
    except (RuntimeError, OSError) as exc:
        linha("gravador", False, str(exc))

    print("\n3) transcrição")
    try:
        import faster_whisper  # noqa: F401

        linha("faster-whisper", True, faster_whisper.__version__)
    except ImportError:
        linha("faster-whisper", False, "não instalado neste interpretador")
    linha("modelo", True, f"{args.model} (carrega no início)")

    print("\n4) regras locais")
    regras = resolver_regras(args.corrections)
    linha("correções", True, f"{len(regras)} regra(s)")
    termos = resolver_vocabulario(args.vocabulary)
    linha("vocabulário", True, f"{len(termos)} termo(s)")
    if not termos and not regras:
        print("         (nenhum termo pessoal em ~/.config/voice-typer/)")

    print("\n5) refino")
    if args.refine == "never":
        linha("refino", True, "desligado")
    else:
        r = Refiner(base_url=args.refine_base_url, model=args.refine_model)
        linha("modelo", True, f"{r.model} | raciocínio desligado: {r.disable_reasoning}")
        try:
            import urllib.request

            url = f"{r.base_url}/models"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {r.api_key or ''}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                modelos = len((json.loads(resp.read().decode()).get("data") or []))
            linha("roteador", True, f"{modelos} modelo(s) em {r.base_url}")
        except Exception as exc:  # noqa: BLE001
            linha("roteador", False, f"{r.base_url}: {exc}")

    print("\n6) atalho")
    linha("tecla", True, f"{key_name(args.key)} (code {args.key})")
    print("\n" + ("tudo pronto" if ok else "há pendências acima"))
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    load_env_file()
    maybe_reexec_in_venv()
    maybe_reexec_in_input_group()
    ap = argparse.ArgumentParser(description="Ditado por voz (atalho, gravação, transcrição).")
    cfg = load_settings()
    ap.add_argument("--key", type=int, default=cfg["key"], help=f"keycode do atalho (padrão {cfg['key']})")
    ap.add_argument("--model", default=cfg["model"], help=f"modelo Whisper (padrão {cfg['model']})")
    ap.add_argument("--language", default=cfg["language"])
    ap.add_argument("--method", choices=("auto", "type", "paste"), default=cfg["method"])
    ap.add_argument("--terminal", action="store_true", default=cfg["terminal"], help="alvo é terminal")
    ap.add_argument("--delay", type=int, default=cfg["delay"],
                    help="delay por caractere no type (ms). Digitar rápido faz apps Electron perderem teclas")
    ap.add_argument("--paste-above", type=int, default=cfg["paste_above"],
                    help="com --method auto, cola em vez de digitar acima deste tamanho (padrão 80)")
    ap.add_argument("--vocabulary", default=None,
                    help="arquivo de vocabulário explícito (padrão: usuário + projeto)")
    ap.add_argument("--corrections", default=None,
                    help="arquivo de correções explícito (padrão: usuário + projeto)")
    ap.add_argument("--no-correct", action="store_true", help="não aplicar correções literais")
    ap.add_argument("--refine", nargs="?", const="auto", default=cfg["refine"],
                    choices=("never", "auto", "always"),
                    help="refino por LLM: 'auto' só quando o texto mostra sinal de problema (padrão), "
                         "'always' em toda ditada, 'never' desligado")
    ap.add_argument("--refine-base-url", help="sobrepõe VT_LLM_BASE_URL")
    ap.add_argument("--refine-model", help="sobrepõe VT_LLM_MODEL")
    ap.add_argument("--refine-timeout", type=float, help="sobrepõe VT_LLM_TIMEOUT")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="arquivo JSONL de ditados")
    ap.add_argument("--no-log", action="store_true", help="não registrar ditados")
    ap.add_argument("--text", help="injeta este texto em vez de transcrever")
    ap.add_argument("--audio-file", help="usa este arquivo em vez do microfone (teste)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sound", action="store_true", default=cfg["no_sound"])
    ap.add_argument("--keep-audio", metavar="ARQUIVO",
                    help="guarda o WAV da última ditada para diagnosticar áudio")
    ap.add_argument("--device", action="append", help="limita a leitura a este dispositivo")
    ap.add_argument("--doctor", action="store_true", help="verifica a stack inteira e sai")
    args = ap.parse_args(argv)

    if args.doctor:
        return doctor(args)

    paths = args.device or keyboard_devices()
    if not paths:
        print("nenhum teclado encontrado", file=sys.stderr)
        return 1
    handles = open_devices(paths)
    if not handles:
        print("não foi possível abrir nenhum teclado (grupo `input`?)", file=sys.stderr)
        return 2

    # Modelo carrega no início: a primeira ditada não paga a carga.
    hotwords = resolver_vocabulario(args.vocabulary)
    refiner: Refiner | None = None
    if args.refine != "never":
        refiner = Refiner(
            base_url=args.refine_base_url,
            model=args.refine_model,
            timeout=args.refine_timeout,
        )
        print(f"refino: {refiner.target}")
    regras = [] if args.no_correct else resolver_regras(args.corrections)
    if hotwords:
        print(f"vocabulário: {len(hotwords)} termo(s)")
    if regras:
        print(f"correções literais: {len(regras)} regra(s)")

    transcriber: Transcriber | None = None
    if not args.text:
        print(f"carregando modelo {args.model}…")
        t0 = time.perf_counter()
        transcriber = Transcriber(args.model, language=args.language, hotwords=hotwords)
        print(f"modelo pronto em {time.perf_counter() - t0:.2f}s")

    recorder: Recorder | None = None
    if not args.text and not args.audio_file:
        recorder = Recorder()
        print(f"gravador: {recorder.name}")

    gate = ToggleGate(args.key)
    recording = False
    tmp_wav: Path | None = None
    label = key_name(args.key)

    # SIGTERM vira flag, não exceção: levantar KeyboardInterrupt interrompia o
    # bloco `finally` no meio do fechamento dos descritores e poluía o journal
    # com traceback a cada restart do serviço.
    parar = {"sim": False}

    def _on_term(_signum, _frame):
        parar["sim"] = True

    signal.signal(signal.SIGTERM, _on_term)

    print(f"atalho: {label} (code {args.key})  |  {len(handles)} teclado(s)")
    print(f"aperte {label} para começar/parar. Ctrl+C sai.\n")
    # Estado inicial explícito: sem isso o status.json fica com o valor da
    # execução anterior ("stopped"), e a bandeja exibe "serviço parado" com o
    # daemon ativo.
    write_status("idle")

    try:
        while not parar["sim"]:
            # timeout curto para que a flag de parada seja vista mesmo sem
            # eventos de teclado; custo desprezível.
            ready, _, _ = select.select(list(handles), [], [], 0.5)
            if not ready:
                continue
            for fd in ready:
                try:
                    raw = handles[fd].read(EVENT_SIZE)  # type: ignore[union-attr]
                except OSError:
                    continue
                if not raw or len(raw) < EVENT_SIZE:
                    continue
                _, _, ev_type, code, value = struct.unpack(EVENT_FMT, raw)
                if ev_type != EV_KEY or is_button(code):
                    continue
                if not gate.feed(code, value, time.monotonic()):
                    continue

                if not recording:
                    recording = True
                    write_status("recording")
                    print("● gravando")
                    if not args.no_sound:
                        play(SOUND_START)
                    if recorder is not None:
                        recorder.start()
                    continue

                # ---- parar ----
                recording = False
                write_status("processing")
                print("■ parado")
                if not args.no_sound:
                    play(SOUND_STOP)

                if args.text:
                    rc = emit_text(args.text, args.method, args.terminal, args.delay, args.dry_run)
                    if rc != 0 and not args.dry_run:
                        notify("voice-typer: falha ao injetar")
                    if args.once:
                        return rc
                    continue

                # 1. obter o WAV
                if args.audio_file:
                    wav = Path(args.audio_file)
                    if not wav.exists():
                        notify("voice-typer: arquivo de teste não encontrado")
                        continue
                else:
                    tmp_wav = Path(tempfile.mkstemp(suffix=".wav", prefix="vt-dict-")[1])
                    try:
                        recorder.stop(tmp_wav)  # type: ignore[union-attr]
                    except RuntimeError as exc:
                        print(f"  falha na captura: {exc}", file=sys.stderr)
                        notify("voice-typer: falha ao gravar", str(exc))
                        tmp_wav.unlink(missing_ok=True)
                        tmp_wav = None
                        gate.reset()
                        drain(handles)
                        continue
                    wav = tmp_wav

                try:
                    dur = max(0.0, (wav.stat().st_size - 44) / 32000)
                    if dur < MIN_DICTATION_S:
                        print(f"  gravação curta demais ({dur:.2f}s), ignorando")
                        notify("voice-typer: gravação muito curta")
                        continue

                    peak, rms = levels(wav)
                    no_teto, corridas = clip_stats(wav)
                    estado = classify(peak, rms, corridas)
                    if estado == "mudo":
                        print(f"  áudio mudo (pico {peak:.1f} dB) — nada a transcrever")
                        notify("voice-typer: não ouvi nada", "verifique o microfone")
                        continue
                    if estado == "saturado":
                        print(f"  aviso: áudio saturado — {corridas} sequência(s) de clipping, "
                              f"{no_teto} amostra(s) no teto (pico {peak:.1f} dB, RMS {rms:.1f} dB)")
                        print("         reduza o ganho: pactl set-source-volume @DEFAULT_AUDIO_SOURCE@ 50%")

                    # 2. transcrever
                    t0 = time.perf_counter()
                    result = transcriber.transcribe_file(wav)  # type: ignore[union-attr]
                    print(f"  {result.audio_s:.1f}s de áudio -> {result.elapsed_s:.2f}s "
                          f"(RTF {result.rtf:.2f})")
                    if not result.text:
                        print("  transcrição vazia")
                        notify("voice-typer: nada reconhecido")
                        continue
                    bruto = result.text
                    texto, disparos = apply_corrections(bruto, regras)
                    if disparos:
                        print(f"  correções aplicadas: {disparos}")

                    # Refino por LLM é a última camada e falha aberta: se der
                    # qualquer problema, o texto corrigido por regra é usado.
                    refinou, motivo_refino = False, "desligado"
                    if refiner is not None:
                        vale, sinal = (
                            (True, "always") if args.refine == "always" else needs_refine(texto)
                        )
                        if vale:
                            print(f"  refino: aplicando ({sinal})")
                            texto, refinou, motivo_refino = refiner.refine(texto)
                            print(f"  refino: {'aplicado' if refinou else 'ignorado'} ({motivo_refino})")
                        else:
                            refinou, motivo_refino = False, f"pulado ({sinal})"
                            print(f"  refino: pulado ({sinal})")

                    if disparos or refinou:
                        print(f"  bruto: {bruto}")
                    print(f"  texto: {texto}")
                    write_status("idle", last_text=texto[:200], chars=len(texto))

                    # 3. injetar
                    metodo = resolve_method(args.method, texto, args.paste_above)
                    rc = emit_text(texto, metodo, args.terminal, args.delay, args.dry_run)
                    write_log(
                        None if args.no_log else Path(args.log),
                        {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "audio_s": round(result.audio_s, 2),
                            "rtf": round(result.rtf, 3),
                            "model": args.model,
                            "chars": len(texto),
                            "corrections": disparos,
                            "method": metodo,
                            "rc": rc,
                            "dry_run": args.dry_run,
                            "transcript": texto,
                            "transcript_raw": bruto,
                            "refined": refinou,
                            "refine_reason": motivo_refino,
                        },
                    )
                    if rc == 0:
                        print(f"  injetado via {metodo}")
                    else:
                        print(f"  FALHA ao injetar (código {rc})", file=sys.stderr)
                        notify("voice-typer: falha ao injetar")
                finally:
                    if tmp_wav is not None:
                        if args.keep_audio:
                            import shutil as _sh
                            try:
                                _sh.copyfile(tmp_wav, Path(args.keep_audio).expanduser())
                                print(f"  áudio guardado em {args.keep_audio}")
                            except OSError as exc:
                                print(f"  falha ao guardar áudio: {exc}", file=sys.stderr)
                        tmp_wav.unlink(missing_ok=True)
                        tmp_wav = None

                # Descarta o que foi apertado durante a transcrição e evita que o
                # `_held` fique preso se a tecla foi solta enquanto estávamos ocupados.
                gate.reset()
                drain(handles)

                if args.once:
                    return 0
    except KeyboardInterrupt:
        print("\nencerrado.")
        return 0
    finally:
        write_status("stopped")
        if recorder is not None:
            recorder.cancel()
        for fh in handles.values():
            try:
                fh.close()  # type: ignore[union-attr]
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
