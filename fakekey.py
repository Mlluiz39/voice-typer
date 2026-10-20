#!/usr/bin/env python3
"""fakekey.py — envia keycodes reais via /dev/uinput.

Cria um teclado virtual no kernel e emite os eventos de tecla pedidos. Como o
evento nasce no subsistema de entrada, qualquer programa que leia
`/dev/input/event*` o vê exatamente como vê um teclado físico — é assim que
testamos o atalho do dictate.py sem depender de alguém apertar a tecla.

Uso:
    python3 fakekey.py pause                 # um toque de Pause
    python3 fakekey.py --key 119 --count 2 --interval 1.5
    python3 fakekey.py --code-list           # teclas conhecidas por nome

Cuidado: a tecla emitida também chega ao aplicativo focado, como qualquer tecla
real. `Pause` e `ScrollLock` não fazem nada nos apps; não use este script com
letras se houver um campo de texto em foco.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import struct
import sys
import time

UINPUT_PATH = "/dev/uinput"

# ioctls de uinput
UI_SET_EVBIT = 0x40045564      # _IOW('U', 100, int)
UI_SET_KEYBIT = 0x40045565     # _IOW('U', 101, int)
UI_DEV_SETUP = 0x405C5503      # _IOW('U', 3, struct uinput_setup)
UI_DEV_CREATE = 0x5501         # _IO('U', 1)
UI_DEV_DESTROY = 0x5502        # _IO('U', 2)

EV_SYN = 0
EV_KEY = 1
SYN_REPORT = 0

# struct input_event (x86-64)
EVENT_FMT = "llHHi"
EVENT_SIZE = 24

NAMED_KEYS = {
    "scrolllock": 70,
    "pause": 119,
    "rightctrl": 97,
    "menu": 139,
    "f13": 183,
    "space": 57,
}


def build_device() -> int:
    """Cria o teclado virtual e devolve o fd."""
    fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)

    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)

    # Habilita as teclas que vamos emitir e um conjunto básico de teclado.
    # KEY_A (30) e KEY_Z (44) importam: a detecção de teclado do voice-typer é
    # por capacidade, não por nome de handler.
    for code in (
        1, 2, 3, 28, 29, 30, 31, 42, 44, 54, 56, 57,
        70, 97, 100, 119, 125, 126, 139, 183,
    ):
        fcntl.ioctl(fd, UI_SET_KEYBIT, code)

    # Sem EV_LED de propósito: declarar capacidade de LED faz o kitty emitir
    # uma sequência CSI de protocolo de teclado para o app, que aparece no
    # stdin como lixo. A detecção de teclado do voice-typer é por EV_KEY +
    # KEY_A, então o LED não é necessário.

    # struct uinput_setup: input_id(4x u16) + name[80] + ff_effects_max(u32)
    name = b"voice-typer fakekey".ljust(80, b"\0")
    setup = struct.pack("HHHH", 0x03, 0x1209, 0x0001, 0x0001) + name + struct.pack("I", 0)
    fcntl.ioctl(fd, UI_DEV_SETUP, setup)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    time.sleep(0.2)  # o kernel precisa publicar o nó antes dos eventos
    return fd


def emit(fd: int, code: int) -> None:
    """Pressiona e solta uma tecla, com SYN_REPORT em cada transição."""
    for value in (1, 0):
        os.write(fd, struct.pack(EVENT_FMT, 0, 0, EV_KEY, code, value))
        os.write(fd, struct.pack(EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0))
        time.sleep(0.03)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Emite keycodes reais via uinput.")
    ap.add_argument("name", nargs="?", help="nome da tecla (pause, scrolllock, ...)")
    ap.add_argument("--key", type=int, help="keycode numérico (alternativa ao nome)")
    ap.add_argument("--count", type=int, default=1, help="quantos toques emitir")
    ap.add_argument("--interval", type=float, default=1.0, help="segundos entre toques")
    ap.add_argument(
        "--delay-before",
        type=float,
        default=0.0,
        help="espera antes de emitir (dá tempo do outro processo enumerar o novo dispositivo)",
    )
    ap.add_argument("--code-list", action="store_true", help="lista teclas por nome")
    args = ap.parse_args(argv)

    if args.code_list:
        for name, code in sorted(NAMED_KEYS.items()):
            print(f"{name:12} {code}")
        return 0

    code = args.key
    if code is None and args.name:
        code = NAMED_KEYS.get(args.name.lower())
    if code is None:
        ap.error("informe um nome de tecla ou --key <code>")

    if not os.path.exists(UINPUT_PATH):
        print(f"{UINPUT_PATH} não existe; carregue o módulo: sudo modprobe uinput", file=sys.stderr)
        return 2
    if not os.access(UINPUT_PATH, os.W_OK):
        print(f"sem permissão de escrita em {UINPUT_PATH} (grupo input?)", file=sys.stderr)
        return 2

    try:
        fd = build_device()
    except OSError as exc:
        print(f"falha ao criar teclado virtual: {exc}", file=sys.stderr)
        return 2

    if args.delay_before > 0:
        print(f"dispositivo criado; aguardando {args.delay_before}s antes de emitir")
        time.sleep(args.delay_before)

    try:
        for i in range(args.count):
            emit(fd, code)
            print(f"toque {i + 1}/{args.count}: code {code}")
            if i + 1 < args.count:
                time.sleep(args.interval)
    finally:
        try:
            fcntl.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
