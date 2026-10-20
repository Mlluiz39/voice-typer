#!/usr/bin/env python3
"""voice_typer_gui.py — interface gráfica e ícone de bandeja.

Roda com o Python do SISTEMA (que tem PyGObject/GTK3), não com o venv: o venv
existe para o faster-whisper, e misturar os dois quebra o isolamento.

Arquitetura: a GUI não dita nada. Ela edita `settings.json`, controla o serviço
`voice-typer` e lê `status.json` para mostrar o estado. O daemon no venv segue
sendo o único a tocar áudio e teclado.

Por que bandeja e não uma janela flutuante: uma janela que roube foco quebra a
injeção, porque o texto iria para ela em vez do campo do usuário. O indicador de
bandeja dá feedback visual sem tocar no foco.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator
except (ValueError, ImportError):
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except (ValueError, ImportError):
        AppIndicator = None

from gi.repository import GLib, Gtk

APP_ID = "voice-typer"
PROJECT = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voice-typer"
SETTINGS = CONFIG_DIR / "settings.json"
SETTINGS_PROJETO = PROJECT / "settings.json"
STATE_DIR = Path.home() / ".local" / "state" / "voice-typer"
STATUS = STATE_DIR / "status.json"
SERVICE = "voice-typer.service"

MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo"]
REFINE = ["never", "auto", "always"]

# teclas oferecidas no seletor (nome exibido -> keycode do Linux)
KEYS = {
    "Pause (119)": 119,
    "ScrollLock (70)": 70,
    "RightCtrl (97)": 97,
    "Menu (139)": 139,
    "F13 (183)": 183,
}

ICONE = {"idle": "audio-input-microphone", "recording": "media-record",
         "processing": "process-working", "stopped": "audio-input-microphone"}


def systemctl(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["systemctl", "--user", *args], capture_output=True,
                           text=True, timeout=15)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def ler_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def ler_settings() -> dict:
    """Configuração do usuário; cai para a do projeto em desenvolvimento."""
    return ler_json(SETTINGS) or ler_json(SETTINGS_PROJETO)


def salvar_settings(cfg: dict) -> None:
    """Grava no diretório do usuário: /opt é somente leitura no pacote."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def abrir_arquivo(caminho: Path) -> None:
    if not caminho.exists():
        caminho.write_text("", encoding="utf-8")
    subprocess.Popen(["xdg-open", str(caminho)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


class JanelaConfig(Gtk.Window):
    def __init__(self, ao_salvar) -> None:
        super().__init__(title="voice-typer — configuração")
        self.set_default_size(460, -1)
        self.set_border_width(16)
        self.ao_salvar = ao_salvar
        cfg = ler_settings()

        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(caixa)

        titulo = Gtk.Label()
        titulo.set_markup("<b>Configuração do ditado</b>")
        titulo.set_xalign(0)
        caixa.pack_start(titulo, False, False, 0)

        grade = Gtk.Grid(column_spacing=12, row_spacing=10)
        caixa.pack_start(grade, False, False, 0)

        def rotulo(texto: str, linha: int) -> None:
            lab = Gtk.Label(label=texto)
            lab.set_xalign(0)
            grade.attach(lab, 0, linha, 1, 1)

        rotulo("Tecla do atalho", 0)
        self.combo_tecla = Gtk.ComboBoxText()
        for nome, code in KEYS.items():
            self.combo_tecla.append(str(code), nome)
        self.combo_tecla.set_active_id(str(cfg.get("key", 119)))
        grade.attach(self.combo_tecla, 1, 0, 1, 1)

        rotulo("Modelo de transcrição", 1)
        self.combo_modelo = Gtk.ComboBoxText()
        for m in MODELS:
            self.combo_modelo.append_text(m)
        atual = cfg.get("model", "small")
        self.combo_modelo.set_active(MODELS.index(atual) if atual in MODELS else 2)
        grade.attach(self.combo_modelo, 1, 1, 1, 1)

        rotulo("Refino por LLM", 2)
        self.combo_refino = Gtk.ComboBoxText()
        for r in REFINE:
            self.combo_refino.append_text(r)
        r_atual = cfg.get("refine", "never")
        self.combo_refino.set_active(REFINE.index(r_atual) if r_atual in REFINE else 0)
        grade.attach(self.combo_refino, 1, 2, 1, 1)

        rotulo("Colar acima de (caracteres)", 3)
        self.spin_colar = Gtk.SpinButton.new_with_range(0, 2000, 10)
        self.spin_colar.set_value(cfg.get("paste_above", 80))
        grade.attach(self.spin_colar, 1, 3, 1, 1)

        rotulo("Alvo é terminal", 4)
        self.check_terminal = Gtk.CheckButton()
        self.check_terminal.set_active(bool(cfg.get("terminal", False)))
        grade.attach(self.check_terminal, 1, 4, 1, 1)

        rotulo("Som de início/fim", 5)
        self.check_som = Gtk.CheckButton()
        self.check_som.set_active(not cfg.get("no_sound", False))
        grade.attach(self.check_som, 1, 5, 1, 1)

        separador = Gtk.Separator()
        caixa.pack_start(separador, False, False, 4)

        teste = Gtk.Box(spacing=8)
        caixa.pack_start(teste, False, False, 0)
        self.entrada_teste = Gtk.Entry()
        self.entrada_teste.set_placeholder_text("texto para injetar no campo focado…")
        teste.pack_start(self.entrada_teste, True, True, 0)
        botao_teste = Gtk.Button(label="Injetar teste")
        botao_teste.connect("clicked", self.injetar_teste)
        teste.pack_start(botao_teste, False, False, 0)

        rodape = Gtk.Box(spacing=8)
        caixa.pack_start(rodape, False, False, 6)
        for texto, acao in (
            ("Vocabulário", PROJECT / "vocabulary.txt"),
            ("Correções", PROJECT / "corrections.txt"),
            ("Log", STATE_DIR / "dictations.jsonl"),
        ):
            b = Gtk.Button(label=texto)
            b.connect("clicked", lambda _w, p=acao: abrir_arquivo(p))
            rodape.pack_start(b, False, False, 0)

        salvar = Gtk.Button(label="Salvar e reiniciar")
        salvar.get_style_context().add_class("suggested-action")
        salvar.connect("clicked", self.salvar)
        caixa.pack_start(salvar, False, False, 4)

        self.status = Gtk.Label()
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        caixa.pack_start(self.status, False, False, 0)

    def coletar(self) -> dict:
        return {
            "key": int(self.combo_tecla.get_active_id() or 119),
            "model": MODELS[self.combo_modelo.get_active()],
            "language": "pt",
            "refine": REFINE[self.combo_refino.get_active()],
            "paste_above": int(self.spin_colar.get_value()),
            "method": "auto",
            "terminal": self.check_terminal.get_active(),
            "no_sound": not self.check_som.get_active(),
            "delay": 15,
        }

    def salvar(self, _botao=None) -> None:
        salvar_settings(self.coletar())
        code, saida = systemctl("restart", SERVICE)
        if code != 0:
            self.status.set_text(f"configuração salva; falha ao reiniciar:\n{saida[:200]}")
        else:
            self.status.set_text("configuração salva e serviço reiniciado")
        if self.ao_salvar:
            self.ao_salvar()

    def injetar_teste(self, _botao=None) -> None:
        texto = self.entrada_teste.get_text().strip()
        if not texto:
            self.status.set_text("digite algo para injetar")
            return
        try:
            p = subprocess.run(
                [str(PROJECT / ".venv" / "bin" / "python"), str(PROJECT / "inject.py"), texto],
                capture_output=True, text=True, timeout=30,
            )
            self.status.set_text("injetado" if p.returncode == 0
                                 else f"falhou: {(p.stdout + p.stderr).strip()[:200]}")
        except (OSError, subprocess.SubprocessError) as exc:
            self.status.set_text(f"falhou: {exc}")


class Aplicativo:
    def __init__(self) -> None:
        self.janela: JanelaConfig | None = None
        icone = ICONE["idle"]
        if AppIndicator is not None:
            self.ind = AppIndicator.Indicator.new(
                APP_ID, icone, AppIndicator.IndicatorCategory.APPLICATION_STATUS
            )
            self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self._montar_menu()
        else:
            self.ind = None
            self._abrir_janela()
        GLib.timeout_add(900, self._atualizar)

    def _montar_menu(self) -> None:
        menu = Gtk.Menu()
        self.item_estado = Gtk.MenuItem(label="estado: …")
        self.item_estado.set_sensitive(False)
        menu.append(self.item_estado)
        menu.append(Gtk.SeparatorMenuItem())

        self.item_servico = Gtk.MenuItem(label="Iniciar serviço")
        self.item_servico.connect("activate", self._alternar_servico)
        menu.append(self.item_servico)

        for rotulo, acao in (
            ("Configuração…", self._abrir_janela),
            ("Vocabulário", lambda *_: abrir_arquivo(PROJECT / "vocabulary.txt")),
            ("Correções", lambda *_: abrir_arquivo(PROJECT / "corrections.txt")),
            ("Ver log de ditados", lambda *_: abrir_arquivo(STATE_DIR / "dictations.jsonl")),
        ):
            item = Gtk.MenuItem(label=rotulo)
            item.connect("activate", acao)
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        sair = Gtk.MenuItem(label="Fechar a bandeja")
        sair.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(sair)
        menu.show_all()
        self.ind.set_menu(menu)

    def _abrir_janela(self, *_) -> None:
        if self.janela is None:
            self.janela = JanelaConfig(ao_salvar=self._atualizar)
            self.janela.connect("destroy", self._janela_fechou)
        self.janela.show_all()
        self.janela.present()

    def _janela_fechou(self, *_):
        self.janela = None

    def _alternar_servico(self, *_):
        code, _ = systemctl("is-active", SERVICE)
        systemctl("stop" if code == 0 else "start", SERVICE)
        self._atualizar()

    def _atualizar(self):
        ativo, _ = systemctl("is-active", SERVICE)
        st = ler_json(STATUS)
        estado = st.get("state", "stopped") if ativo == 0 else "stopped"
        if self.ind is not None:
            self.ind.set_icon_full(ICONE.get(estado, "audio-input-microphone"), estado)
        rotulo = {"idle": "pronto", "recording": "gravando…",
                  "processing": "transcrevendo…", "stopped": "serviço parado"}.get(estado, estado)
        self.item_estado.set_label(f"voice-typer: {rotulo}")
        self.item_servico.set_label("Parar serviço" if ativo == 0 else "Iniciar serviço")
        return True


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--settings":
        # modo janela direta, útil quando não há bandeja disponível
        win = JanelaConfig(ao_salvar=None)
        win.connect("destroy", Gtk.main_quit)
        win.show_all()
        Gtk.main()
        return 0
    Aplicativo()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
