"""Single-job orchestration. Originals are never modified."""
import os
from pathlib import Path
import re
import tempfile

from . import catalog, engine
from .sources import APR_PATHS, DIRECTORY, Entry, file_entry, scan_source, validate_entries
from ..i18n import tr  # noqa: E402


def safe_name(name):
    name = re.sub(r"[\\/\x00-\x1f:*?\"<>|]+", " ", name).strip(" .")
    text = re.sub(r"\s+", " ", name)
    while len(text.encode("utf-8")) > 180:
        text = text[:-1]
    return text.strip(" .") or "Jogo"


def prepare(entries, apr_module, scratch):
    """Stage only the optional module and small index, never a whole game."""
    result = [e for e in entries if e.rel.lower() != "ampr_emu.index"
              and e.rel.lower() not in APR_PATHS]
    apr = any(e.rel.lower() in APR_PATHS for e in entries)
    if not apr:
        return list(entries), False
    if not apr_module:
        raise ValueError(tr("Jogo APR detectado. Selecione um libSceAmpr.sprx compatível."))
    module = Path(apr_module).resolve(strict=True)
    if not module.is_file() or not module.stat().st_size:
        raise ValueError(tr("Módulo APR selecionado está vazio ou não é arquivo."))
    if any(e.source == str(module) for e in entries):
        raise ValueError(tr("Selecione o módulo APR fora da pasta de origem."))
    for e in entries:
        if e.rel.lower() in APR_PATHS:
            if not e.rel.lower().endswith(".sprx"):
                raise ValueError(tr("Módulo APR .prx detectado; selecione uma versão .prx compatível manualmente."))
            result.append(file_entry(module, e.rel))
    validate_entries(result)
    index = scratch / "ampr_emu.index"
    engine.make_index(result, index)
    result.append(file_entry(index, "ampr_emu.index"))
    return result, True


def output_name(code, name):
    return f"{code} - {safe_name(name)}.ffpfsc"


def run(source, destination, apr_module=None, workers=4, level=1,
        callback=lambda status: None, check=lambda: None):
    if Path(source).suffix.lower() == ".ffpfsc":
        raise ValueError(tr("Um arquivo .ffpfsc já é FF-PFSC. Escolha o formato LZ4 para convertê-lo."))
    callback(tr("Lendo arquivos e identificando jogo..."))
    root, entries, info = scan_source(source, check)
    check()
    callback(tr("Consultando catálogo..."))
    title = catalog.lookup(info["code"]) or info["name"]
    name = output_name(info["code"], title)
    if not Path(destination).is_dir():
        raise ValueError(tr("Destino precisa ser uma pasta existente."))
    dest = Path(destination).resolve(strict=True)
    output = dest / name
    sidecar = Path(str(output) + ".vhash")
    temp_output = dest / f".{output.name}.gc-compress.tmp"
    temp_sidecar = Path(str(temp_output) + ".vhash")
    if any(p.exists() or p.is_symlink() for p in (output, sidecar, temp_output, temp_sidecar)):
        raise FileExistsError(tr('Saída já existe: {0}').format(output))
    if len(os.fsencode(output)) >= 900:
        raise ValueError(tr("Caminho de saída muito longo."))
    if root.is_dir() and (dest == root or root in dest.parents):
        raise ValueError(tr("Escolha um destino fora da pasta de origem."))
    if root.is_file() and output == root:
        raise ValueError(tr("Origem e destino coincidem."))
    callback(f"{info['code']} — {title}" + (" (APR)" if info["apr"] else ""))
    with tempfile.TemporaryDirectory(prefix="sapobox-") as temporary:
        scratch = Path(temporary)
        entries, apr = prepare(entries, apr_module, scratch)
        validate_entries(entries)
        check()
        callback(tr("Comprimindo para .ffpfsc..."))
        engine.convert(entries, info["code"], root, output, workers, level)
    callback(tr("Concluído"))
    return output, apr


def shadowmount_hints(output, code):
    """Text to merge into /data/shadowmount/config.ini on the console if needed."""
    return f"image_sector={output.name}:65536\nimage_ro={code}.exfat\n"
