"""Protocolo do worker: mensagens em português e cancelamento do motor FF-PFSC."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent


def make_game(root, size):
    (root / "sce_sys").mkdir(parents=True)
    (root / "sce_sys" / "param.json").write_text(json.dumps({
        "titleId": "PPSA77777",
        "localizedParameters": {"defaultLanguage": "en-US", "en-US": {"titleName": "Cancel"}},
    }))
    with (root / "data.bin").open("wb") as stream:
        for _ in range(size // (1 << 20)):
            stream.write(os.urandom(1 << 20))


def last_event(stdout):
    lines = [line for line in stdout.splitlines() if line.startswith("{")]
    return json.loads(lines[-1])


class WorkerTest(unittest.TestCase):
    def test_missing_destination_is_reported_in_portuguese(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp, "game")
            make_game(game, 1 << 20)
            result = subprocess.run(
                [sys.executable, "-m", "sapobox.converter.worker", str(game), str(Path(tmp, "inexistente"))],
                cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1)
        event = last_event(result.stdout)
        self.assertEqual(event["event"], "error")
        self.assertIn("Destino precisa ser uma pasta existente", event["text"])

    def test_sigterm_stops_native_conversion_promptly(self):
        with tempfile.TemporaryDirectory() as tmp:
            game, out = Path(tmp, "game"), Path(tmp, "out")
            make_game(game, 400 << 20)
            out.mkdir()
            proc = subprocess.Popen(
                [sys.executable, "-m", "sapobox.converter.worker", str(game), str(out), "--level", "1",
                 "--workers", "2"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                # Wait until the native writer has really started, then cancel.
                for line in proc.stdout:
                    if '"event": "progress"' in line and '"done": 0,' not in line:
                        break
                started = time.monotonic()
                proc.terminate()
                stdout, _ = proc.communicate(timeout=30)
                elapsed = time.monotonic() - started
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            self.assertLess(elapsed, 10, "SIGTERM ignorado durante a conversão nativa")
            self.assertEqual(last_event(stdout)["event"], "error")
            self.assertEqual(last_event(stdout)["text"], "Conversão cancelada")
            self.assertEqual(list(out.iterdir()), [], "saída parcial não foi removida")


if __name__ == "__main__":
    unittest.main()
