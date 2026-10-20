#!/usr/bin/env python3
"""Testes do passo 2: máquina de estados do atalho.

Determinísticos: exercitam `ToggleGate` sem tocar em /dev/input.

    python3 test_dictate_unit.py
"""

from __future__ import annotations

import sys
import unittest

from dictate import MIN_TOGGLE_INTERVAL_S, ToggleGate, comando_reexec

PAUSE = 119
SCROLL = 70

PRESS, RELEASE, REPEAT = 1, 0, 2


class TestToggleGate(unittest.TestCase):
    def test_primeira_pressao_alterna(self):
        g = ToggleGate(PAUSE)
        self.assertTrue(g.feed(PAUSE, PRESS, 0.0))

    def test_autorepeat_nao_alterna(self):
        g = ToggleGate(PAUSE)
        g.feed(PAUSE, PRESS, 0.0)
        self.assertFalse(g.feed(PAUSE, REPEAT, 0.05))
        self.assertFalse(g.feed(PAUSE, REPEAT, 0.10))

    def test_pressao_repetida_sem_soltar_nao_alterna(self):
        g = ToggleGate(PAUSE)
        g.feed(PAUSE, PRESS, 0.0)
        self.assertFalse(g.feed(PAUSE, PRESS, 5.0))

    def test_soltar_e_apertar_de_novo_alterna(self):
        g = ToggleGate(PAUSE)
        self.assertTrue(g.feed(PAUSE, PRESS, 0.0))
        g.feed(PAUSE, RELEASE, 0.1)
        self.assertTrue(g.feed(PAUSE, PRESS, 1.0))

    def test_bounce_dentro_do_intervalo_e_suprimido(self):
        g = ToggleGate(PAUSE)
        self.assertTrue(g.feed(PAUSE, PRESS, 0.0))
        g.feed(PAUSE, RELEASE, 0.01)
        self.assertFalse(g.feed(PAUSE, PRESS, MIN_TOGGLE_INTERVAL_S / 2))

    def test_outra_tecla_e_ignorada(self):
        g = ToggleGate(PAUSE)
        self.assertFalse(g.feed(SCROLL, PRESS, 0.0))

    def test_sequencia_realista_tres_ciclos(self):
        # Simula apertar/soltar três vezes com pausas humanas.
        g = ToggleGate(PAUSE)
        t = 0.0
        alternancias = 0
        for _ in range(3):
            if g.feed(PAUSE, PRESS, t):
                alternancias += 1
            g.feed(PAUSE, RELEASE, t + 0.1)
            t += 2.0
        self.assertEqual(alternancias, 3)

    def test_nao_alterna_em_valor_desconhecido(self):
        g = ToggleGate(PAUSE)
        self.assertFalse(g.feed(PAUSE, 99, 0.0))

    def test_intervalo_customizado(self):
        g = ToggleGate(PAUSE, min_interval_s=5.0)
        self.assertTrue(g.feed(PAUSE, PRESS, 0.0))
        g.feed(PAUSE, RELEASE, 0.1)
        self.assertFalse(g.feed(PAUSE, PRESS, 1.0))
        g.feed(PAUSE, RELEASE, 1.1)
        self.assertTrue(g.feed(PAUSE, PRESS, 6.0))


class TestComandoReexec(unittest.TestCase):
    """Regressão real: o `sg` executa o comando via `sh -c`.

    Uma versão anterior colocou o `-u` antes do interpretador e o serviço entrou
    em crash-loop com `sh: 0: Illegal option -`. Os testes de shell não pegaram
    porque o ambiente de teste já tinha o grupo `input`, então o re-exec nunca
    era exercitado.
    """

    def _cmd(self, args=None):
        return comando_reexec("/usr/bin/python3", "/opt/vt/dictate.py", args or [])

    def test_primeiro_token_e_o_interpretador_nao_uma_flag(self):
        import shlex

        partes = shlex.split(self._cmd(["--refine"]))
        self.assertFalse(partes[0].startswith("-"), f"começa com flag: {partes[0]}")
        self.assertEqual(partes[0], "/usr/bin/python3")

    def test_flag_u_vem_depois_do_interpretador(self):
        import shlex

        partes = shlex.split(self._cmd())
        self.assertIn("-u", partes)
        self.assertEqual(partes.index("-u"), 1)

    def test_shell_consegue_interpretar(self):
        import subprocess

        r = subprocess.run(["sh", "-n", "-c", self._cmd(["--refine", "auto"])],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"sh rejeitou o comando: {r.stderr.strip()}")

    def test_argumentos_preservados(self):
        import shlex

        partes = shlex.split(self._cmd(["--key", "70"]))
        self.assertIn("--key", partes)
        self.assertIn("70", partes)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
