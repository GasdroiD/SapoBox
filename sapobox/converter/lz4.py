"""LZ4 asset-pack conversion using the Linux host packer.

Never mutates the source. The FF-PFSC backend is intentionally separate.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from . import catalog
from .sources import APR_PATHS, DIRECTORY, scan_source
from .workflow import safe_name
from ..i18n import tr  # noqa: E402

PACKED_RUNTIME_SHA256 = "69e6c4d5e4f5fb83c9e01815db5861c4c75734acbf4595cafa50d4c218116d1a"
PROGRESS = re.compile(r"\[progress\]\s*(\d+)\s*/\s*(\d+)")
PHASE = re.compile(r"\[phase\s+[^]]+\]\s*([^:]+):\s*(.*)")
_active_packer = None


def cancel_active():
    """Unblock a worker waiting for packer stderr so it can unmount FUSE."""
    if _active_packer is not None and _active_packer.poll() is None:
        _active_packer.terminate()


def validate_runtime(path):
    if not path:
        raise ValueError(tr("LZ4 requer o libSceAmpr.sprx PackedStdio 0.4.2.1. Selecione-o em APR Emu."))
    file = Path(path).resolve(strict=True)
    if not file.is_file():
        raise ValueError(tr("O runtime APR selecionado não é um arquivo."))
    sha = hashlib.sha256()
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    if sha.hexdigest() != PACKED_RUNTIME_SHA256:
        raise ValueError(tr("LZ4 exige o runtime PackedStdio 0.4.2.1 (SHA-256 diferente). "
                         "Não use o módulo APR padrão: ele não lê packs .pak."))
    return file


def imports_ampr(entry):
    """Inspect only executables/modules, without reading an entire game file."""
    marker = b"libSceAmpr"
    stream = None if entry.reader is not None else open(entry.source, "rb", buffering=0)
    try:
        for logical, physical, size in entry.extents:
            position = physical
            pending = b""
            while size:
                n = min(size, 1024 * 1024)
                if stream is not None:
                    stream.seek(position)
                    block = stream.read(n)
                else:
                    block = entry.reader.read_at(position, n)
                if len(block) != n:
                    raise ValueError(tr('Arquivo truncado: {0}').format(entry.rel))
                if marker in pending + block:
                    return True
                pending = block[-len(marker):]
                position += n
                size -= n
        return False
    finally:
        if stream is not None:
            stream.close()


@contextmanager
def source_tree(path, entries, callback, check):
    """Folder directly, or an ephemeral read-only FUSE mount of the exFAT image."""
    if path.is_dir():
        yield path
        return
    if not shutil.which("fusermount3"):
        raise RuntimeError(tr("Imagens .exfat para LZ4 exigem fuse3/fusermount3 instalado."))
    # fusepy is optional: folder conversion needs neither FUSE nor privileges.
    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="sapobox-mount-") as scratch:
        mount = Path(scratch) / "app0"
        mount.mkdir()
        process = subprocess.Popen([python, "-m", "sapobox.converter.exfat_fuse", str(path), str(mount)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
        mounted = False
        try:
            callback(tr("Montando imagem exFAT em leitura (FUSE)..."))
            for _ in range(100):
                check()
                if os.path.ismount(mount):
                    mounted = True
                    break
                if process.poll() is not None:
                    message = process.stderr.read(2000).decode(errors="replace")
                    raise RuntimeError(tr('Falha ao montar exFAT: {0}').format(message.strip()))
                time.sleep(.1)
            if not mounted:
                raise RuntimeError(tr("A montagem FUSE não ficou pronta em 10 segundos. "
                                   "Confira acesso a /dev/fuse e fusermount3."))
            yield mount
        finally:
            if mounted:
                subprocess.run(["fusermount3", "-u", str(mount)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stderr.close()


def convert(source, destination, runtime, workers=4, level=9, profile=None,
            callback=lambda status: None, progress=lambda done, total, phase: None,
            check=lambda: None):
    if not 1 <= workers <= 16 or not 1 <= level <= 12:
        raise ValueError(tr("LZ4: workers deve estar entre 1 e 16 e level entre 1 e 12."))
    packer = Path(__file__).with_name("ampr_pack_cli")
    if not packer.is_file():
        raise RuntimeError(tr("Packer LZ4 ausente. Execute 'make'."))
    runtime = validate_runtime(runtime)
    callback(tr("Identificando o jogo e arquivos APR..."))
    source, entries, info = scan_source(source, check)
    if info["apr"] is False and not any(
        e.size != DIRECTORY and
        (e.rel.lower() == "eboot.bin" or e.rel.lower().startswith("sce_module/")) and
        imports_ampr(e) for e in entries):
        raise ValueError(tr("Não há evidência de importação libSceAmpr. Use FF-PFSC para este jogo."))
    check()
    title = catalog.lookup(info["code"]) or info["name"]
    name = f"{info['code']} - {safe_name(title)}-lz4"
    dest = Path(destination).resolve(strict=True)
    if not dest.is_dir():
        raise ValueError(tr("O destino precisa ser uma pasta existente."))
    output = dest / name
    if output.exists() or output.is_symlink():
        raise FileExistsError(tr('Saída já existe: {0}').format(output))
    if len(os.fsencode(output)) >= 850:
        raise ValueError(tr("Caminho de saída muito longo para o packer."))
    if source.is_dir() and (dest == source or source in dest.parents):
        raise ValueError(tr("Escolha um destino fora da pasta de origem."))
    if profile:
        profile = Path(profile).resolve(strict=True)
        if not profile.is_file() or profile.suffix.lower() != ".toml":
            raise ValueError(tr("Selecione um perfil TOML existente."))
    callback(tr('{0} — {1}: preparando LZ4').format(info['code'], title))
    # Only the newly-created staging directory is removed on failure.
    staging = Path(tempfile.mkdtemp(prefix=f".{info['code']}-lz4-partial-", dir=dest))
    success = False
    try:
        with source_tree(source, entries, callback, check) as root:
            command = [str(packer), "pack", str(root), str(staging),
                       "--runtime", str(runtime), "--install-runtime-if-missing",
                       "--level", str(level), "--workers", str(workers)]
            if profile:
                command += ["--profile", str(profile)]
            phase = "lz4-scan"
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       bufsize=0)
            global _active_packer
            _active_packer = process
            output_data = bytearray()
            # stderr progress uses carriage returns, so read it in a thread while
            # stdout is bounded and the GUI remains in its own process.
            from threading import Thread
            def drain_stdout():
                for block in iter(lambda: process.stdout.read(4096), b""):
                    output_data.extend(block)
                    if len(output_data) > 1024 * 1024:
                        process.terminate()
                        break
            reader = Thread(target=drain_stdout, daemon=True)
            reader.start()
            diagnostic = bytearray()
            buf = bytearray()
            try:
                while True:
                    check()
                    chunk = os.read(process.stderr.fileno(), 4096)
                    if not chunk:
                        break
                    diagnostic.extend(chunk)
                    if len(diagnostic) > 8192:
                        del diagnostic[:-8192]
                    buf.extend(chunk)
                    parts = re.split(b"[\r\n]", buf)
                    buf = bytearray(parts.pop())
                    for raw in parts:
                        text = raw.decode(errors="replace")
                        match = PHASE.search(text)
                        if match:
                            phase = match.group(1)
                            callback(match.group(2))
                        match = PROGRESS.search(text)
                        if match:
                            progress(int(match.group(1)), int(match.group(2)), phase)
                reader.join(timeout=5)
                code = process.wait(timeout=10)
            finally:
                _active_packer = None
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                process.stderr.close()
            if code:
                raise RuntimeError(diagnostic.decode(errors="replace")[-1200:].strip() or tr("Packer LZ4 falhou"))
            stats = json.loads(output_data)
            if stats.get("packed", 0) == 0:
                raise ValueError(tr("Nenhum arquivo foi empacotado. Ajuste o perfil TOML ou escolha FF-PFSC."))
            if not stats.get("runtime"):
                raise RuntimeError(tr("O runtime PackedStdio não foi instalado; saída não pode ser publicada."))
        check()
        staging.rename(output)
        success = True
        callback(tr("LZ4 concluído"))
        return output, stats
    finally:
        if not success:
            shutil.rmtree(staging)
