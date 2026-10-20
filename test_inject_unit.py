#!/usr/bin/env python3
"""Testes da lógica pura do injetor — sem GUI, sem foco, determinísticos.

Cobre o que decide se o texto vai chegar certo no campo: normalização,
fatiamento e escolha de backend. A injeção real (que depende do compositor e do
foco) fica em `demo-inject.sh`, porque em sessão gráfica viva ela não é
determinística.

    python3 test_inject_unit.py
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout

import inject


class TestSanitize(unittest.TestCase):
    def test_remove_newline_final(self):
        # Newline final viraria Enter e submeteria o prompt no meio do ditado.
        self.assertEqual(inject.sanitize("olá mundo\n", keep_newlines=False), "olá mundo")
        self.assertEqual(inject.sanitize("olá mundo\n\n\n", keep_newlines=False), "olá mundo")

    def test_colapsa_newlines_internos(self):
        self.assertEqual(
            inject.sanitize("primeira linha\nsegunda linha", keep_newlines=False),
            "primeira linha segunda linha",
        )

    def test_preserva_newlines_internos_quando_pedido(self):
        self.assertEqual(
            inject.sanitize("linha um\nlinha dois\n", keep_newlines=True),
            "linha um\nlinha dois",
        )

    def test_normaliza_crlf(self):
        self.assertEqual(inject.sanitize("a\r\nb\r\n", keep_newlines=True), "a\nb")

    def test_remove_linhas_vazias_ao_colapsar(self):
        self.assertEqual(inject.sanitize("a\n\n\nb\n", keep_newlines=False), "a b")

    def test_texto_so_de_espaco_fica_vazio(self):
        self.assertEqual(inject.sanitize("\n \n\n", keep_newlines=False), "")

    def test_preserva_acentos_e_emoji(self):
        texto = "coração não você — “aspas” 😀"
        self.assertEqual(inject.sanitize(texto, keep_newlines=False), texto)

    def test_preserva_codigo(self):
        texto = "if (x != null) return;"
        self.assertEqual(inject.sanitize(texto, keep_newlines=False), texto)


class TestChunking(unittest.TestCase):
    def test_texto_vazio_nao_gera_pedaco(self):
        self.assertEqual(inject.chunk_text(""), [])

    def test_texto_curto_um_pedaco(self):
        self.assertEqual(inject.chunk_text("curto", size=500), ["curto"])

    def test_divisao_respeita_tamanho(self):
        pedacos = inject.chunk_text("a" * 1200, size=500)
        self.assertEqual(len(pedacos), 3)
        self.assertEqual([len(p) for p in pedacos], [500, 500, 200])

    def test_reconstroi_o_original(self):
        texto = "coração " * 200
        self.assertEqual("".join(inject.chunk_text(texto)), texto)

    def test_nao_corta_emoji_no_meio(self):
        texto = "😀" * 400
        pedacos = inject.chunk_text(texto, size=7)
        self.assertEqual("".join(pedacos), texto)


class TestBackendSelection(unittest.TestCase):
    def test_type_dry_run_nao_executa(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = inject.type_text("coração", 8, dry_run=True)
        self.assertTrue(ok)
        self.assertIn("coração", buf.getvalue())

    def test_type_sem_wtype_retorna_false(self):
        original = inject.have
        inject.have = lambda exe: False
        try:
            self.assertFalse(inject.type_text("x", 8, dry_run=True))
        finally:
            inject.have = original

    def test_paste_sem_wlcopy_retorna_false(self):
        original = inject.have
        inject.have = lambda exe: False
        try:
            self.assertFalse(inject.paste_text("x", False, False, False))
        finally:
            inject.have = original


class TestCli(unittest.TestCase):
    def test_sem_texto_e_sem_check_e_erro(self):
        with self.assertRaises(SystemExit) as ctx:
            inject.main([])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_texto_so_espaco_sai_zero(self):
        self.assertEqual(inject.main(["--dry-run", "\n\n"]), 0)

    def test_dry_run_paste_menciona_atalho_de_terminal(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                inject.main(["--method", "paste", "--terminal", "--dry-run", "texto"])
            except SystemExit:
                pass
        self.assertIn("Ctrl+Shift+V", buf.getvalue())


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
