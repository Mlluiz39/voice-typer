#!/usr/bin/env python3
"""detect_key.py — descobre qual tecla usar como atalho do ditado.

Lê `/dev/input/event*` diretamente, sem dependências (só stdlib), e imprime o
nome de cada tecla pressionada. Serve para duas coisas:

  1. descobrir quais teclas o SEU teclado realmente tem — ScrollLock e Pause
     faltam em muitos teclados compactos e de notebook;
  2. verificar se a permissão de leitura funciona antes de construirmos o
     listener do atalho global.

Escuta apenas. Não captura o teclado (sem EVIOCGRAB), não interfere no que os
aplicativos recebem e não grava nada em disco.

Uso:
    python3 detect_key.py              # mostra cada tecla pressionada
    python3 detect_key.py --combo      # mostra os modificadores ativos junto
    python3 detect_key.py --once       # encerra na primeira tecla

Requer participação no grupo `input` (você já está).
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import os
import select
import struct
import sys

EV_KEY = 1
# struct input_event em x86-64: tv_sec(8) tv_usec(8) type(2) code(2) value(4)
EVENT_SIZE = 24
EVENT_FMT = "llHHi"

# Nomes dos keycodes do Linux que importam para escolher um atalho.
KEYS: dict[int, str] = {
    1: "Escape", 2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7",
    9: "8", 10: "9", 11: "0", 12: "minus", 13: "equal", 14: "Backspace",
    15: "Tab", 16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y",
    22: "U", 23: "I", 24: "O", 25: "P", 26: "leftbrace", 27: "rightbrace",
    28: "Enter", 29: "LeftCtrl", 30: "A", 31: "S", 32: "D", 33: "F",
    34: "G", 35: "H", 36: "J", 37: "K", 38: "L", 39: "semicolon",
    40: "apostrophe", 41: "grave", 42: "LeftShift", 43: "backslash",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N", 50: "M",
    51: "comma", 52: "dot", 53: "slash", 54: "RightShift",
    55: "KPasterisk", 56: "LeftAlt", 57: "Space", 58: "CapsLock",
    59: "F1", 60: "F2", 61: "F3", 62: "F4", 63: "F5", 64: "F6", 65: "F7",
    66: "F8", 67: "F9", 68: "F10", 69: "NumLock", 70: "ScrollLock",
    71: "KP7", 72: "KP8", 73: "KP9", 74: "KPminus", 75: "KP4", 76: "KP5",
    77: "KP6", 78: "KPplus", 79: "KP1", 80: "KP2", 81: "KP3", 82: "KP0",
    83: "KPdot", 87: "F11", 88: "F12", 96: "KPEnter", 97: "RightCtrl",
    98: "KPslash", 99: "SysRq", 100: "RightAlt", 102: "Home", 103: "Up",
    104: "PageUp", 105: "Left", 106: "Right", 107: "End", 108: "Down",
    109: "PageDown", 110: "Insert", 111: "Delete", 113: "Mute",
    114: "VolumeDown", 115: "VolumeUp", 116: "Power",
    117: "KPequal", 119: "Pause", 121: "KPcomma", 122: "Hangeul",
    125: "LeftMeta", 126: "RightMeta", 127: "Compose", 139: "Menu",
    158: "Back", 159: "Forward", 163: "NextSong", 164: "PlayPause",
    165: "PreviousSong", 166: "StopCD", 172: "HomePage", 183: "F13",
    184: "F14", 185: "F15", 186: "F16", 187: "F17", 188: "F18",
    189: "F19", 190: "F20", 191: "F21", 192: "F22", 193: "F23", 194: "F24",
    217: "Search",
}

MODIFIERS: dict[int, str] = {
    29: "ctrl", 97: "rctrl",
    42: "shift", 54: "rshift",
    56: "alt", 100: "ralt",
    125: "super", 126: "rsuper",
}

# Teclas que valem a pena testar como atalho, com o motivo.
CANDIDATES = {
    "ScrollLock": "ideal: não envia nada para os apps, zero conflito",
    "Pause": "ideal: igual ao ScrollLock, mas raro em teclados compactos",
    "RightCtrl": "bom: costuma estar livre e não conflita com AltGr",
    "Menu": "bom: tecla morta na maioria dos apps",
    "F13": "bom se existir: nenhum app usa",
}


def key_name(code: int) -> str:
    return KEYS.get(code, f"code_{code}")


# Faixa BTN_* do kernel (0x100-0x151): botões de mouse, joystick e caneta.
# Dispositivos compostos entregam esses códigos no mesmo nó que o teclado.
def is_button(code: int) -> bool:
    return 0x100 <= code <= 0x151


def _ioc(direction: int, type_: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(type_) << 8) | nr


def _has_capability(fd: int, ev_type: int, bits_size: int, code: int) -> bool:
    """Testa um bit em EVIOCGBIT(ev_type)."""
    buf = bytearray(bits_size)
    try:
        fcntl.ioctl(fd, _ioc(2, "E", 0x20 + ev_type, bits_size), buf)
    except OSError:
        return False
    index, offset = divmod(code, 8)
    return index < len(buf) and bool(buf[index] >> offset & 1)


def is_keyboard_device(path: str) -> bool:
    """Um teclado tem EV_KEY e expõe teclas de letra (KEY_A).

    A checagem é por capacidade, não por nome de handler: dispositivos criados
    via uinput não recebem o handler `kbd`, e alguns teclados físicos também
    não. Filtrar pelo nome perderia esses nós.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        if not _has_capability(fd, 0, 8, EV_KEY):
            return False
        # KEY_A = 30, enviesado para teclado e não para mouse/gamepad
        return _has_capability(fd, EV_KEY, 96, 30)
    finally:
        os.close(fd)


def keyboard_devices() -> list[str]:
    """Lista os nós de evento que são teclado.

    Tenta por capacidade; se nenhum for encontrado, cai para o handler `kbd`
    em /proc/bus/input/devices.
    """
    candidates = sorted(glob.glob("/dev/input/event*"))
    found = [path for path in candidates if is_keyboard_device(path)]
    if found:
        return found

    try:
        with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []

    events: set[str] = set()
    for block in text.split("\n\n"):
        if "kbd" not in block:
            continue
        for line in block.splitlines():
            if not line.startswith("H: Handlers="):
                continue
            for token in line.split():
                if token.startswith("event"):
                    events.add("/dev/input/" + token)
    return sorted(events)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Descobre keycodes das teclas pressionadas.")
    ap.add_argument("--combo", action="store_true", help="mostra modificadores ativos junto")
    ap.add_argument("--once", action="store_true", help="encerra na primeira tecla")
    ap.add_argument("--device", action="append", help="lê só este dispositivo (pode repetir)")
    args = ap.parse_args(argv)

    paths = args.device or keyboard_devices()
    if not paths:
        print("nenhum teclado encontrado em /proc/bus/input/devices", file=sys.stderr)
        return 1

    # Manter as referências vivas: um objeto de arquivo temporário seria
    # coletado pelo GC e fecharia o descritor.
    handles: dict[int, object] = {}
    for path in paths:
        try:
            fh = open(path, "rb", buffering=0)
        except OSError as exc:
            print(f"aviso: {path}: {exc.strerror}", file=sys.stderr)
            continue
        handles[fh.fileno()] = fh

    if not handles:
        print(
            "não foi possível abrir nenhum dispositivo de entrada.\n"
            "Confira sua participação no grupo `input` (após alterar, refaça o login).",
            file=sys.stderr,
        )
        return 2

    print(f"escutando {len(handles)} teclado(s). Pressione teclas. Ctrl+C para sair.\n")
    print("teclas candidatas a atalho:")
    for name, why in CANDIDATES.items():
        print(f"  {name:12} {why}")
    print()

    held: set[int] = set()
    try:
        while True:
            ready, _, _ = select.select(list(handles), [], [], None)
            for fd in ready:
                try:
                    raw = handles[fd].read(EVENT_SIZE)  # type: ignore[union-attr]
                except OSError:
                    continue
                if not raw or len(raw) < EVENT_SIZE:
                    continue
                _, _, ev_type, code, value = struct.unpack(EVENT_FMT, raw)
                if ev_type != EV_KEY:
                    continue

                if value == 0:      # release
                    held.discard(code)
                    continue
                if value == 2:      # autorepeat
                    continue

                if is_button(code):
                    continue

                if code in MODIFIERS:  # press de modificador
                    held.add(code)
                    if args.combo:
                        print(f"  {MODIFIERS[code]:8} (code {code})")
                    continue

                name = key_name(code)
                if args.combo:
                    mods = [MODIFIERS[c] for c in MODIFIERS if c in held]
                    label = "+".join(mods + [name]) if mods else name
                else:
                    label = name
                marker = "   <-- candidato" if name in CANDIDATES else ""
                print(f"  {label:30} code {code:<4}{marker}")

                if args.once:
                    return 0
    except KeyboardInterrupt:
        print("\nencerrado.")
        return 0
    finally:
        for fh in handles.values():
            try:
                fh.close()  # type: ignore[union-attr]
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
