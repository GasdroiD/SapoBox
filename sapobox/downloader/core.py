"""Núcleo do Downloader, sem dependência de GTK.

Cada download roda em uma thread própria. Pausar encerra a conexão e mantém o
arquivo parcial (``<nome>.part``); retomar continua do ponto em que parou, usando
o cabeçalho HTTP ``Range``. Se o servidor ignorar ``Range``, o arquivo é baixado
de novo desde o início.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from ..i18n import tr  # noqa: E402

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Downloader/1.0"
CHUNK_SIZE = 64 * 1024
TIMEOUT = 30  # segundos sem dados até considerar a conexão travada
MAX_CONCURRENT_LIMIT = 10
MAX_CONNECTIONS = 8  # partes simultâneas por download
MIN_SEGMENTED_SIZE = 8 * 1024 * 1024  # abaixo disso não compensa dividir

QUEUED = "queued"
DOWNLOADING = "downloading"
PAUSED = "paused"
COMPLETED = "completed"
ERROR = "error"

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
SETTINGS_FILE = _CONFIG_HOME / "downloader" / "settings.json"
DOWNLOADS_FILE = _DATA_HOME / "downloader" / "downloads.json"

_PERSISTED_FIELDS = (
    "id", "url", "dest_dir", "filename", "status",
    "downloaded", "total", "error", "added_at",
)
_CONTENT_DISPOSITION_EXT = re.compile(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", re.IGNORECASE)
_CONTENT_DISPOSITION = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)
_CONTENT_RANGE_TOTAL = re.compile(r"/(\d+)\s*$")


# --------------------------------------------------------------------------- #
# Configurações
# --------------------------------------------------------------------------- #

@dataclass
class Settings:
    download_dir: str = str(Path.home() / "Downloads")
    max_concurrent: int = 3
    connections: int = 4  # partes simultâneas de cada download, quando o servidor permite


def clamp_concurrency(value: int) -> int:
    return max(1, min(MAX_CONCURRENT_LIMIT, int(value)))


def clamp_connections(value: int) -> int:
    return max(1, min(MAX_CONNECTIONS, int(value)))


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    settings = Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(data, dict):
        return settings
    if isinstance(data.get("download_dir"), str):
        settings.download_dir = data["download_dir"]
    if isinstance(data.get("max_concurrent"), int):
        settings.max_concurrent = clamp_concurrency(data["max_concurrent"])
    if isinstance(data.get("connections"), int):
        settings.connections = clamp_connections(data["connections"])
    return settings


def save_settings(settings: Settings, path: Path = SETTINGS_FILE) -> None:
    _write_json(path, asdict(settings))


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #

@dataclass
class Download:
    url: str
    dest_dir: str
    filename: str | None = None
    status: str = QUEUED
    downloaded: int = 0
    total: int | None = None
    error: str | None = None
    added_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    speed: float = 0.0  # bytes/s; preenchido por DownloadManager.snapshot()

    @property
    def is_active(self) -> bool:
        return self.status in (QUEUED, DOWNLOADING)

    @property
    def final_path(self) -> Path | None:
        return Path(self.dest_dir, self.filename) if self.filename else None

    @property
    def part_path(self) -> Path | None:
        return Path(self.dest_dir, self.filename + ".part") if self.filename else None

    @property
    def seg_path(self) -> Path | None:
        """Estado das partes de um download dividido (some quando ele termina)."""
        return Path(self.dest_dir, self.filename + ".part.seg") if self.filename else None


@dataclass
class AddResult:
    added: int = 0
    duplicates: int = 0
    invalid: int = 0


class TransferIncomplete(Exception):
    """A conexão terminou antes do tamanho anunciado pelo servidor."""


class _Stopped(Exception):
    """Sinal interno: o usuário pausou ou removeu o download."""


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

def is_valid_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def guess_name(url: str) -> str:
    return urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name)


def sanitize_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
    return name[:200]


def suggest_filename(headers, url: str) -> str:
    disposition = headers.get("Content-Disposition") or ""
    name = None
    if match := _CONTENT_DISPOSITION_EXT.search(disposition):
        name = urllib.parse.unquote(match.group(1).strip())
    elif match := _CONTENT_DISPOSITION.search(disposition):
        name = match.group(1).strip()
    return sanitize_filename(name or guess_name(url)) or "download"


def describe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return tr('o servidor respondeu HTTP {0} {1}').format(exc.code, exc.reason)
    if isinstance(exc, urllib.error.URLError):
        return tr('falha de conexão ({0})').format(exc.reason)
    if isinstance(exc, TransferIncomplete):
        return tr("a conexão caiu antes do fim do arquivo")
    if isinstance(exc, http.client.IncompleteRead):
        return tr("a conexão caiu antes do fim do arquivo")
    if isinstance(exc, TimeoutError):
        return tr("a conexão ficou sem resposta (tempo esgotado)")
    if isinstance(exc, ConnectionError):
        return tr("a conexão foi encerrada pelo servidor")
    if isinstance(exc, OSError):
        return tr('erro ao gravar o arquivo ({0})').format(exc.strerror or exc)
    return str(exc) or exc.__class__.__name__


def _int_header(headers, name: str) -> int | None:
    try:
        return int(headers.get(name))
    except (TypeError, ValueError):
        return None


def _total_from_range(headers, offset: int) -> int | None:
    match = _CONTENT_RANGE_TOTAL.search(headers.get("Content-Range") or "")
    if match:
        return int(match.group(1))
    length = _int_header(headers, "Content-Length")
    return offset + length if length is not None else None


def _load_segments(download: Download):
    """Lê o estado das partes: (total, [[início, fim, feito], ...]) ou None."""
    path = download.seg_path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        total, segments = int(data["total"]), [[int(a), int(b), int(c)] for a, b, c in data["segments"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not segments or any(not (0 <= c <= b - a + 1) for a, b, c in segments):
        return None
    return total, segments


def _partial_size(download: Download) -> int:
    part = download.part_path
    if download.seg_path and (loaded := _load_segments(download)):
        return sum(done for _a, _b, done in loaded[1])
    try:
        return part.stat().st_size if part else 0
    except OSError:
        return 0


def _write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        print(tr('Não foi possível salvar {0}: {1}').format(path, exc), file=sys.stderr)


def _to_record(download: Download) -> dict:
    return {key: getattr(download, key) for key in _PERSISTED_FIELDS}


# --------------------------------------------------------------------------- #
# Transferência (uma thread por download)
# --------------------------------------------------------------------------- #

class DownloadWorker(threading.Thread):
    def __init__(self, manager: DownloadManager, download: Download):
        super().__init__(name=f"download-{download.id[:8]}", daemon=True)
        self.manager = manager
        self.download = download
        self._stop_requested = threading.Event()

    def request_stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        outcome, error = COMPLETED, None
        try:
            self._transfer()
        except _Stopped:
            outcome = PAUSED
        except Exception as exc:  # rede ou disco: vira erro do item, não derruba o app
            outcome, error = ERROR, describe_error(exc)
        self.manager._worker_finished(self, outcome, error)

    def _check_stop(self) -> None:
        if self._stop_requested.is_set():
            raise _Stopped

    def _request(self, offset: int):
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(self.download.url, headers=headers)
        try:
            return urllib.request.urlopen(request, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:  # intervalo fora do arquivo: recomeça
                return None
            raise

    def _try_segmented(self) -> bool:
        """Testa se o servidor aceita Range e o arquivo é grande; se sim, baixa em partes."""
        d = self.download
        request = urllib.request.Request(d.url, headers={
            "User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as probe:
                if probe.status != 206:
                    return False
                total = _total_from_range(probe.headers, 0) if _CONTENT_RANGE_TOTAL.search(
                    probe.headers.get("Content-Range") or "") else None
                name = suggest_filename(probe.headers, d.url)
        except (urllib.error.URLError, OSError, ValueError):
            return False  # sem teste confiável: usa o caminho de uma conexão só
        if not total or total < MIN_SEGMENTED_SIZE:
            return False
        if d.filename is None:
            self.manager._claim_filename(d, name)
        count = min(self.manager.settings.connections, max(1, total // (2 * 1024 * 1024)))
        size = -(-total // count)
        segments = [[i * size, min(total, (i + 1) * size) - 1, 0] for i in range(count)]
        self._transfer_segmented(total, segments, fresh=True)
        return True

    def _save_segments(self, total: int, segments: list) -> None:
        _write_json(self.download.seg_path, {"total": total, "segments": segments})

    def _transfer_segmented(self, total: int, segments: list, fresh: bool = False) -> None:
        d = self.download
        lock = threading.Lock()
        failure: list[Exception] = []
        halt = threading.Event()
        fd = os.open(d.part_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if fresh or os.fstat(fd).st_size != total:
                os.ftruncate(fd, total)
            d.total = total
            d.downloaded = sum(c for _a, _b, c in segments)
            self._save_segments(total, segments)

            def fetch(seg):
                try:
                    start, end, _ = seg
                    while seg[2] < end - start + 1 and not halt.is_set():
                        pos = start + seg[2]
                        request = urllib.request.Request(d.url, headers={
                            "User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                            "Range": f"bytes={pos}-{end}"})
                        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                            if response.status != 206:
                                raise TransferIncomplete()
                            while not halt.is_set():
                                room = end - start + 1 - seg[2]
                                if room <= 0:
                                    break
                                chunk = response.read(min(CHUNK_SIZE, room))
                                if not chunk:
                                    raise TransferIncomplete()
                                os.pwrite(fd, chunk, start + seg[2])
                                with lock:
                                    seg[2] += len(chunk)
                                    d.downloaded += len(chunk)
                except Exception as exc:
                    failure.append(exc)
                    halt.set()

            threads = [threading.Thread(target=fetch, args=(seg,), daemon=True) for seg in segments]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                if self._stop_requested.is_set():
                    halt.set()
                time.sleep(0.5)
                with lock:
                    self._save_segments(total, [list(s) for s in segments])
            with lock:
                self._save_segments(total, [list(s) for s in segments])
        finally:
            os.close(fd)
        if self._stop_requested.is_set():
            raise _Stopped
        if failure:
            raise failure[0]
        if any(c != b - a + 1 for a, b, c in segments):
            raise TransferIncomplete()
        d.seg_path.unlink(missing_ok=True)
        os.replace(d.part_path, d.final_path)

    def _transfer(self) -> None:
        d = self.download
        self._check_stop()
        Path(d.dest_dir).mkdir(parents=True, exist_ok=True)

        saved = _load_segments(d) if d.filename else None
        if saved:
            self._transfer_segmented(*saved)
            return
        offset = _partial_size(d)
        if offset == 0 and self.manager.settings.connections > 1 and self._try_segmented():
            return
        response = self._request(offset) if offset else None
        if response is None:
            offset = 0
            response = self._request(0)

        with response:
            self._check_stop()
            if d.filename is None:
                self.manager._claim_filename(d, suggest_filename(response.headers, d.url))

            if offset and response.status == 206:
                mode, total = "ab", _total_from_range(response.headers, offset)
            else:  # servidor ignorou Range: começa do zero
                offset, mode = 0, "wb"
                total = _int_header(response.headers, "Content-Length")

            d.total = total
            d.downloaded = offset
            with open(d.part_path, mode) as fh:
                while True:
                    self._check_stop()
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    fh.write(chunk)
                    d.downloaded += len(chunk)

        if d.total is not None and d.downloaded != d.total:
            raise TransferIncomplete()
        os.replace(d.part_path, d.final_path)


# --------------------------------------------------------------------------- #
# Gerenciador (fila, limite de simultâneos, persistência)
# --------------------------------------------------------------------------- #

class DownloadManager:
    def __init__(self, settings: Settings, data_file: Path = DOWNLOADS_FILE, on_completed=None):
        self.settings = settings
        self._data_file = data_file
        self._on_completed = on_completed  # chamado a partir da thread do worker
        self._lock = threading.RLock()
        self._items: dict[str, Download] = {}
        self._workers: dict[str, DownloadWorker] = {}
        self._samples: dict[str, tuple[float, int]] = {}
        self._load()
        with self._lock:
            self._schedule_locked()

    # ----- consultas -------------------------------------------------------

    def snapshot(self) -> list[Download]:
        """Cópias dos downloads, na ordem em que foram adicionados, com velocidade."""
        now = time.monotonic()
        with self._lock:
            result = []
            for d in self._items.values():
                copy = replace(d)
                previous = self._samples.get(d.id)
                if d.status == DOWNLOADING and previous and now > previous[0]:
                    copy.speed = (d.downloaded - previous[1]) / (now - previous[0])
                self._samples[d.id] = (now, d.downloaded)
                result.append(copy)
            return result

    # ----- ações do usuário ------------------------------------------------

    def add_urls(self, lines: list[str], dest_dir: str) -> AddResult:
        result = AddResult()
        with self._lock:
            for line in lines:
                url = line.strip()
                if not url:
                    continue
                if not is_valid_url(url):
                    result.invalid += 1
                    continue
                if any(o.url == url and o.status != COMPLETED for o in self._items.values()):
                    result.duplicates += 1
                    continue
                download = Download(url=url, dest_dir=dest_dir)
                self._items[download.id] = download
                result.added += 1
            if result.added:
                self._save_locked()
                self._schedule_locked()
        return result

    def pause(self, download_id: str) -> None:
        with self._lock:
            d = self._items.get(download_id)
            if d is None or not d.is_active:
                return
            d.status = PAUSED
            worker = self._workers.get(download_id)
            if worker:
                worker.request_stop()  # o slot só libera quando a thread termina
            self._save_locked()

    def resume(self, download_id: str) -> None:
        with self._lock:
            d = self._items.get(download_id)
            if d is None or d.status not in (PAUSED, ERROR):
                return
            d.status, d.error = QUEUED, None
            self._save_locked()
            self._schedule_locked()

    def toggle(self, download_id: str) -> None:
        with self._lock:
            d = self._items.get(download_id)
            if d is None:
                return
        if d.is_active:
            self.pause(download_id)
        else:
            self.resume(download_id)

    def pause_all(self) -> None:
        for d in self.snapshot():
            self.pause(d.id)

    def resume_all(self) -> None:
        for d in self.snapshot():
            self.resume(d.id)

    def remove(self, download_id: str) -> None:
        with self._lock:
            d = self._items.pop(download_id, None)
            if d is None:
                return
            self._samples.pop(download_id, None)
            worker = self._workers.get(download_id)
            if worker:
                worker.request_stop()  # o próprio worker apaga o parcial ao terminar
            elif d.part_path and d.status != COMPLETED:
                d.part_path.unlink(missing_ok=True)
                d.seg_path.unlink(missing_ok=True)
            self._save_locked()
            self._schedule_locked()

    def clear_completed(self) -> None:
        with self._lock:
            for d in [d for d in self._items.values() if d.status == COMPLETED]:
                del self._items[d.id]
            self._save_locked()

    def set_connections(self, value: int) -> None:
        with self._lock:
            self.settings.connections = clamp_connections(value)

    def set_max_concurrent(self, value: int) -> None:
        with self._lock:
            self.settings.max_concurrent = clamp_concurrency(value)
            self._schedule_locked()

    def shutdown(self) -> None:
        """Pausa o que está baixando para que a próxima abertura retome do parcial."""
        with self._lock:
            for d in self._items.values():
                if d.status == DOWNLOADING:
                    d.status = PAUSED
                worker = self._workers.get(d.id)
                if worker:
                    worker.request_stop()
            self._save_locked()

    # ----- uso interno (threads de trabalho também chamam) -----------------

    def _schedule_locked(self) -> None:
        for d in self._items.values():
            if len(self._workers) >= self.settings.max_concurrent:
                break
            if d.status == QUEUED and d.id not in self._workers:
                worker = DownloadWorker(self, d)
                d.status, d.error = DOWNLOADING, None
                self._workers[d.id] = worker
                worker.start()

    def _worker_finished(self, worker: DownloadWorker, outcome: str, error: str | None) -> None:
        download = worker.download
        completed = False
        with self._lock:
            if self._workers.get(download.id) is worker:
                del self._workers[download.id]

            if self._items.get(download.id) is download:
                if outcome == COMPLETED:
                    download.status, download.error = COMPLETED, None
                    completed = True
                elif outcome == ERROR:
                    download.status, download.error = ERROR, error
                elif download.status == DOWNLOADING:
                    download.status = PAUSED
                self._save_locked()
            elif outcome != COMPLETED and download.part_path:
                download.part_path.unlink(missing_ok=True)  # foi removido durante a transferência
                download.seg_path.unlink(missing_ok=True)

            self._schedule_locked()

        if completed and self._on_completed:
            self._on_completed(download.filename)

    def _claim_filename(self, download: Download, suggested: str) -> None:
        with self._lock:
            stem, ext = os.path.splitext(suggested)
            name, n = suggested, 1
            while self._name_in_use(download, name):
                name = f"{stem} ({n}){ext}"
                n += 1
            download.filename = name
            self._save_locked()

    def _name_in_use(self, download: Download, name: str) -> bool:
        base = Path(download.dest_dir)
        if (base / name).exists() or (base / (name + ".part")).exists():
            return True
        return any(
            o.filename == name and o.dest_dir == download.dest_dir
            for o in self._items.values() if o is not download
        )

    def _save_locked(self) -> None:
        _write_json(self._data_file, [_to_record(d) for d in self._items.values()])

    def _load(self) -> None:
        try:
            records = json.loads(self._data_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(records, list):
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            known = {k: v for k, v in record.items() if k in _PERSISTED_FIELDS}
            try:
                d = Download(**known)
            except TypeError:
                continue
            if d.status == DOWNLOADING:  # o app fechou no meio: a fila retoma sozinha
                d.status = QUEUED
            if d.status != COMPLETED:
                d.downloaded = _partial_size(d)
            self._items[d.id] = d
