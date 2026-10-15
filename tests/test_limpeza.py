"""Testes da limpeza: só remove o que a extração criou."""

import tempfile
import unittest
from pathlib import Path

from sapobox.extrator import limpeza


class LimpezaTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.raiz = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_pasta_criada_pela_extracao_sai_inteira(self) -> None:
        destino = self.raiz / "jogo"
        antes = limpeza.fotografar(destino)  # não existe
        self.assertIsNone(antes)
        (destino / "sub").mkdir(parents=True)
        (destino / "sub" / "a.bin").write_bytes(b"x")
        removidos = limpeza.remover_criados(destino, antes)
        self.assertFalse(destino.exists())
        self.assertEqual(removidos, 3)  # jogo/, sub/ e a.bin

    def test_pasta_existente_so_perde_o_que_foi_criado(self) -> None:
        destino = self.raiz / "saida"
        (destino / "sub").mkdir(parents=True)
        (destino / "ja_estava.txt").write_text("meu")
        (destino / "sub" / "tambem_estava.txt").write_text("meu")

        antes = limpeza.fotografar(destino)
        (destino / "novo.txt").write_text("extraido")
        (destino / "sub" / "novo2.txt").write_text("extraido")
        (destino / "nova_pasta" / "fundo").mkdir(parents=True)
        (destino / "nova_pasta" / "fundo" / "x.txt").write_text("extraido")

        limpeza.remover_criados(destino, antes)

        self.assertEqual((destino / "ja_estava.txt").read_text(), "meu")
        self.assertEqual((destino / "sub" / "tambem_estava.txt").read_text(), "meu")
        self.assertFalse((destino / "novo.txt").exists())
        self.assertFalse((destino / "sub" / "novo2.txt").exists())
        self.assertFalse((destino / "nova_pasta").exists())

    def test_tamanho_em_disco_soma_os_arquivos(self) -> None:
        destino = self.raiz / "medida"
        (destino / "sub").mkdir(parents=True)
        (destino / "a.bin").write_bytes(b"x" * 100)
        (destino / "sub" / "b.bin").write_bytes(b"y" * 250)
        self.assertEqual(limpeza.tamanho_em_disco(destino), 350)
        self.assertEqual(limpeza.tamanho_em_disco(self.raiz / "nada"), 0)

    def test_pasta_inexistente_nao_da_erro(self) -> None:
        self.assertEqual(limpeza.remover_criados(self.raiz / "nada", None), 0)

    def test_arquivo_no_lugar_da_pasta_nao_e_apagado(self) -> None:
        arquivo = self.raiz / "nao_e_pasta"
        arquivo.write_text("importante")
        self.assertEqual(limpeza.remover_criados(arquivo, None), 0)
        self.assertEqual(arquivo.read_text(), "importante")


if __name__ == "__main__":
    unittest.main()
