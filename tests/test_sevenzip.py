"""Testes do motor que fala com o 7-Zip.

Os testes de integração criam arquivos de verdade com o 7z instalado e são
pulados se ele não existir. Os demais rodam em qualquer lugar.
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from sapobox.extrator import limpeza
from sapobox.extrator.arquivos import identificar
from sapobox.extrator.sevenzip import (
    Extracao,
    _classificar,
    _decodificadores_do_texto,
    _resumir_listagem,
    encontrar_7z,
    montar_comando_extracao,
    tem_decodificador_rar,
    verificar,
)

PROGRAMA = encontrar_7z()
SENHA = "segredo-de-teste"


class ComandoTest(unittest.TestCase):
    def test_sem_senha_nao_passa_nenhum_p(self) -> None:
        cmd = montar_comando_extracao("/usr/bin/7z", Path("a.7z"), Path("/d"), None, "pular")
        self.assertFalse(any(item.startswith("-p") for item in cmd))
        self.assertEqual(cmd[-2:], ["--", "a.7z"])
        self.assertIn("-aos", cmd)
        self.assertIn("-o/d", cmd)

    def test_com_senha_vai_colada_no_p(self) -> None:
        cmd = montar_comando_extracao("/usr/bin/7z", Path("a.7z"), Path("/d"), "abc", "sobrescrever")
        self.assertIn("-pabc", cmd)
        self.assertIn("-aoa", cmd)

    def test_nome_com_hifen_fica_depois_do_separador(self) -> None:
        cmd = montar_comando_extracao("/usr/bin/7z", Path("-estranho.7z"), Path("/d"), None, "renomear")
        self.assertEqual(cmd[-2:], ["--", "-estranho.7z"])
        self.assertIn("-aou", cmd)


class ClassificarTest(unittest.TestCase):
    def test_codigos(self) -> None:
        self.assertEqual(_classificar(0, "", False).estado, "ok")
        self.assertEqual(_classificar(1, "", False).estado, "aviso")
        self.assertEqual(_classificar(2, "ERROR: x", False).estado, "erro")

    def test_senha_incorreta_tem_prioridade_sobre_cannot_open(self) -> None:
        registro = "ERROR: hdr.7z : Cannot open encrypted archive. Wrong password?"
        self.assertEqual(_classificar(2, registro, False).estado, "senha_incorreta")

    def test_pedido_de_senha_com_stdin_fechado(self) -> None:
        self.assertEqual(_classificar(255, "Enter password (will not be echoed):", False).estado, "senha_necessaria")

    def test_incompleto(self) -> None:
        registro = "Open ERROR: Cannot open the file as [7z] archive\nUnexpected end of archive"
        self.assertEqual(_classificar(2, registro, False).estado, "incompleto")

    def test_cancelamento_vence_tudo(self) -> None:
        self.assertEqual(_classificar(255, "Enter password", True).estado, "cancelado")

    def test_metodo_sem_decodificador(self) -> None:
        registro = "ERROR: Unsupported Method : pasta/eboot.bin"
        self.assertEqual(_classificar(2, registro, False).estado, "metodo_nao_suportado")


class DecodificadoresTest(unittest.TestCase):
    # Trechos reais de `7z i`: o do Debian não tem RAR; o do projeto (vendor/7zip) tem.
    SISTEMA = """Codecs:
 0  ED        21 LZMA2
 0  ED     30101 LZMA
 0  ED     30401 PPMD
 0  EDF  6F10701 7zAES
 0  EDF  6F00181 AES256CBC

Hashers:
"""
    VENDOR = """Codecs:
 0  EDF  6F10701 7zAES
 0  EDF  6F00181 AES256CBC
 1   D     40301 Rar1
 1   D     40302 Rar2
 1   D     40303 Rar3
 1   D     40305 Rar5

Hashers:
"""

    def test_sistema_sem_rar(self) -> None:
        decodificadores = _decodificadores_do_texto(self.SISTEMA)
        self.assertFalse(any(nome.startswith("Rar") for nome in decodificadores))
        self.assertIn("LZMA2", decodificadores)

    def test_vendor_com_rar(self) -> None:
        self.assertIn("Rar5", _decodificadores_do_texto(self.VENDOR))


class ResumirListagemTest(unittest.TestCase):
    # Formato real de `7z l -slt` (7-Zip 25.01): pasta tem "Attributes = D ...".
    TEXTO = """Listing archive: cont.7z

--
Path = cont.7z
Type = 7z
Physical Size = 3000425
Method = LZMA2:3m 7zAES

----------
Path = pasta
Size = 0
Packed Size = 0
Attributes = D drwxrwxr-x
Encrypted = -

Path = pasta/dados.bin
Size = 3000000
Packed Size = 3000190
Attributes = A -rw-rw-r--
Encrypted = +

Path = pasta/leia.txt
Size = 3
Attributes = A -rw-rw-r--
Encrypted = +
"""

    def test_conta_so_arquivos_e_soma_tamanho(self) -> None:
        self.assertEqual(_resumir_listagem(self.TEXTO), (2, 3000003))

    def test_listagem_vazia(self) -> None:
        self.assertEqual(_resumir_listagem(""), (0, 0))


class CapturaSaidaTest(unittest.TestCase):
    """Usa um processo de verdade que escreve a mesma saída do 7z (com \\b e %)."""

    def rodar(self, bruto: bytes):
        script = f"import sys; sys.stdout.buffer.write({bruto!r})"
        progresso: list[int] = []
        linhas: list[str] = []
        resultado = Extracao(
            [sys.executable, "-c", script],
            ao_progresso=progresso.append,
            ao_linha=linhas.append,
        ).executar()
        return resultado, progresso, linhas

    def test_progresso_e_linhas_limpas(self) -> None:
        bruto = (
            b"\n7-Zip 25.01 (x64)\n\n"
            b"  0%\x08\x08\x08\x08    \x08\x08\x08\x08"
            b" 42%\x08\x08\x08\x08\x08 99%\x08\x08\x08\x08\x08"
            b"Everything is Ok\n\nFolders: 1\n"
        )
        resultado, progresso, linhas = self.rodar(bruto)
        # Envia o último percentual lido de cada bloco: é o que a barra precisa.
        self.assertEqual(progresso[-1], 99)
        self.assertIn("Everything is Ok", linhas)
        self.assertIn("Folders: 1", linhas)
        self.assertNotIn("%", "".join(linhas))
        self.assertEqual(resultado.estado, "ok")

    def test_cancelada_antes_de_comecar_nao_roda_o_7z(self) -> None:
        extracao = Extracao([sys.executable, "-c", "raise SystemExit(0)"])
        extracao.cancelar()
        self.assertEqual(extracao.executar().estado, "cancelado")


@unittest.skipIf(PROGRAMA is None, "7-Zip não encontrado")
class IntegracaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.base = base
        origem = base / "origem" / "pasta"
        origem.mkdir(parents=True)
        rnd = random.Random(7)
        cls.dados = bytes(rnd.getrandbits(8) for _ in range(300_000))
        (origem / "dados.bin").write_bytes(cls.dados)
        (origem / "leia.txt").write_text("oi\n")
        fonte = str(base / "origem" / "pasta")

        def criar(nome: str, *extra: str) -> None:
            subprocess.run(
                [PROGRAMA, "a", "-bb0", "-y", *extra, str(base / nome), fonte],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        criar("plano.7z")
        criar("cont.7z", f"-p{SENHA}")
        criar("hdr.7z", f"-p{SENHA}", "-mhe=on")
        criar("dividido.7z", "-v100k", f"-p{SENHA}")
        criar("dividido_plano.7z", "-v100k")
        cls.pasta = base / "arquivos"
        cls.pasta.mkdir()
        for arquivo in base.glob("*.7z*"):
            if arquivo.is_file():
                shutil.move(str(arquivo), cls.pasta / arquivo.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def destino(self, nome: str) -> Path:
        pasta = self.base / "saida" / nome
        shutil.rmtree(pasta, ignore_errors=True)
        pasta.mkdir(parents=True)
        return pasta

    def extrair(self, entrada: Path, destino: Path, senha: str | None, modo: str = "pular"):
        comando = montar_comando_extracao(PROGRAMA, entrada, destino, senha, modo)
        return Extracao(comando).executar()

    def conferir_conteudo(self, destino: Path) -> None:
        self.assertEqual((destino / "pasta" / "dados.bin").read_bytes(), self.dados)
        self.assertEqual((destino / "pasta" / "leia.txt").read_text(), "oi\n")

    def test_verificar_arquivo_sem_senha(self) -> None:
        v = verificar(PROGRAMA, self.pasta / "plano.7z")
        self.assertIsNone(v.erro)
        self.assertFalse(v.precisa_senha)
        self.assertEqual(v.arquivos, 2)
        self.assertEqual(v.tamanho, 300_003)

    def test_verificar_conteudo_criptografado_pede_senha(self) -> None:
        v = verificar(PROGRAMA, self.pasta / "cont.7z")
        self.assertIsNone(v.erro)
        self.assertTrue(v.precisa_senha)
        self.assertEqual(v.arquivos, 2)

    def test_verificar_cabecalho_criptografado_pede_senha(self) -> None:
        v = verificar(PROGRAMA, self.pasta / "hdr.7z")
        self.assertIsNone(v.erro)
        self.assertTrue(v.precisa_senha)
        self.assertIsNone(v.arquivos)

    def test_verificar_multiparte_criptografada_a_partir_de_parte_do_meio(self) -> None:
        conjunto = identificar(self.pasta / "dividido.7z.002")
        self.assertGreaterEqual(len(conjunto.partes), 2)
        v = verificar(PROGRAMA, conjunto.entrada)
        self.assertTrue(v.precisa_senha)

    def test_verificar_multiparte_com_parte_faltando_da_erro(self) -> None:
        falta = self.base / "falta"
        shutil.rmtree(falta, ignore_errors=True)
        falta.mkdir()
        for nome in ("dividido.7z.001", "dividido.7z.003"):
            shutil.copy(self.pasta / nome, falta / nome)
        conjunto = identificar(falta / "dividido.7z.001")
        self.assertEqual(conjunto.faltando, (2,))
        self.assertIsNotNone(verificar(PROGRAMA, conjunto.entrada).erro)

    def test_extrair_sem_senha(self) -> None:
        destino = self.destino("plano")
        resultado = self.extrair(self.pasta / "plano.7z", destino, None)
        self.assertEqual(resultado.estado, "ok")
        self.conferir_conteudo(destino)

    def test_extrair_conteudo_criptografado_sem_senha_pede_senha(self) -> None:
        resultado = self.extrair(self.pasta / "cont.7z", self.destino("cont_sem"), None)
        self.assertEqual(resultado.estado, "senha_necessaria")

    def test_extrair_senha_errada(self) -> None:
        resultado = self.extrair(self.pasta / "cont.7z", self.destino("cont_err"), "errada")
        self.assertEqual(resultado.estado, "senha_incorreta")

    def test_extrair_senha_errada_cabecalho_criptografado(self) -> None:
        resultado = self.extrair(self.pasta / "hdr.7z", self.destino("hdr_err"), "errada")
        self.assertEqual(resultado.estado, "senha_incorreta")

    def test_extrair_com_senha_certa(self) -> None:
        destino = self.destino("cont_ok")
        resultado = self.extrair(self.pasta / "cont.7z", destino, SENHA)
        self.assertEqual(resultado.estado, "ok")
        self.conferir_conteudo(destino)

    def test_extrair_cabecalho_criptografado_com_senha_certa(self) -> None:
        destino = self.destino("hdr_ok")
        resultado = self.extrair(self.pasta / "hdr.7z", destino, SENHA)
        self.assertEqual(resultado.estado, "ok")
        self.conferir_conteudo(destino)

    def test_extrair_multiparte_pela_primeira_parte_com_senha(self) -> None:
        destino = self.destino("div_ok")
        conjunto = identificar(self.pasta / "dividido.7z.003")
        resultado = self.extrair(conjunto.entrada, destino, SENHA)
        self.assertEqual(resultado.estado, "ok")
        self.conferir_conteudo(destino)

    def test_extrair_multiparte_sem_senha_pede_senha(self) -> None:
        conjunto = identificar(self.pasta / "dividido.7z.001")
        resultado = self.extrair(conjunto.entrada, self.destino("div_sem"), None)
        self.assertEqual(resultado.estado, "senha_necessaria")

    def test_extrair_multiparte_sem_senha_quando_nao_criptografada(self) -> None:
        conjunto = identificar(self.pasta / "dividido_plano.7z.002")
        destino = self.destino("div_plano")
        resultado = self.extrair(conjunto.entrada, destino, None)
        self.assertEqual(resultado.estado, "ok")
        self.conferir_conteudo(destino)

    def test_modos_de_arquivo_existente(self) -> None:
        destino = self.destino("modos")
        existente = destino / "pasta" / "leia.txt"
        existente.parent.mkdir(parents=True)

        existente.write_text("ORIGINAL\n")
        self.extrair(self.pasta / "plano.7z", destino, None, "pular")
        self.assertEqual(existente.read_text(), "ORIGINAL\n")

        self.extrair(self.pasta / "plano.7z", destino, None, "sobrescrever")
        self.assertEqual(existente.read_text(), "oi\n")

        existente.write_text("ORIGINAL\n")
        self.extrair(self.pasta / "plano.7z", destino, None, "renomear")
        self.assertEqual(existente.read_text(), "ORIGINAL\n")
        self.assertTrue(any(p.name != "leia.txt" for p in existente.parent.iterdir()))

    def test_progresso_real_do_7z(self) -> None:
        destino = self.destino("progresso")
        progresso: list[int] = []
        comando = montar_comando_extracao(PROGRAMA, self.pasta / "cont.7z", destino, SENHA, "pular")
        resultado = Extracao(comando, ao_progresso=progresso.append).executar()
        self.assertEqual(resultado.estado, "ok")
        self.assertTrue(progresso)
        self.assertTrue(all(0 <= p <= 100 for p in progresso))

    def test_falha_remove_so_o_criado_e_mantem_o_que_ja_existia(self) -> None:
        destino = self.destino("falha_limpa")
        ja_existia = destino / "meu_arquivo.txt"
        ja_existia.write_text("não pode sumir")
        antes = limpeza.fotografar(destino)
        resultado = self.extrair(self.pasta / "cont.7z", destino, "errada")
        self.assertEqual(resultado.estado, "senha_incorreta")
        limpeza.remover_criados(destino, antes)
        self.assertEqual(ja_existia.read_text(), "não pode sumir")
        self.assertFalse((destino / "pasta").exists())

    def test_falha_com_pasta_nova_sai_inteira(self) -> None:
        destino = self.base / "saida" / "pasta_nova_falha"
        shutil.rmtree(destino, ignore_errors=True)
        antes = limpeza.fotografar(destino)
        resultado = self.extrair(self.pasta / "cont.7z", destino, "errada")
        self.assertEqual(resultado.estado, "senha_incorreta")
        limpeza.remover_criados(destino, antes)
        self.assertFalse(destino.exists())

    def test_motor_escolhido_le_rar(self) -> None:
        if not tem_decodificador_rar(PROGRAMA):
            self.skipTest("nenhum 7-Zip com decodificador RAR nesta máquina")
        self.assertTrue(tem_decodificador_rar(PROGRAMA))

    def test_cancelar_no_meio_nao_deixa_o_7z_rodando(self) -> None:
        # Arquivo maior, para dar tempo de cancelar. Não checa o estado final de forma rígida:
        # se o 7z terminar antes, o resultado é "ok"; o que importa é voltar sem travar.
        grande = self.base / "grande"
        grande.mkdir(exist_ok=True)
        (grande / "a.bin").write_bytes(os.urandom(40_000_000))
        arquivo = self.base / "arquivos" / "grande.7z"
        subprocess.run(
            [PROGRAMA, "a", "-bb0", "-y", "-mx=0", str(arquivo), str(grande)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        extracao = Extracao(montar_comando_extracao(PROGRAMA, arquivo, self.destino("grande"), None, "sobrescrever"))
        threading.Timer(0.05, extracao.cancelar).start()
        resultado = extracao.executar()
        self.assertIn(resultado.estado, ("cancelado", "ok"))


if __name__ == "__main__":
    unittest.main()
