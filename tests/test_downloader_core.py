"""Testes do núcleo (sem GTK), usando um servidor HTTP local com suporte a Range."""

import hashlib
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sapobox.downloader.core import (  # noqa: E402
    COMPLETED,
    DOWNLOADING,
    ERROR,
    PAUSED,
    QUEUED,
    DownloadManager,
    Settings,
    load_settings,
    save_settings,
)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, _Handler)
        self.payload = b""
        self.delay = 0.0          # pausa entre blocos, para simular rede lenta
        self.support_range = True
        self.disposition = None
        self.requests = []        # cabeçalhos Range recebidos


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        range_header = self.headers.get("Range")
        server.requests.append(range_header)

        if self.path.startswith("/missing"):
            self.send_error(404, "Not Found")
            return

        data, start, status = server.payload, 0, 200
        if range_header and server.support_range:
            start = int(re.match(r"bytes=(\d+)-", range_header).group(1))
            status = 206
        body = data[start:]
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            if server.disposition:
                self.send_header("Content-Disposition", server.disposition)
            self.end_headers()
            for i in range(0, len(body), 16 * 1024):
                self.wfile.write(body[i:i + 16 * 1024])
                if server.delay:
                    time.sleep(server.delay)
        except (BrokenPipeError, ConnectionResetError):
            pass


def wait_until(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("tempo esgotado esperando a condição")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = os.path.join(self.tmp.name, "downloads")
        self.data_file = Path(self.tmp.name) / "downloads.json"

        self.server = _Server(("127.0.0.1", 0))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

        self.manager = self.make_manager()
        self.addCleanup(self.stop_manager, self.manager)

    def make_manager(self, max_concurrent=3):
        return DownloadManager(
            Settings(download_dir=self.dest, max_concurrent=max_concurrent),
            data_file=self.data_file,
        )

    def stop_manager(self, manager):
        manager.shutdown()
        wait_until(lambda: not manager._workers)

    def add(self, path, manager=None):
        manager = manager or self.manager
        before = {d.id for d in manager.snapshot()}
        manager.add_urls([self.base + path], self.dest)
        return next(d.id for d in manager.snapshot() if d.id not in before)

    def get(self, download_id, manager=None):
        manager = manager or self.manager
        return next(d for d in manager.snapshot() if d.id == download_id)


class DownloadTests(ManagerTestCase):
    def test_completes_and_keeps_name_from_url(self):
        self.server.payload = os.urandom(300_000)
        download_id = self.add("/files/video%20final.mp4")

        wait_until(lambda: self.get(download_id).status == COMPLETED)

        final = Path(self.dest, "video final.mp4")
        self.assertEqual(sha(final.read_bytes()), sha(self.server.payload))
        self.assertFalse(Path(self.dest, "video final.mp4.part").exists())

    def test_uses_content_disposition_name(self):
        self.server.payload = b"abc"
        self.server.disposition = "attachment; filename*=UTF-8''relat%C3%B3rio.pdf"
        download_id = self.add("/x")

        wait_until(lambda: self.get(download_id).status == COMPLETED)
        self.assertEqual(self.get(download_id).filename, "relatório.pdf")

    def test_pause_then_resume_continues_from_partial_file(self):
        self.server.payload = os.urandom(4 * 1024 * 1024)
        self.server.delay = 0.01
        download_id = self.add("/big.bin")

        wait_until(lambda: self.get(download_id).downloaded > 512 * 1024)
        self.manager.pause(download_id)
        wait_until(lambda: self.get(download_id).status == PAUSED and not self.manager._workers)

        part = Path(self.dest, "big.bin.part")
        offset = part.stat().st_size
        self.assertGreaterEqual(offset, 512 * 1024)
        self.assertLess(offset, len(self.server.payload))

        self.manager.resume(download_id)
        wait_until(lambda: self.get(download_id).status == COMPLETED)

        self.assertEqual(sha(Path(self.dest, "big.bin").read_bytes()), sha(self.server.payload))
        self.assertIn(f"bytes={offset}-", self.server.requests)

    def test_resume_restarts_when_server_ignores_range(self):
        self.server.payload = os.urandom(2 * 1024 * 1024)
        self.server.delay = 0.01
        self.server.support_range = False
        download_id = self.add("/plain.bin")

        wait_until(lambda: self.get(download_id).downloaded > 256 * 1024)
        self.manager.pause(download_id)
        wait_until(lambda: not self.manager._workers)
        self.manager.resume(download_id)
        wait_until(lambda: self.get(download_id).status == COMPLETED)

        self.assertEqual(sha(Path(self.dest, "plain.bin").read_bytes()), sha(self.server.payload))

    def test_concurrency_limit_is_respected(self):
        manager = self.make_manager(max_concurrent=2)
        self.addCleanup(self.stop_manager, manager)
        self.server.payload = os.urandom(600_000)
        self.server.delay = 0.005
        for i in range(5):
            self.add(f"/part{i}.bin", manager)

        peak = 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            peak = max(peak, len(manager._workers))
            if all(d.status == COMPLETED for d in manager.snapshot()):
                break
            time.sleep(0.01)

        self.assertEqual(peak, 2)
        self.assertTrue(all(d.status == COMPLETED for d in manager.snapshot()))

    def test_name_collision_gets_numbered(self):
        Path(self.dest).mkdir(parents=True)
        Path(self.dest, "data.bin").write_bytes(b"antigo")
        self.server.payload = b"novo"
        download_id = self.add("/data.bin")

        wait_until(lambda: self.get(download_id).status == COMPLETED)
        self.assertEqual(self.get(download_id).filename, "data (1).bin")
        self.assertEqual(Path(self.dest, "data.bin").read_bytes(), b"antigo")
        self.assertEqual(Path(self.dest, "data (1).bin").read_bytes(), b"novo")

    def test_http_error_marks_item_and_retry_works(self):
        download_id = self.add("/missing/file.zip")
        wait_until(lambda: self.get(download_id).status == ERROR)
        self.assertIn("404", self.get(download_id).error)

        self.manager.resume(download_id)  # "Tentar novamente"
        wait_until(lambda: self.get(download_id).status == ERROR)

    def test_invalid_and_duplicate_lines_are_reported(self):
        result = self.manager.add_urls(
            ["", "não é link", "ftp://x/y", self.base + "/a.bin", self.base + "/a.bin"], self.dest
        )
        self.assertEqual((result.added, result.duplicates, result.invalid), (1, 1, 2))

    def test_state_survives_restart_and_resumes(self):
        self.server.payload = os.urandom(3 * 1024 * 1024)
        self.server.delay = 0.01
        download_id = self.add("/persist.bin")
        wait_until(lambda: self.get(download_id).downloaded > 256 * 1024)

        self.manager.shutdown()  # fechar o app
        wait_until(lambda: not self.manager._workers)

        reopened = self.make_manager()
        self.addCleanup(self.stop_manager, reopened)
        restored = self.get(download_id, reopened)
        self.assertEqual(restored.status, PAUSED)
        self.assertGreater(restored.downloaded, 0)

        reopened.resume(download_id)
        wait_until(lambda: self.get(download_id, reopened).status == COMPLETED)
        self.assertEqual(sha(Path(self.dest, "persist.bin").read_bytes()), sha(self.server.payload))

    def test_removing_active_download_deletes_partial(self):
        self.server.payload = os.urandom(3 * 1024 * 1024)
        self.server.delay = 0.01
        download_id = self.add("/gone.bin")
        wait_until(lambda: self.get(download_id).downloaded > 128 * 1024)

        self.manager.remove(download_id)
        wait_until(lambda: not self.manager._workers)
        self.assertFalse(Path(self.dest, "gone.bin.part").exists())
        self.assertEqual(self.manager.snapshot(), [])

    def test_queued_status_before_slot_is_free(self):
        manager = self.make_manager(max_concurrent=1)
        self.addCleanup(self.stop_manager, manager)
        self.server.payload = os.urandom(2 * 1024 * 1024)
        self.server.delay = 0.01
        first = self.add("/one.bin", manager)
        second = self.add("/two.bin", manager)

        self.assertEqual(self.get(first, manager).status, DOWNLOADING)
        self.assertEqual(self.get(second, manager).status, QUEUED)


class SegmentedTests(ManagerTestCase):
    def test_large_file_is_split_into_parts(self):
        self.server.payload = os.urandom(20 * 1024 * 1024)
        download_id = self.add("/big.bin")
        wait_until(lambda: self.get(download_id).status == COMPLETED, timeout=60)
        self.assertEqual(sha(Path(self.dest, "big.bin").read_bytes()), sha(self.server.payload))
        starts = {r for r in self.server.requests if r and r != "bytes=0-0"}
        self.assertGreaterEqual(len(starts), 4)
        self.assertFalse(Path(self.dest, "big.bin.part").exists())
        self.assertFalse(Path(self.dest, "big.bin.part.seg").exists())

    def test_pause_and_resume_segmented(self):
        self.server.payload = os.urandom(16 * 1024 * 1024)
        self.server.delay = 0.002
        download_id = self.add("/big.bin")
        wait_until(lambda: self.get(download_id).downloaded > 1_000_000)
        self.manager.pause(download_id)
        wait_until(lambda: not self.manager._workers)
        self.assertTrue(Path(self.dest, "big.bin.part.seg").exists())
        self.server.delay = 0
        self.manager.resume(download_id)
        wait_until(lambda: self.get(download_id).status == COMPLETED, timeout=60)
        self.assertEqual(sha(Path(self.dest, "big.bin").read_bytes()), sha(self.server.payload))


class SettingsTests(unittest.TestCase):
    def test_saved_download_folder_is_loaded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(Settings(download_dir="/dados/filmes", max_concurrent=4), path)

            loaded = load_settings(path)

            self.assertEqual(loaded.download_dir, "/dados/filmes")
            self.assertEqual(loaded.max_concurrent, 4)

    def test_missing_or_broken_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertEqual(load_settings(path), Settings())
            path.write_text("não é json", encoding="utf-8")
            self.assertEqual(load_settings(path), Settings())


if __name__ == "__main__":
    unittest.main()
