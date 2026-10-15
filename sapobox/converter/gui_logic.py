"""Regras da interface que não dependem de GTK: validação, textos e cálculo de progresso.

Tudo aqui roda sem display, então é testado diretamente com unittest.
"""
from dataclasses import dataclass
import functools
import hashlib
import os
from pathlib import Path

from .exfat_fuse import fuse_available
from .lz4 import PACKED_RUNTIME_SHA256
from ..i18n import tr  # noqa: E402

FFPFSC = "ffpfsc"
LZ4 = "lz4"
FORMATS = (FFPFSC, LZ4)
APP_NAME = "SapoBox Conversor"

OUTPUT_TITLES = {
    FFPFSC: "FF-PFSC",
    LZ4: "LZ4",
}
OUTPUT_HELP = {
    FFPFSC: tr("Um arquivo .ffpfsc com .vhash ao lado. No console, o ShadowMountPlus precisa de configuração."),
    LZ4: tr("Uma pasta de pacotes .pak e índices. Exige o runtime PackedStdio 0.4.2.1."),
}
# (rótulo, nível enviado ao worker, explicação curta)
COMPRESSION = {
    FFPFSC: [
        (tr("Rápida"), 1, tr("Conversão mais rápida, arquivo maior.")),
        (tr("Equilibrada"), 5, tr("Meio-termo entre tempo e tamanho.")),
        (tr("Arquivo menor"), 9, tr("Arquivo menor, conversão mais demorada.")),
    ],
    LZ4: [
        (tr("Rápida"), 1, tr("Compressão LZ4 simples, arquivos maiores.")),
        (tr("Equilibrada"), 9, tr("LZ4 HC 9, recomendado.")),
        (tr("Arquivo menor"), 12, tr("LZ4 HC 12, o mais lento e o menor.")),
    ],
}
DEFAULT_LEVEL = {FFPFSC: 1, LZ4: 9}
THREADS_HELP = (tr("Partes do jogo comprimidas ao mesmo tempo. Mais threads = mais rápido, "
                "porém mais CPU e memória."))

PHASE_TITLES = {
    "lz4-scan": tr("Lendo o jogo"),
    "lz4-index": tr("Criando o índice de arquivos"),
    "lz4-plan": tr("Escolhendo o que vai para os pacotes"),
    "lz4-pack": tr("Comprimindo os pacotes"),
    "lz4-finalize": tr("Gravando os pacotes"),
    "lz4-verify": tr("Conferindo os pacotes com o original"),
    "lz4-copy": tr("Copiando os arquivos que ficam soltos"),
}

# Texto e tom de cada tipo de detecção de APR Emu, em linguagem comum.
PROBE_TONE = {"emulator": "warning", "module": "warning", "import": "warning", "unknown": "success"}


def is_ffpfsc(path):
    return bool(path) and str(path).lower().endswith(".ffpfsc")


def default_workers(cpus=None):
    return min(8, max(2, cpus or os.cpu_count() or 4))


def default_level(output_format):
    return DEFAULT_LEVEL[output_format]


def level_index(output_format, level):
    for index, (_label, value, _help) in enumerate(COMPRESSION[output_format]):
        if value == level:
            return index
    return next(i for i, (_l, v, _h) in enumerate(COMPRESSION[output_format])
                if v == DEFAULT_LEVEL[output_format])


def local_runtime():
    """O runtime PackedStdio do projeto, somente se o SHA-256 bater com o exigido."""
    candidate = Path(__file__).resolve().parent.parent.parent / "runtime" / "libSceAmpr.sprx"
    return str(candidate) if runtime_matches(candidate) else None


def sha256_of(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@functools.lru_cache(maxsize=8)
def _cached_hash(path, mtime_ns, size):
    return sha256_of(path)


def runtime_matches(path):
    """Confere o SHA-256 do runtime. O resultado fica em cache enquanto o arquivo não muda."""
    try:
        st = Path(path).stat()
        return _cached_hash(str(path), st.st_mtime_ns, st.st_size) == PACKED_RUNTIME_SHA256
    except OSError:
        return False


def short_path(path, limit=44):
    """Caminho curto para linhas de interface: ~ no lugar da pasta pessoal e só as duas últimas partes."""
    if not path:
        return ""
    home = str(Path.home())
    text = "~" + path[len(home):] if path.startswith(home + os.sep) else path
    if len(text) <= limit:
        return text
    parts = Path(text).parts
    return "…/" + "/".join(parts[-2:])


def inside(path, root):
    """True quando path é root ou está abaixo dele. Só pastas podem conter a saída."""
    if not root.is_dir():
        return False
    return path == root or root in path.parents


def module_needed(detected, output_format):
    """Mesma regra do backend: LZ4 sempre precisa do runtime; FF-PFSC só com APR Emu."""
    return output_format == LZ4 or detected in ("emulator", "module")


@dataclass
class Form:
    """Estado dos campos da tela de configuração."""
    source: str = ""
    dest: str = ""
    output_format: str = FFPFSC
    module: str = ""
    profile: str = ""
    detected: str | None = None
    probing: bool = False
    probe_error: str = ""


@dataclass(frozen=True)
class Problem:
    field: str   # "source", "dest", "module" ou "profile"
    short: str   # frase curta para a barra inferior
    detail: str  # explicação completa para a linha do campo


def problems(form):
    """Motivos que impedem a conversão agora. Lista vazia significa pronto."""
    found = []
    source_ok = bool(form.source) and Path(form.source).exists()
    if not source_ok:
        found.append(Problem("source", tr("Escolha o jogo para começar"),
                             tr("Escolha a pasta do jogo ou uma imagem .exfat.")))
    elif form.probing:
        found.append(Problem("source", tr("Verificando o jogo…"),
                             tr("Aguarde a verificação do jogo terminar.")))
    elif form.probe_error:
        found.append(Problem("source", tr("Não foi possível ler este jogo"), form.probe_error))
    elif is_ffpfsc(form.source) and form.output_format == FFPFSC:
        found.append(Problem("source", tr("Arquivo .ffpfsc só vira LZ4"),
                             tr("Um .ffpfsc já é FF-PFSC. Escolha o formato LZ4 para convertê-lo.")))
    elif form.output_format == LZ4 and Path(form.source).is_file() and not fuse_available():
        found.append(Problem("source", tr("Falta o FUSE para ler esta imagem"),
                             tr("Imagens .exfat e .ffpfsc em LZ4 precisam de python3-fusepy e fuse3. "
                             "Rode: sh instalar_dependencias.sh")))
    dest_ok = bool(form.dest) and Path(form.dest).is_dir()
    if not dest_ok:
        found.append(Problem("dest", tr("Escolha a pasta de saída"),
                             tr("Escolha uma pasta que já exista para receber a conversão.")))
    elif source_ok and inside(Path(form.dest).resolve(), Path(form.source).resolve()):
        found.append(Problem("dest", tr("A saída não pode ficar dentro do jogo"),
                             tr("Escolha uma pasta fora da pasta do jogo: o original nunca é alterado.")))
    lz4 = form.output_format == LZ4
    if lz4 and form.detected == "unknown":
        # Mesma regra do backend: LZ4 só para jogos que importam libSceAmpr.
        found.append(Problem("source", tr("Este jogo não usa libSceAmpr"),
                             tr("O formato LZ4 é para jogos que importam libSceAmpr. Use FF-PFSC.")))
    if module_needed(form.detected, form.output_format) and not form.module:
        if lz4:
            found.append(Problem("module", tr("Falta o runtime PackedStdio"),
                                 tr("O formato LZ4 exige o runtime PackedStdio 0.4.2.1.")))
        else:
            found.append(Problem("module", tr("Este jogo precisa de um módulo APR"),
                                 tr("O jogo usa APR Emu. Escolha um libSceAmpr.sprx compatível.")))
    elif form.module and not Path(form.module).is_file():
        found.append(Problem("module", tr("Módulo não encontrado"),
                             tr("O arquivo do módulo escolhido não existe mais.")))
    elif form.module and lz4 and not runtime_matches(form.module):
        found.append(Problem("module", tr("Runtime incompatível"),
                             tr("O LZ4 exige exatamente o PackedStdio 0.4.2.1. O arquivo escolhido é outro.")))
    if lz4 and form.profile and not Path(form.profile).is_file():
        found.append(Problem("profile", tr("Perfil não encontrado"),
                             tr("O arquivo de perfil TOML escolhido não existe mais.")))
    return found


def show_module_section(form):
    """Mostra a seção do módulo APR quando ela é relevante para este jogo."""
    return (form.output_format == LZ4 or form.detected in ("emulator", "module", "import")
            or bool(form.module))


def module_explanation(output_format):
    if output_format == LZ4:
        return (tr("O LZ4 precisa do runtime PackedStdio 0.4.2.1, verificado pelo SHA-256. "
            "Use o do projeto ou um arquivo com o mesmo hash."))
    return (tr("APR Emu é um módulo de compatibilidade (libSceAmpr). Se o jogo usar, o conversor "
            "embute o módulo escolhido na saída."))


class Progress:
    """Converte eventos de progresso do worker em fração da barra e um texto legível."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.phase = None
        self.last_done = 0
        self.last_time = None
        self.speed = 0.0

    def update(self, data):
        """Devolve (fração ou None quando é desconhecida, texto)."""
        done, total, now = data["done"], data["total"], data["timestamp"]
        phase = data.get("phase")
        if phase != self.phase:
            self.phase = phase
            self.last_time, self.last_done, self.speed = None, 0, 0.0
        if phase == "lz4-plan":
            if not total:
                return None, tr('{0} arquivos analisados').format(done)
            return min(1.0, done / total), tr('{0} de {1} arquivos analisados').format(done, total)
        if self.last_time is not None and done > self.last_done and now > self.last_time:
            instant = (done - self.last_done) / (now - self.last_time)
            self.speed = instant if not self.speed else self.speed * .7 + instant * .3
        self.last_time, self.last_done = now, done
        if not total:
            return None, f"{human(done)} processados"
        fraction = min(1.0, done / total)
        text = f"{human(done)} de {human(total)}"
        if self.speed:
            text += tr(' · {0}/s · restam {1}').format(human(self.speed), duration((total - done) / self.speed))
        return fraction, text


def human(n):
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024


def saving_text(source_bytes, output_bytes):
    """Linha da tela de conclusão: quanto o jogo diminuiu (ou aumentou) em relação à origem."""
    before, after = human(source_bytes), human(output_bytes)
    difference = source_bytes - output_bytes
    if difference > 0:
        return tr('Diminuiu {0} (de {1} para {2}).').format(human(difference), before, after)
    if difference < 0:
        return tr('Aumentou {0} (de {1} para {2}).').format(human(-difference), before, after)
    return tr('O tamanho não mudou ({0}).').format(after)


def duration(sec):
    if sec < 0 or sec > 24 * 3600:
        return "calculando…"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
