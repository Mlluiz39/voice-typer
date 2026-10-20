#!/usr/bin/env python3
"""Testes das correções literais e regex — sem modelo, sem rede.

    .venv/bin/python test_correct_unit.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from correct import apply_corrections, load_corrections

# Padrão real usado em corrections.txt: cobre as variantes observadas de
# "tendo em vista que". Fica em raw string porque \b e \s são sintaxe de regex.
REGRA_VISTA = r"re:\b(?:e\s+)?t(?:enho|endo|em)\s+(?:em\s+|a\s+)?vista\s+que\b => tendo em vista que"


class BaseCorrecoes(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.arquivo = self.tmp / "corrections.txt"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _regras(self, conteudo: str):
        self.arquivo.write_text(conteudo + "\n", encoding="utf-8")
        return load_corrections(self.arquivo)


class TestLiteral(BaseCorrecoes):
    def test_correcao_simples(self):
        regras = self._regras("na meninite => na mesma forma")
        out, n = apply_corrections("é para verificar na meninite", regras)
        self.assertEqual(out, "é para verificar na mesma forma")
        self.assertEqual(n, 1)

    def test_casa_ignorando_caixa_e_preserva_caixa(self):
        regras = self._regras("na meninite => na mesma forma")
        out, _ = apply_corrections("NA MENINITE isso", regras)
        self.assertTrue(out.startswith("NA mesma forma") or out.startswith("Na mesma forma"), out)

    def test_preserva_maiuscula_inicial(self):
        regras = self._regras(REGRA_VISTA)
        out, _ = apply_corrections("Tenho em vista que sim", regras)
        self.assertTrue(out.startswith("Tendo em vista que"), out)

    def test_minuscula_continua_minuscula(self):
        regras = self._regras(REGRA_VISTA)
        out, _ = apply_corrections("disse que tem a vista que sim", regras)
        self.assertIn("disse que tendo em vista que sim", out)

    def test_respeita_fronteira_de_palavra(self):
        regras = self._regras("vista => visão")
        out, _ = apply_corrections("a vista do mar e a prevista", regras)
        self.assertEqual(out, "a visão do mar e a prevista")

    def test_comentarios_e_linhas_invalidas_ignorados(self):
        regras = self._regras("# comentario\n\nsem seta\nok => certo")
        self.assertEqual(len(regras), 1)

    def test_arquivo_ausente_nao_quebra(self):
        self.assertEqual(load_corrections(self.tmp / "nao_existe.txt"), [])

    def test_texto_vazio(self):
        regras = self._regras("a => b")
        self.assertEqual(apply_corrections("", regras), ("", 0))


class TestRegex(BaseCorrecoes):
    def test_cobre_variantes_do_mesmo_erro(self):
        # O erro de ASR muda de forma entre rodadas; uma regra literal só
        # alcançaria uma delas.
        regras = self._regras(REGRA_VISTA)
        for variante in ("e tem a vista que", "Tenho em vista que", "tenho em vista que"):
            out, n = apply_corrections(f"disse {variante} sim", regras)
            # a caixa é preservada do original, então compara sem caixa aqui
            self.assertIn("tendo em vista que sim", out.lower(), f"falhou para {variante!r}")
            self.assertEqual(n, 1, f"esperava 1 disparo para {variante!r}")

    def test_nao_conta_noop(self):
        regras = self._regras(REGRA_VISTA)
        out, n = apply_corrections("disse tendo em vista que sim", regras)
        self.assertEqual(n, 0, "texto já correto não deve contar como correção")
        self.assertIn("tendo em vista que sim", out)

    def test_regex_invalida_e_ignorada(self):
        regras = self._regras("re:[ => quebrada")
        self.assertEqual(regras, [])

    def test_expressao_ambigua_nao_tem_regra(self):
        # "na medida" é português válido: uma regra cega estragaria texto correto.
        # É justamente o tipo de erro que exige contexto, não regra.
        regras = self._regras("na meninite => na mesma forma")
        out, n = apply_corrections("resolvido na medida certa", regras)
        self.assertEqual((out, n), ("resolvido na medida certa", 0))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
