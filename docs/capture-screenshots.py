#!/usr/bin/env python3
"""capture-screenshots.py — gera as imagens do README.

Renderiza a janela real da interface com `Gtk.OffscreenWindow`, sem capturar a
tela do usuário. Vantagens sobre um screenshot comum: é reproduzível, não expõe
nada além do próprio aplicativo e pode ser regerado quando a UI mudar.

    python3 docs/capture-screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from voice_typer_gui import PainelConfig  # noqa: E402


def renderizar(painel: Gtk.Widget, destino: Path, escala: float = 2.0) -> None:
    """Renderiza o painel num offscreen, amplia e salva como PNG.

    Precisa receber o painel (um Gtk.Box), não a janela: GTK não permite
    adicionar um toplevel como filho de outro, e o resultado seria uma imagem
    vazia com apenas o fundo.
    """
    painel.set_size_request(560, -1)
    off = Gtk.OffscreenWindow()
    off.add(painel)
    off.show_all()

    # processa o layout pendente antes de capturar
    for _ in range(3):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    largura = painel.get_allocated_width()
    altura = painel.get_allocated_height()
    off.set_size_request(largura, altura)
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)

    pix = off.get_pixbuf()
    if pix is None:
        raise RuntimeError("não foi possível renderizar a janela")

    if escala != 1.0:
        pix = pix.scale_simple(
            int(pix.get_width() * escala),
            int(pix.get_height() * escala),
            GdkPixbuf.InterpType.HYPER,
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    pix.savev(str(destino), "png", [], [])
    print(f"  {destino.relative_to(PROJ)}  {pix.get_width()}x{pix.get_height()}")


def main() -> int:
    destino = PROJ / "docs" / "settings.png"
    print("renderizando interface")
    renderizar(PainelConfig(ao_salvar=None), destino)
    return 0


if __name__ == "__main__":
    sys.exit(main())
