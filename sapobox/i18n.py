"""Idioma da interface: português (padrão do código-fonte) e inglês.

`tr("texto em português")` devolve o texto no idioma ativo. O idioma é escolhido uma vez por
processo, nesta ordem: variável SAPOBOX_LANG, preferência salva (menu do SapoBox) e idioma do
sistema. Os processos filhos (por exemplo o conversor) herdam a escolha pelo mesmo caminho.
"""
import os
import re
from pathlib import Path

IDIOMAS = {"pt": "Português", "en": "English"}
PADRAO = "pt"


def _arquivo_config() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "sapobox" / "language"


def normalizar(codigo):
    codigo = re.split(r"[_.@-]", (codigo or "").strip().lower())[0]
    return codigo if codigo in IDIOMAS else None


def salvo():
    try:
        return normalizar(_arquivo_config().read_text())
    except OSError:
        return None


def salvar(codigo):
    codigo = normalizar(codigo)
    if codigo is None:
        raise ValueError(codigo)
    arquivo = _arquivo_config()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(codigo + "\n")


def _do_sistema():
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        valor = os.environ.get(var)
        if valor and valor not in ("C", "POSIX"):
            return "pt" if normalizar(valor) == "pt" else "en"
    return PADRAO


def detectar():
    return normalizar(os.environ.get("SAPOBOX_LANG")) or salvo() or _do_sistema()


IDIOMA = detectar()


def tr(texto):
    if IDIOMA == "pt":
        return texto
    from .i18n_en import EN
    return EN.get(texto, texto)
