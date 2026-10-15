"""Leitura somente leitura para descobrir se o jogo usa APR Emu (libSceAmpr).

Procura o nome libSceAmpr em pastas de módulo e dentro dos executáveis do jogo.
Os executáveis são lidos em blocos, então não há limite de tamanho: a busca é completa.
"""
import json
import sys

from .sources import DIRECTORY, scan_source
from .lz4 import imports_ampr
from ..i18n import tr  # noqa: E402

MESSAGES = {
    "emulator": (tr("APR Emu encontrado na pasta fakelib. Para converter, escolha um módulo "
                 "libSceAmpr compatível: ele será incluído na saída.")),
    "module": (tr("Há um libSceAmpr na pasta sce_module. Pelo nome não dá para confirmar que é o "
               "APR Emu; confira o módulo escolhido antes de converter.")),
    "import": (tr("O executável do jogo usa libSceAmpr, mas o módulo não está na pasta do jogo. "
               "Escolha um módulo compatível para incluir na saída.")),
    "unknown": (tr("Nenhum sinal de APR Emu em eboot.bin nem em sce_module. "
                "Nenhum módulo será incluído na conversão.")),
}


def inspect(path):
    root, entries, info = scan_source(path)
    names = {e.rel.lower() for e in entries}
    in_fakelib = "fakelib/libsceampr.sprx" in names or "fakelib/libsceampr.prx" in names
    in_sce_module = "sce_module/libsceampr.sprx" in names or "sce_module/libsceampr.prx" in names
    candidates = [e for e in entries if e.size != DIRECTORY and
                  (e.rel.lower() == "eboot.bin" or
                   (e.rel.lower().startswith("sce_module/") and
                    e.rel.lower().endswith((".sprx", ".prx"))))]
    found_import = None  # only read executables when the folders do not already decide
    if in_fakelib:
        kind = "emulator"
    elif in_sce_module:
        kind = "module"
    else:
        found_import = any(imports_ampr(e) for e in candidates)
        kind = "import" if found_import else "unknown"
    return {"code": info["code"], "name": info["name"], "kind": kind,
            "apr": info["apr"], "imports": found_import, "text": MESSAGES[kind]}


def main():
    try:
        print(json.dumps(inspect(sys.argv[1]), ensure_ascii=False))
    except OSError as exc:
        print(json.dumps({"error": exc.strerror or str(exc)}, ensure_ascii=False))
        return 1
    except (ValueError, IndexError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
