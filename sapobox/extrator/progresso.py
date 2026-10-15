"""Velocidade, tempo restante e textos do progresso da extração.

O 7-Zip informa só a porcentagem. Com o tamanho total descompactado (vindo da
listagem) dá para mostrar "de X · velocidade · restam mm:ss", como o Conversor.
Sem o total, o tempo restante ainda sai da porcentagem.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from ..i18n import tr  # noqa: E402

# Média móvel: velocidade dos últimos segundos, para não pular a cada atualização.
JANELA_SEGUNDOS = 15.0
# Antes disso a velocidade ainda é ruído: mostra "calculando".
MINIMO_SEGUNDOS = 2.0


@dataclass(frozen=True)
class Estimativa:
    percentual: int
    total: int | None  # bytes descompactados, se a listagem os informou
    feito: int | None  # bytes já extraídos (percentual * total)
    velocidade: float | None  # bytes/s com total conhecido; senão None
    restante: float | None  # segundos até o fim, quando há como estimar


class Medidor:
    """Recebe percentuais com a hora em que chegaram e devolve a estimativa."""

    def __init__(self, total: int | None = None) -> None:
        self._total = total if total else None
        self._amostras: deque[tuple[float, float]] = deque()  # (instante, fração 0..1)

    @property
    def total(self) -> int | None:
        return self._total

    def atualizar(self, percentual: float, agora: float) -> Estimativa:
        fracao = min(max(percentual, 0.0), 100.0) / 100
        self._amostras.append((agora, fracao))
        while len(self._amostras) > 2 and agora - self._amostras[0][0] > JANELA_SEGUNDOS:
            self._amostras.popleft()

        instante, inicial = self._amostras[0]
        velocidade = None
        restante = None
        if agora - instante >= MINIMO_SEGUNDOS and fracao > inicial:
            taxa = (fracao - inicial) / (agora - instante)  # fração por segundo
            restante = (1 - fracao) / taxa
            velocidade = taxa * self._total if self._total else None

        feito = round(fracao * self._total) if self._total else None
        return Estimativa(
            percentual=round(fracao * 100),
            total=self._total,
            feito=feito,
            velocidade=velocidade,
            restante=restante,
        )


def formatar_tamanho(bytes_: float) -> str:
    """1234567890 vira '1,1 GiB', com vírgula como no resto da interface."""
    unidades = ["B", "KiB", "MiB", "GiB", "TiB"]
    indice = 0
    while bytes_ >= 1024 and indice < len(unidades) - 1:
        bytes_ /= 1024
        indice += 1
    if indice == 0:
        return f"{int(bytes_)} B"
    return f"{bytes_:.1f} {unidades[indice]}".replace(".", ",")


def formatar_duracao(segundos: float) -> str:
    """390 vira '06:30'; 3723 vira '1:02:03'."""
    total = int(round(segundos))
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}:{minutos:02d}:{seg:02d}"
    return f"{minutos:02d}:{seg:02d}"


def linha_progresso(estimativa: Estimativa) -> str:
    """Texto de uma linha: 'feito de total · velocidade · restam tempo'."""
    partes = []
    if estimativa.total is not None:
        partes.append(
            f"{formatar_tamanho(estimativa.feito or 0)} de {formatar_tamanho(estimativa.total)}"
        )
        if estimativa.velocidade is not None:
            partes.append(f"{formatar_tamanho(estimativa.velocidade)}/s")
    else:
        partes.append(f"{estimativa.percentual}%")
    if estimativa.restante is not None:
        partes.append(f"restam {formatar_duracao(estimativa.restante)}")
    elif estimativa.percentual < 100:
        partes.append(tr("calculando a velocidade…"))
    return " · ".join(partes)
