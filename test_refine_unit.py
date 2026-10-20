#!/usr/bin/env python3
"""Testes do refino por LLM — com transporte falso, sem rede e sem chave.

    .venv/bin/python test_refine_unit.py
"""

from __future__ import annotations

import sys
import unittest

from refine import MAX_RATIO, MIN_RATIO, Refiner, needs_refine

TEXTO = "Vamos la verificar se nao vai ter algum enorte nas palavras do ditado."
BOM = "Vamos lá verificar se não vai ter algum erro nas palavras do ditado."


def refiner(resposta=None, *, exc=None, api_key="x", base_url="https://exemplo/v1"):
    def transport(_messages):
        if exc is not None:
            raise exc
        return resposta

    return Refiner(api_key=api_key, base_url=base_url, transport=transport)


class TestRefino(unittest.TestCase):
    def test_resposta_boa_refina(self):
        novo, ok, motivo = refiner(BOM).refine(TEXTO)
        self.assertTrue(ok)
        self.assertEqual(novo, BOM)
        self.assertEqual(motivo, "ok")

    def test_texto_vazio_nao_chama_llm(self):
        novo, ok, motivo = refiner(BOM).refine("   ")
        self.assertFalse(ok)
        self.assertIn("vazio", motivo)

    def test_sem_chave_em_servico_remoto_nao_chama(self):
        novo, ok, motivo = refiner(BOM, api_key=None).refine(TEXTO)
        self.assertFalse(ok)
        self.assertEqual(novo, TEXTO)
        self.assertIn("VT_LLM_API_KEY", motivo)

    def test_ollama_local_dispensa_chave(self):
        novo, ok, _ = refiner(BOM, api_key=None, base_url="http://127.0.0.1:11434/v1").refine(TEXTO)
        self.assertTrue(ok, "serviço local não deve exigir chave")

    def test_falha_de_rede_mantem_original(self):
        novo, ok, motivo = refiner(exc=OSError("conexão recusada")).refine(TEXTO)
        self.assertFalse(ok)
        self.assertEqual(novo, TEXTO)
        self.assertIn("rede", motivo)

    def test_resposta_vazia_mantem_original(self):
        novo, ok, _ = refiner("   ").refine(TEXTO)
        self.assertFalse(ok)
        self.assertEqual(novo, TEXTO)

    def test_resposta_resumida_e_descartada(self):
        # O pior defeito possível no polimento é apagar conteúdo.
        resumo = TEXTO[: int(len(TEXTO) * (MIN_RATIO - 0.1))]
        novo, ok, motivo = refiner(resumo).refine(TEXTO)
        self.assertFalse(ok)
        self.assertEqual(novo, TEXTO)
        self.assertIn("suspeito", motivo)

    def test_resposta_gigante_e_descartada(self):
        enorme = "palavra " * int(len(TEXTO) * (MAX_RATIO + 0.5))
        novo, ok, motivo = refiner(enorme).refine(TEXTO)
        self.assertFalse(ok)
        self.assertEqual(novo, TEXTO)
        self.assertIn("suspeito", motivo)

    def test_aspas_da_resposta_sao_removidas(self):
        novo, ok, _ = refiner(f'"{BOM}"').refine(TEXTO)
        self.assertTrue(ok)
        self.assertEqual(novo, BOM)

    def test_resposta_identica_conta_como_sem_alteracao(self):
        novo, ok, motivo = refiner(TEXTO).refine(TEXTO)
        self.assertFalse(ok)
        self.assertEqual(motivo, "sem alterações")

    def test_resposta_malformada_mantem_original(self):
        def transport(_m):
            raise KeyError("choices")

        r = Refiner(api_key="x", base_url="https://exemplo/v1", transport=transport)
        novo, ok, motivo = r.refine(TEXTO)
        self.assertFalse(ok)
        self.assertEqual(novo, TEXTO)
        self.assertIn("inesperada", motivo)


class TestHeuristica(unittest.TestCase):
    """O refino custa 6-7 s; só vale quando há sinal de problema."""

    def test_texto_limpo_nao_refina(self):
        ok, motivo = needs_refine(
            "Preciso mandar uma mensagem para o cliente dizendo que o orçamento "
            "ficou pronto até sexta-feira."
        )
        self.assertFalse(ok, motivo)

    def test_minuscula_apos_ponto_refina(self):
        ok, motivo = needs_refine("Terminei o relatório. ele ficou bom.")
        self.assertTrue(ok)
        self.assertIn("minúscula", motivo)

    def test_palavra_repetida_refina(self):
        ok, motivo = needs_refine("eu acho que que isso está errado")
        self.assertTrue(ok)
        self.assertIn("repetida", motivo)

    def test_texto_vazio_nao_refina(self):
        self.assertFalse(needs_refine("   ")[0])

    def test_texto_curto_limpo_nao_refina(self):
        self.assertFalse(needs_refine("Reunião amanhã às nove.")[0])


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
