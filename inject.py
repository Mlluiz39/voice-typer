#!/usr/bin/env python3
"""Injetor de texto para o ditado — passo 1 do voice-typer.

Insere texto no campo focado do aplicativo atual, sem integração por app.

Dois backends, nesta ordem de preferência:

  type  -> `wtype`: envia Unicode literal pelo protocolo de teclado virtual do
           compositor (zwp_virtual_keyboard_v1). Não depende do layout de
           teclado, então acentos e emoji funcionam com layout `us` ou `br`.
           Verificado funcionando em cosmic-comp 1.0.

  paste -> `wl-copy` + atalho de colar. Colar é o caminho mais robusto para
           textos longos e o único que não sofre com TUI perdendo caracteres
           durante redesenho.

Uso:
    ./inject.py "texto a digitar"
    ./inject.py --method paste "texto longo ..."
    ./inject.py --terminal "comando para o shell"
    ./inject.py --check          # diagnostica o ambiente
    ./inject.py --dry-run "..."  # mostra o que seria enviado

Segurança para terminal / TUI:
    Newlines no fim do texto são removidos por padrão, porque Enter submeteria
    o prompt no meio do ditado. Newlines internos são convertidos em espaço,
    a menos que --keep-newlines seja passado.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

# wtype recebe o texto como argumento; fatiar evita argumentos gigantes.
CHUNK_SIZE = 500

# Delay padrão entre caracteres. Com 8 ms o wtype gera ~125 eventos por segundo,
# e apps Electron/WebView perdem eventos nessa taxa — sintoma: letra faltando no
# meio da palavra, observado no uso real. O ditado usa colagem para textos longos
# (`--paste-above`); este valor é a margem para o caminho que ainda digita.
DEFAULT_DELAY_MS = 15


def have(exe: str) -> bool:
    return shutil.which(exe) is not None


def session_type() -> str:
    return os.environ.get("XDG_SESSION_TYPE", "").lower()


def sanitize(text: str, keep_newlines: bool) -> str:
    """Normaliza o texto para injeção segura em qualquer campo."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Newline final viraria Enter e submeteria o prompt.
    text = text.rstrip("\n")
    if not keep_newlines:
        # Newline interno no meio de um TUI quebra o comando em vários envios.
        text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    return text


def run(
    cmd: list[str],
    *,
    input_bytes: bytes | None = None,
    capture: bool = True,
    timeout: float = 20.0,
) -> subprocess.CompletedProcess:
    """Executa um comando sem deixar filho daemonizado segurar nossos pipes.

    `wl-copy` forka e permanece vivo servindo a seleção. Se ele herdar um pipe
    de stdout/stderr, o `subprocess.run` espera um EOF que nunca chega e trava.
    Por isso os comandos que daemonizam passam `capture=False` (DEVNULL).
    """
    if capture:
        return subprocess.run(
            cmd,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    return subprocess.run(
        cmd,
        input=input_bytes,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=timeout,
        start_new_session=True,
    )


def _is_extend(ch: str) -> bool:
    """Caractere que precisa ficar colado ao anterior para renderizar junto."""
    o = ord(ch)
    return (
        0x0300 <= o <= 0x036F  # marcas combinantes (acentos decompostos)
        or o == 0x200D          # ZWJ, une sequências de emoji
        or 0xFE00 <= o <= 0xFE0F  # seletores de variação
        or 0x1F3FB <= o <= 0x1F3FF  # modificadores de tom de pele
    )


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Fatia o texto em pedaços seguros para o argumento de linha de comando.

    O corte nunca separa um caractere do seu sufixo combinante: fatiar no meio
    de uma sequência ZWJ ou de uma marca de acento decomposta renderizaria
    lixo nos dois pedaços.
    """
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        # Não cortar antes de um combinante, nem logo após um ZWJ.
        while end < n and (_is_extend(text[end]) or text[end - 1] == "\u200d"):
            end += 1
        chunks.append(text[start:end])
        start = end
    return chunks


def type_text(text: str, delay_ms: int, dry_run: bool) -> bool:
    """Digita via wtype. Retorna False se o backend não estiver utilizável."""
    if not have("wtype"):
        return False

    for chunk in chunk_text(text):
        cmd = ["wtype", "-d", str(delay_ms), chunk]
        if dry_run:
            print(f"[dry-run] {' '.join(cmd[:-1])} {chunk!r}")
            continue
        proc = run(cmd)
        if proc.returncode != 0:
            sys.stderr.write(
                f"wtype falhou (exit {proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}\n"
            )
            return False
    return True


def paste_shortcut(terminal: bool) -> tuple[int, int]:
    """Envia o atalho de colar. Terminais usam Ctrl+Shift+V."""
    if not have("wtype"):
        return (-1, -1)

    if terminal:
        # Ctrl+Shift+V
        proc_ctrl = run(["wtype", "-M", "ctrl", "-M", "shift", "-k", "v", "-m", "shift", "-m", "ctrl"])
    else:
        # Ctrl+V
        proc_ctrl = run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"])
    return (proc_ctrl.returncode, proc_ctrl.returncode)


def paste_text(text: str, terminal: bool, dry_run: bool, restore_clipboard: bool) -> bool:
    """Copia para o clipboard e dispara o atalho de colar."""
    if not have("wl-copy"):
        return False

    previous: bytes | None = None
    if restore_clipboard and have("wl-paste"):
        proc_prev = run(["wl-paste", "--no-newline"])
        if proc_prev.returncode == 0:
            previous = proc_prev.stdout

    if dry_run:
        kind = "Ctrl+Shift+V (terminal)" if terminal else "Ctrl+V"
        print(f"[dry-run] wl-copy <<< {text!r} ; então {kind}")
        return True

    proc = run(["wl-copy"], input_bytes=text.encode("utf-8"), capture=False)
    if proc.returncode != 0:
        sys.stderr.write("wl-copy falhou ao publicar a seleção\n")
        return False

    # Dá tempo do compositor publicar a seleção antes do colar.
    time.sleep(0.06)
    rc_a, rc_b = paste_shortcut(terminal)
    if rc_a != 0 or rc_b != 0:
        sys.stderr.write("falha ao enviar o atalho de colar\n")
        return False

    if restore_clipboard and previous is not None:
        # Restaura depois que o app alvo já leu o clipboard.
        time.sleep(0.35)  # app lento ainda pode não ter lido o clipboard
        run(["wl-copy"], input_bytes=previous, capture=False)
    return True


def check() -> int:
    """Diagnostica o ambiente e diz se a injeção tem como funcionar."""
    rows: list[tuple[str, str, bool]] = []

    st = session_type() or "desconhecido"
    rows.append(("sessão", st, st == "wayland"))

    for exe, why in (
        ("wtype", "digitar Unicode literal"),
        ("wl-copy", "colar via clipboard"),
        ("wl-paste", "salvar/restaurar clipboard"),
        ("kitty", "atalho rápido de terminal (opcional)"),
    ):
        rows.append((exe, why, have(exe)))

    width = max(len(r[0]) for r in rows) + 2
    print("diagnóstico do injetor")
    print("-" * 52)
    for name, why, ok in rows:
        mark = "OK  " if ok else "AUSENTE"
        print(f"{mark} {name.ljust(width)} {why}")

    print()
    if not have("wtype"):
        print("Sem wtype não há como digitar. Instale: sudo apt install wtype")
        return 2
    # Teste inofensivo: wtype sem texto apenas inicializa o teclado virtual.
    proc = run(["wtype", ""])
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        print(f"wtype não conseguiu inicializar o teclado virtual: {err}")
        print("Isto indica que o compositor não expõe zwp_virtual_keyboard_v1.")
        print("Alternativa: usar --method paste.")
        return 2
    print("wtype inicializou o teclado virtual com sucesso.")
    print("Pronto para o passo 2 (atalho global + áudio).")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Injeta texto no campo focado (ditado).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("text", nargs="?", help="texto a injetar")
    ap.add_argument("--method", choices=("auto", "type", "paste"), default="auto")
    ap.add_argument("--terminal", action="store_true", help="alvo é terminal (colar usa Ctrl+Shift+V)")
    ap.add_argument("--delay", type=int, default=DEFAULT_DELAY_MS, help="delay por caractere em ms (type)")
    ap.add_argument("--keep-newlines", action="store_true", help="preserva newlines internos")
    ap.add_argument("--restore-clipboard", action="store_true", help="restaura o clipboard após colar")
    ap.add_argument("--dry-run", action="store_true", help="mostra o que seria feito")
    ap.add_argument("--check", action="store_true", help="diagnostica o ambiente")
    args = ap.parse_args(argv)

    if args.check:
        return check()

    if not args.text:
        ap.error("informe o texto (ou use --check)")

    if session_type() != "wayland":
        sys.stderr.write(
            f"aviso: XDG_SESSION_TYPE={session_type() or '?(vazio)'}; "
            "este injetor foi feito para Wayland.\n"
        )

    text = sanitize(args.text, args.keep_newlines)
    if not text:
        sys.stderr.write("nada a injetar após normalizar o texto\n")
        return 0

    method = args.method
    if method in ("auto", "type"):
        if type_text(text, args.delay, args.dry_run):
            return 0
        if method == "type":
            return 3
        sys.stderr.write("wtype indisponível ou falhou; usando clipboard como fallback\n")

    if paste_text(text, args.terminal, args.dry_run, args.restore_clipboard):
        return 0
    sys.stderr.write("nenhum backend conseguiu injetar o texto\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
