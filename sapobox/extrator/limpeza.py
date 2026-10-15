"""Desfaz o que uma extração criou quando ela falha ou é cancelada.

Só remove caminhos que não existiam antes da extração. Arquivos que já estavam
na pasta de destino ficam intocados. Se a pasta de destino não existia, a
extração a criou, e ela sai inteira.

Limite: um arquivo que já existia e foi sobrescrito (opção "Sobrescrever") não
volta ao conteúdo original, porque o 7-Zip já o substituiu.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def tamanho_em_disco(pasta: Path) -> int:
    """Bytes ocupados pelos arquivos de `pasta` (0 se ela não existe).

    Usado para medir o progresso: o que a extração já gravou no destino. Arquivos
    que somem durante a conta são ignorados.
    """
    if not pasta.is_dir():
        return 0
    total = 0
    for raiz, _pastas, arquivos in os.walk(pasta):
        for nome in arquivos:
            try:
                total += os.lstat(os.path.join(raiz, nome)).st_size
            except OSError:
                continue
    return total


def fotografar(pasta: Path) -> frozenset[str] | None:
    """Caminhos relativos que já existem em `pasta`. None se a pasta ainda não existe."""
    if not pasta.is_dir():
        return None
    return frozenset(str(caminho.relative_to(pasta)) for caminho in pasta.rglob("*"))


def remover_criados(pasta: Path, antes: frozenset[str] | None) -> int:
    """Apaga o que foi criado em `pasta` desde a fotografia. Devolve quantos itens saíram."""
    if not pasta.is_dir():
        return 0
    if antes is None:
        total = sum(1 for _ in pasta.rglob("*")) + 1
        shutil.rmtree(pasta)
        return total

    novos = [c for c in pasta.rglob("*") if str(c.relative_to(pasta)) not in antes]
    removidos = 0
    # Mais fundos primeiro: arquivos saem antes das pastas, e as pastas ficam vazias.
    for caminho in sorted(novos, key=lambda c: len(c.parts), reverse=True):
        try:
            if caminho.is_dir() and not caminho.is_symlink():
                caminho.rmdir()
            else:
                caminho.unlink()
        except OSError:
            continue
        removidos += 1
    return removidos
