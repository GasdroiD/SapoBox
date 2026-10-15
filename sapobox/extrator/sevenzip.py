"""Conversa com o 7-Zip do sistema: achar o programa, verificar o arquivo e extrair.

Nada aqui depende de GTK. A janela chama estas funções em outra thread.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from ..i18n import tr  # noqa: E402

# Ordem de procura. A variável EXTRATOR_7Z força um programa específico.
# A cópia dentro do projeto (vendor/7zip) é a primeira: traz os decodificadores RAR
# (Codecs/Rar.so). O 7-Zip do Debian lê 7z e ZIP, mas não tem RAR: a lista de
# codecs de `7z i` não traz Rar1/2/3/5 nele.
_RAIZ = Path(__file__).resolve().parent.parent.parent
_CAMINHOS_PADRAO = (
    str(_RAIZ / "vendor/7zip/7z"),
    "/usr/bin/7z",
    "/usr/bin/7zz",
    "/usr/lib/7zip/7z",
)
_NOMES_PROCURADOS = ("7zz", "7z", "7za")

# Opção de sobrescrita do 7z para cada escolha da tela.
MODOS_EXISTENTE = {"pular": "-aos", "sobrescrever": "-aoa", "renomear": "-aou"}

# Texto mostrado na tela para cada estado da extração.
MENSAGENS = {
    "ok": tr("Extração concluída."),
    "aviso": tr("Concluído com avisos. Veja o registro."),
    "senha_necessaria": tr("Este arquivo precisa de senha."),
    "senha_incorreta": tr("Senha incorreta."),
    "incompleto": tr("Arquivo incompleto: pode estar faltando alguma parte."),
    "metodo_nao_suportado": (
        tr("O 7-Zip usado não tem o decodificador deste formato. "
        "Para RAR, é preciso um 7-Zip que o tenha (veja o README).")
    ),
    "cancelado": tr("Extração cancelada."),
    "erro": tr("A extração falhou. Veja o registro."),
}

_PORCENTAGEM = re.compile(rb"(?<!\d)(\d{1,3})%")
_TRECHO_PORCENTAGEM = re.compile(rb"\s*\d{1,3}%\s*")
_CRIPTOGRAFADO = re.compile(r"^Encrypted = \+$", re.MULTILINE)


def encontrar_7z() -> str | None:
    """Caminho do 7-Zip a usar: o primeiro que lê RAR; se nenhum ler, o primeiro que existir."""
    forcado = os.environ.get("EXTRATOR_7Z")
    if forcado and os.access(forcado, os.X_OK):
        return forcado
    encontrados = []
    for caminho in _CAMINHOS_PADRAO:
        if os.access(caminho, os.X_OK):
            encontrados.append(caminho)
    for nome in _NOMES_PROCURADOS:
        if achado := shutil.which(nome):
            encontrados.append(achado)
    if not encontrados:
        return None
    for caminho in encontrados:
        if tem_decodificador_rar(caminho):
            return caminho
    return encontrados[0]


def tem_decodificador_rar(programa: str) -> bool:
    return any(nome.startswith("Rar") for nome in _decodificadores(programa))


def _decodificadores(programa: str) -> set[str]:
    """Nomes dos decodificadores listados por `7z i` (linhas com "D" nas flags)."""
    try:
        saida = subprocess.run(
            [programa, "i"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=20,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return _decodificadores_do_texto(saida)


def _decodificadores_do_texto(saida: str) -> set[str]:
    decodificadores = set()
    na_secao = False
    for linha in saida.splitlines():
        if linha.startswith("Codecs:"):
            na_secao = True
            continue
        if na_secao:
            if not linha.strip():
                break
            partes = linha.split()
            if len(partes) >= 4 and "D" in partes[1]:
                decodificadores.add(partes[-1])
    return decodificadores


@dataclass(frozen=True)
class Verificacao:
    """O que o 7-Zip informou sobre o arquivo, antes de extrair."""

    precisa_senha: bool = False
    erro: str | None = None  # motivo, quando o 7-Zip não consegue abrir o arquivo
    arquivos: int | None = None  # quantidade de arquivos dentro (None se a lista é oculta)
    tamanho: int | None = None  # bytes depois de descompactar (None se a lista é oculta)


def verificar(programa: str, entrada: Path, tempo_limite: float = 120) -> Verificacao:
    """Lista o arquivo sem senha para descobrir se ela é necessária.

    Com stdin fechado o 7z não fica esperando digitação: quando precisa de senha
    para ler a lista, ele avisa e sai.
    """
    try:
        processo = subprocess.run(
            [programa, "l", "-slt", "--", str(entrada)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=tempo_limite,
        )
    except subprocess.TimeoutExpired:
        return Verificacao(erro=tr("O 7-Zip demorou demais para ler o arquivo."))

    texto = processo.stdout.decode("utf-8", "replace")
    if processo.returncode == 0:
        # Conteúdo criptografado: a lista aparece, mas cada arquivo traz "Encrypted = +".
        arquivos, tamanho = _resumir_listagem(texto)
        return Verificacao(
            precisa_senha=bool(_CRIPTOGRAFADO.search(texto)),
            arquivos=arquivos,
            tamanho=tamanho,
        )
    if _pede_senha(texto):
        # Cabeçalho criptografado: nem a lista aparece sem a senha.
        return Verificacao(precisa_senha=True)
    return Verificacao(erro=_mensagem_de_erro(texto))


def montar_comando_extracao(
    programa: str,
    entrada: Path,
    destino: Path,
    senha: str | None,
    modo: str,
) -> list[str]:
    """Linha do 7z para extrair. A senha só entra se foi digitada."""
    comando = [programa, "x", "-y", "-bsp1", MODOS_EXISTENTE[modo], f"-o{destino}"]
    if senha:
        comando.append(f"-p{senha}")
    comando += ["--", str(entrada)]
    return comando


@dataclass(frozen=True)
class Resultado:
    estado: str  # chave de MENSAGENS
    codigo: int | None  # código de saída do 7z
    registro: str  # linhas que o 7z escreveu, sem as barras de progresso

    @property
    def mensagem(self) -> str:
        return MENSAGENS[self.estado]


class Extracao:
    """Uma execução do 7z. `executar` bloqueia, então rode em outra thread."""

    def __init__(
        self,
        comando: list[str],
        ao_progresso: Callable[[int], None] | None = None,
        ao_linha: Callable[[str], None] | None = None,
    ) -> None:
        self._comando = comando
        self._ao_progresso = ao_progresso
        self._ao_linha = ao_linha
        self._lock = threading.Lock()
        self._processo: subprocess.Popen[bytes] | None = None
        self._cancelada = False
        self._linhas: list[str] = []

    def executar(self) -> Resultado:
        with self._lock:
            if self._cancelada:
                return Resultado("cancelado", None, "")
            # Sessão própria: o cancelamento mata o grupo inteiro, sem deixar processo órfão.
            self._processo = subprocess.Popen(
                self._comando,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        processo = self._processo
        restante = b""
        try:
            while pedaco := processo.stdout.read1(4096):
                if percentuais := _PORCENTAGEM.findall(pedaco):
                    if self._ao_progresso:
                        self._ao_progresso(int(percentuais[-1]))
                # O 7z separa as atualizações com \b, e só quebra linha ao terminar algo.
                *linhas, restante = re.split(rb"[\r\n]", restante + pedaco)
                for linha in linhas:
                    self._emitir(linha)
        finally:
            processo.stdout.close()
        self._emitir(restante)

        codigo = processo.wait()
        with self._lock:
            cancelada = self._cancelada
        return _classificar(codigo, "\n".join(self._linhas), cancelada)

    def cancelar(self) -> None:
        with self._lock:
            self._cancelada = True
            processo = self._processo
        if processo is not None and processo.poll() is None:
            try:
                os.killpg(processo.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _emitir(self, linha: bytes) -> None:
        limpa = _TRECHO_PORCENTAGEM.sub(b"", linha).replace(b"\x08", b"").strip()
        if not limpa:
            return
        texto = limpa.decode("utf-8", "replace")
        self._linhas.append(texto)
        if self._ao_linha:
            self._ao_linha(texto)


def _classificar(codigo: int, registro: str, cancelada: bool) -> Resultado:
    # Códigos do 7z: 0 ok, 1 aviso, 2 erro fatal, 255 parou pedindo senha (stdin fechado).
    if cancelada:
        estado = "cancelado"
    elif codigo == 0:
        estado = "ok"
    elif codigo == 1:
        estado = "aviso"
    elif "Wrong password" in registro:
        estado = "senha_incorreta"
    elif "Enter password" in registro or codigo == 255:
        estado = "senha_necessaria"
    elif "Unsupported Method" in registro:
        estado = "metodo_nao_suportado"
    elif "Unexpected end" in registro or "Cannot open the file as" in registro:
        estado = "incompleto"
    else:
        estado = "erro"
    return Resultado(estado, codigo, registro)


def _pede_senha(texto: str) -> bool:
    return any(
        trecho in texto
        for trecho in ("Enter password", "Wrong password", "Cannot open encrypted archive")
    )


def _mensagem_de_erro(texto: str) -> str:
    if "Unsupported" in texto:
        return tr("Formato ou método de compactação não suportado pelo 7-Zip.")
    if "Unexpected end" in texto or "Cannot open the file as" in texto:
        return tr("Não parece um arquivo compactado válido, ou falta alguma parte.")
    return tr("O 7-Zip não conseguiu abrir o arquivo.")


def _resumir_listagem(texto: str) -> tuple[int, int]:
    """Conta os arquivos e soma o tamanho descompactado de `7z l -slt`."""
    arquivos = 0
    tamanho = 0
    for bloco in texto.split("\n\n"):
        campos = {}
        for linha in bloco.splitlines():
            chave, separador, valor = linha.partition(" = ")
            if separador:
                campos[chave] = valor
        # Só entradas têm "Size" (o cabeçalho do arquivo tem "Physical Size").
        # Pasta aparece como "Folder = +" ou com atributo "D" (ex.: "Attributes = D drwxr-xr-x").
        if "Path" not in campos or not campos.get("Size", "").isdigit():
            continue
        if campos.get("Folder") == "+" or campos.get("Attributes", "").startswith("D"):
            continue
        arquivos += 1
        tamanho += int(campos["Size"])
    return arquivos, tamanho
