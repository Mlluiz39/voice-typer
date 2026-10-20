#!/usr/bin/env python3
"""Testes da classificação de áudio em record.py — sem microfone, sem rede.

O caso central é a regressão que originou esta função: um pop de abertura de
stream tem pico no teto mas energia baixa, e NÃO é saturação. Antes, o aviso
olhava só o pico e reportava saturação falsa em toda gravação.

    .venv/bin/python test_record_unit.py
"""

from __future__ import annotations

import sys
import unittest

from record import CLIP_PEAK_DB, CLIP_RMS_DB, SILENT_PEAK_DB, classify


class TestClassify(unittest.TestCase):
    def test_sem_medicao_assume_ok(self):
        self.assertEqual(classify(None, None), "ok")

    def test_silencio(self):
        self.assertEqual(classify(-64.3, -79.7), "mudo")
        self.assertEqual(classify(-70.0, -90.0), "mudo")

    def test_saturacao_real(self):
        # Corridas de amostras no teto são a assinatura física de saturação.
        self.assertEqual(classify(-0.2, -19.2, corridas=3), "saturado")
        self.assertEqual(classify(0.0, -12.0, corridas=1), "saturado")

    def test_pop_de_abertura_nao_e_saturacao(self):
        # Regressão: amostra isolada no teto NÃO é saturação. Era o falso
        # positivo original com o dongle USB no início do stream.
        self.assertEqual(classify(-0.21, -31.5, corridas=0), "ok")
        self.assertEqual(classify(0.0, -14.7, corridas=0), "ok")

    def test_fala_normal(self):
        self.assertEqual(classify(-19.5, -35.6, corridas=0), "ok")
        self.assertEqual(classify(-12.8, -28.2, corridas=0), "ok")

    def test_pico_alto_sem_corridas_e_transiente(self):
        # O caso que motivou a mudança: pico em 0 dB com corridas=0.
        self.assertEqual(classify(0.0, -3.3, corridas=0), "ok")

    def test_limiares_sao_os_documentados(self):
        # Guarda contra alguém mexer num limiar sem perceber o efeito.
        self.assertEqual(SILENT_PEAK_DB, -50.0)
        self.assertEqual(CLIP_PEAK_DB, -1.0)
        self.assertEqual(CLIP_RMS_DB, -30.0)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
