"""Testes da identificação de partes. Não precisam do 7-Zip nem de display."""

import tempfile
import unittest
from pathlib import Path

from sapobox.extrator.arquivos import ErroArquivo, identificar


class IdentificarPartesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.pasta = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def criar(self, *nomes: str, tamanho: int = 10) -> None:
        for nome in nomes:
            (self.pasta / nome).write_bytes(b"x" * tamanho)

    def nomes(self, conjunto) -> list[str]:
        return [p.name for p in conjunto.partes]

    def test_arquivo_unico(self) -> None:
        self.criar("jogo.7z")
        c = identificar(self.pasta / "jogo.7z")
        self.assertEqual(c.entrada.name, "jogo.7z")
        self.assertEqual(self.nomes(c), ["jogo.7z"])
        self.assertEqual(c.rotulo, "jogo")
        self.assertEqual(c.faltando, ())

    def test_rotulo_tira_tar_gz(self) -> None:
        self.criar("backup.tar.gz")
        self.assertEqual(identificar(self.pasta / "backup.tar.gz").rotulo, "backup")

    def test_dividido_escolhendo_segunda_parte_volta_para_o_primeiro(self) -> None:
        self.criar("filme.7z.001", "filme.7z.002", "filme.7z.003")
        c = identificar(self.pasta / "filme.7z.002")
        self.assertEqual(c.entrada.name, "filme.7z.001")
        self.assertEqual(self.nomes(c), ["filme.7z.001", "filme.7z.002", "filme.7z.003"])
        self.assertEqual(c.rotulo, "filme")
        self.assertEqual(c.faltando, ())
        self.assertEqual(c.descricao, "Dividido · 3 partes")

    def test_dividido_mostra_partes_faltando(self) -> None:
        self.criar("filme.7z.001", "filme.7z.003", "filme.7z.004")
        c = identificar(self.pasta / "filme.7z.001")
        self.assertEqual(c.faltando, (2,))

    def test_dividido_sem_primeira_parte_da_erro(self) -> None:
        self.criar("filme.7z.002", "filme.7z.003")
        with self.assertRaisesRegex(ErroArquivo, r"filme\.7z\.001"):
            identificar(self.pasta / "filme.7z.002")

    def test_rar_em_partes_a_partir_de_qualquer_parte(self) -> None:
        self.criar("jogo.part01.rar", "jogo.part02.rar", "jogo.part03.rar")
        c = identificar(self.pasta / "jogo.part03.rar")
        self.assertEqual(c.entrada.name, "jogo.part01.rar")
        self.assertEqual(
            self.nomes(c), ["jogo.part01.rar", "jogo.part02.rar", "jogo.part03.rar"]
        )
        self.assertEqual(c.rotulo, "jogo")

    def test_rar_em_partes_com_lacuna(self) -> None:
        self.criar("jogo.part1.rar", "jogo.part3.rar")
        c = identificar(self.pasta / "jogo.part1.rar")
        self.assertEqual(c.faltando, (2,))

    def test_rar_em_partes_sem_primeira_parte_da_erro(self) -> None:
        self.criar("jogo.part2.rar", "jogo.part3.rar")
        with self.assertRaises(ErroArquivo):
            identificar(self.pasta / "jogo.part2.rar")

    def test_rar_antigo_escolhendo_r01(self) -> None:
        self.criar("x.rar", "x.r00", "x.r01")
        c = identificar(self.pasta / "x.r01")
        self.assertEqual(c.entrada.name, "x.rar")
        self.assertEqual(self.nomes(c), ["x.rar", "x.r00", "x.r01"])
        self.assertEqual(c.rotulo, "x")

    def test_rar_antigo_sem_rar_principal_da_erro(self) -> None:
        self.criar("x.r00", "x.r01")
        with self.assertRaisesRegex(ErroArquivo, "x.rar"):
            identificar(self.pasta / "x.r00")

    def test_rar_unico(self) -> None:
        self.criar("solto.rar")
        c = identificar(self.pasta / "solto.rar")
        self.assertEqual(self.nomes(c), ["solto.rar"])
        self.assertEqual(c.descricao, "Arquivo único")

    def test_zip_fracionado_entrada_e_o_zip(self) -> None:
        self.criar("a.z01", "a.z02", "a.zip")
        c = identificar(self.pasta / "a.z02")
        self.assertEqual(c.entrada.name, "a.zip")
        self.assertEqual(self.nomes(c), ["a.z01", "a.z02", "a.zip"])
        self.assertEqual(c.rotulo, "a")

    def test_zip_comum_e_arquivo_unico(self) -> None:
        self.criar("fotos.zip")
        c = identificar(self.pasta / "fotos.zip")
        self.assertEqual(self.nomes(c), ["fotos.zip"])
        self.assertEqual(c.entrada.name, "fotos.zip")

    def test_arquivo_qualquer_vira_unico(self) -> None:
        self.criar("leiame.txt")
        c = identificar(self.pasta / "leiame.txt")
        self.assertEqual(self.nomes(c), ["leiame.txt"])

    def test_tamanho_total_soma_as_partes(self) -> None:
        self.criar("m.7z.001", "m.7z.002", tamanho=100)
        self.assertEqual(identificar(self.pasta / "m.7z.001").tamanho_total(), 200)

    def test_arquivo_inexistente(self) -> None:
        with self.assertRaises(ErroArquivo):
            identificar(self.pasta / "nao_existe.7z")

    def test_pasta_nao_e_arquivo(self) -> None:
        (self.pasta / "sub").mkdir()
        with self.assertRaisesRegex(ErroArquivo, "pasta"):
            identificar(self.pasta / "sub")

    def test_nao_pega_arquivos_de_outro_conjunto(self) -> None:
        # "outro.7z.001" não pertence ao conjunto "filme"
        self.criar("filme.7z.001", "filme.7z.002", "outro.7z.001", "outro.7z.002")
        c = identificar(self.pasta / "filme.7z.001")
        self.assertEqual(self.nomes(c), ["filme.7z.001", "filme.7z.002"])


if __name__ == "__main__":
    unittest.main()
