"""Testes do cálculo de velocidade e tempo restante. Não precisam de GTK nem do 7-Zip."""

import unittest

from sapobox.extrator.progresso import (
    Estimativa,
    Medidor,
    formatar_duracao,
    formatar_tamanho,
    linha_progresso,
)

GIB = 1024**3


class MedidorTest(unittest.TestCase):
    def test_sem_tempo_suficiente_nao_chuta_velocidade(self) -> None:
        m = Medidor(total=100)
        m.atualizar(0, 0.0)
        est = m.atualizar(5, 1.0)  # só 1 s de amostra
        self.assertIsNone(est.velocidade)
        self.assertIsNone(est.restante)

    def test_velocidade_e_restante_com_total(self) -> None:
        m = Medidor(total=100)
        m.atualizar(0, 0.0)
        est = m.atualizar(10, 10.0)  # 10 bytes em 10 s = 1 byte/s
        self.assertEqual(est.feito, 10)
        self.assertAlmostEqual(est.velocidade, 1.0)
        self.assertAlmostEqual(est.restante, 90.0)

    def test_janela_esquece_o_inicio_lento(self) -> None:
        # Começou devagar (0 a 10% em 10 s) e acelerou depois.
        # A janela de 15 s deve olhar só para o trecho recente.
        m = Medidor(total=100)
        m.atualizar(0, 0.0)
        m.atualizar(10, 10.0)
        est = m.atualizar(30, 30.0)  # últimos 20 s: de 10% a 30%
        self.assertAlmostEqual(est.velocidade, 1.0)  # 20 bytes em 20 s
        self.assertAlmostEqual(est.restante, 70.0)

    def test_progresso_parado_nao_mostra_velocidade(self) -> None:
        m = Medidor(total=100)
        m.atualizar(20, 0.0)
        est = m.atualizar(20, 30.0)
        self.assertIsNone(est.velocidade)
        self.assertIsNone(est.restante)

    def test_sem_total_so_estima_o_restante(self) -> None:
        m = Medidor(total=None)
        m.atualizar(0, 0.0)
        est = m.atualizar(50, 10.0)  # 50% em 10 s: restam 10 s
        self.assertIsNone(est.velocidade)
        self.assertIsNone(est.feito)
        self.assertAlmostEqual(est.restante, 10.0)

    def test_percentual_fora_da_faixa_e_limitado(self) -> None:
        m = Medidor(total=100)
        m.atualizar(0, 0.0)
        est = m.atualizar(140, 5.0)
        self.assertEqual(est.percentual, 100)
        self.assertEqual(est.feito, 100)


class FormatoTest(unittest.TestCase):
    def test_tamanho_com_gib(self) -> None:
        self.assertEqual(formatar_tamanho(int(90.5 * GIB)), "90,5 GiB")
        self.assertEqual(formatar_tamanho(512), "512 B")

    def test_duracao(self) -> None:
        self.assertEqual(formatar_duracao(390), "06:30")
        self.assertEqual(formatar_duracao(3723), "1:02:03")
        self.assertEqual(formatar_duracao(0), "00:00")

    def test_linha_completa_no_formato_do_conversor(self) -> None:
        est = Estimativa(
            percentual=57,
            total=160 * GIB,
            feito=int(91 * GIB),
            velocidade=182.4 * 1024**2,
            restante=390,
        )
        self.assertEqual(
            linha_progresso(est),
            "91,0 GiB de 160,0 GiB · 182,4 MiB/s · restam 06:30",
        )

    def test_linha_sem_total_mostra_porcentagem(self) -> None:
        est = Estimativa(percentual=40, total=None, feito=None, velocidade=None, restante=60)
        self.assertEqual(linha_progresso(est), "40% · restam 01:00")

    def test_linha_sem_estimativa_ainda(self) -> None:
        est = Estimativa(percentual=0, total=100, feito=0, velocidade=None, restante=None)
        self.assertEqual(linha_progresso(est), "0 B de 100 B · calculando a velocidade…")


if __name__ == "__main__":
    unittest.main()
