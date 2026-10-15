"""Identifica um arquivo compactado e as partes que fazem parte dele.

Recebe qualquer parte escolhida pelo usuário (.002, .part3.rar, .z02...) e
devolve o conjunto completo, na ordem certa, junto com o arquivo que deve ser
entregue ao 7-Zip. Não abre o arquivo e não executa o 7-Zip: só olha os nomes
na pasta, então a regra pode ser testada sozinha.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from ..i18n import tr  # noqa: E402

# Sufixos que não entram no nome da pasta de destino: "jogo.tar.gz" vira "jogo".
_SUFIXOS_COMPACTADOS = re.compile(
    r"\.(7z|zip|rar|tar|tgz|gz|bz2|xz|zst|iso|wim|cab)$", re.IGNORECASE
)

# Formatos de nome. Os testes usam fullmatch, do mais específico ao mais genérico.
_RAR_EM_PARTES = re.compile(r"(?P<base>.+)\.part\d+\.rar", re.IGNORECASE)
_DIVIDIDO = re.compile(r"(?P<base>.+)\.\d{3,}")
_RAR_ANTIGO = re.compile(r"(?P<base>.+)\.(?:rar|r\d{2,})", re.IGNORECASE)
_ZIP_FRACIONADO = re.compile(r"(?P<base>.+)\.(?:zip|z\d{2,})", re.IGNORECASE)


class ErroArquivo(Exception):
    """O arquivo escolhido não pode ser usado como ponto de partida."""


@dataclass(frozen=True)
class ConjuntoCompactado:
    entrada: Path  # arquivo que o 7-Zip recebe (sempre a primeira parte, ou o .zip/.rar principal)
    partes: tuple[Path, ...]  # todas as partes encontradas, na ordem
    rotulo: str  # nome sugerido para a pasta de destino
    descricao: str  # texto para a tela, ex.: "Dividido · 3 partes"
    faltando: tuple[int, ...]  # números das partes que não estão na pasta

    def tamanho_total(self) -> int:
        """Bytes ocupados pelas partes no disco (ainda compactados)."""
        return sum(parte.stat().st_size for parte in self.partes)


def identificar(caminho: str | Path) -> ConjuntoCompactado:
    """Devolve o conjunto de arquivos ao qual `caminho` pertence.

    Lança ErroArquivo quando o arquivo não existe ou quando falta uma parte
    anterior, por exemplo: o usuário escolheu o .002 mas o .001 não está na pasta.
    """
    arquivo = Path(caminho)
    if not arquivo.exists():
        raise ErroArquivo(tr('Arquivo não encontrado: {0}').format(arquivo))
    if not arquivo.is_file():
        raise ErroArquivo(tr("Escolha um arquivo, não uma pasta."))

    nome = arquivo.name
    if m := _RAR_EM_PARTES.fullmatch(nome):
        return _rar_em_partes(arquivo, m["base"])
    if m := _DIVIDIDO.fullmatch(nome):
        return _dividido(arquivo, m["base"])
    if m := _RAR_ANTIGO.fullmatch(nome):
        return _rar_antigo(arquivo, m["base"])
    if m := _ZIP_FRACIONADO.fullmatch(nome):
        return _zip_fracionado(arquivo, m["base"])
    return _conjunto([arquivo], arquivo, nome, tr("Arquivo único"), ())


def _dividido(arquivo: Path, base: str) -> ConjuntoCompactado:
    """Formato 7-Zip/split: nome.7z.001, nome.7z.002, ..."""
    achados = {
        int(m["num"]): caminho
        for m, caminho in _encontrar(arquivo.parent, re.escape(base) + r"\.(?P<num>\d{3,})")
    }
    if 1 not in achados:
        raise ErroArquivo(tr('Falta a primeira parte: {0}.001').format(base))
    return _conjunto(
        _em_ordem(achados), achados[1], base, _descricao(tr("Dividido"), len(achados)), _faltando(achados)
    )


def _rar_em_partes(arquivo: Path, base: str) -> ConjuntoCompactado:
    """RAR moderno em partes: nome.part1.rar, nome.part2.rar, ..."""
    achados = {
        int(m["num"]): caminho
        for m, caminho in _encontrar(
            arquivo.parent, re.escape(base) + r"\.part(?P<num>\d+)\.rar", re.IGNORECASE
        )
    }
    if 1 not in achados:
        raise ErroArquivo(tr('Falta a primeira parte: {0}.part1.rar').format(base))
    return _conjunto(
        _em_ordem(achados), achados[1], base, _descricao("RAR", len(achados)), _faltando(achados)
    )


def _rar_antigo(arquivo: Path, base: str) -> ConjuntoCompactado:
    """RAR antigo: nome.rar + nome.r00, nome.r01, ... O .rar é a primeira parte."""
    pasta = arquivo.parent
    primeira = _encontrar(pasta, re.escape(base) + r"\.rar", re.IGNORECASE)
    if not primeira:
        raise ErroArquivo(tr('Falta a primeira parte: {0}.rar').format(base))
    restantes = {
        int(m["num"]): caminho
        for m, caminho in _encontrar(pasta, re.escape(base) + r"\.r(?P<num>\d{2,})", re.IGNORECASE)
    }
    partes = [primeira[0][1], *_em_ordem(restantes)]
    if len(partes) == 1:
        return _conjunto(partes, primeira[0][1], base, tr("Arquivo único"), ())
    return _conjunto(partes, primeira[0][1], base, _descricao("RAR", len(partes)), ())


def _zip_fracionado(arquivo: Path, base: str) -> ConjuntoCompactado:
    """ZIP fracionado (PKZIP): nome.z01, nome.z02, ..., nome.zip. O .zip é a última parte e a entrada do 7-Zip."""
    pasta = arquivo.parent
    ultimo = _encontrar(pasta, re.escape(base) + r"\.zip", re.IGNORECASE)
    if not ultimo:
        raise ErroArquivo(tr('Falta o último arquivo: {0}.zip').format(base))
    zs = {
        int(m["num"]): caminho
        for m, caminho in _encontrar(pasta, re.escape(base) + r"\.z(?P<num>\d{2,})", re.IGNORECASE)
    }
    partes = [*_em_ordem(zs), ultimo[0][1]]
    if len(partes) == 1:
        return _conjunto(partes, ultimo[0][1], base, tr("Arquivo único"), ())
    return _conjunto(partes, ultimo[0][1], base, _descricao(tr("ZIP fracionado"), len(partes)), ())


def _encontrar(pasta: Path, padrao: str, flags: int = 0) -> list[tuple[re.Match[str], Path]]:
    """Arquivos da pasta cujo nome bate inteiro com `padrao`."""
    regex = re.compile(padrao, flags)
    achados = []
    for item in pasta.iterdir():
        m = regex.fullmatch(item.name)
        if m and item.is_file():
            achados.append((m, item))
    return achados


def _em_ordem(achados: dict[int, Path]) -> list[Path]:
    return [achados[n] for n in sorted(achados)]


def _faltando(achados: dict[int, Path]) -> tuple[int, ...]:
    """Números que faltam entre a primeira e a última parte encontrada."""
    if not achados:
        return ()
    return tuple(n for n in range(1, max(achados) + 1) if n not in achados)


def _descricao(prefixo: str, partes: int) -> str:
    return (tr('{0} · {1} partes') if partes != 1 else tr('{0} · {1} parte')).format(prefixo, partes)


def _rotulo(nome: str) -> str:
    """Tira os sufixos de compactado do nome: 'jogo.tar.gz' vira 'jogo'."""
    atual = nome
    while (novo := _SUFIXOS_COMPACTADOS.sub("", atual)) != atual:
        atual = novo
    return atual or nome


def _conjunto(
    partes: list[Path],
    entrada: Path,
    rotulo: str,
    descricao: str,
    faltando: tuple[int, ...],
) -> ConjuntoCompactado:
    return ConjuntoCompactado(
        entrada=entrada,
        partes=tuple(partes),
        rotulo=_rotulo(rotulo),
        descricao=descricao,
        faltando=faltando,
    )
