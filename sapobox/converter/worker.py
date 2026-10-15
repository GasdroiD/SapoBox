"""CLI worker: JSON line protocol keeps GUI responsive and isolated from C crashes."""
import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

from . import engine
from .workflow import run
from .lz4 import convert as lz4_convert, cancel_active
from ..i18n import tr  # noqa: E402

CANCELLED = tr("Conversão cancelada")


def emit(**fields):
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=tr("Converter jogo para FF-PFSC"))
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--apr-module")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--level", type=int, default=1)
    parser.add_argument("--format", choices=("ffpfsc", "lz4"), default="ffpfsc")
    parser.add_argument("--profile", help=tr("Perfil TOML para saída LZ4"))
    args = parser.parse_args()
    if not 1 <= args.workers <= 16 or not 1 <= args.level <= (12 if args.format == "lz4" else 9):
        parser.error(tr("workers deve estar entre 1 e 16; level entre 1 e 9 (FF-PFSC) ou 1 e 12 (LZ4)"))
    if args.profile and args.format != "lz4":
        parser.error(tr("--profile só pode ser usado com --format lz4"))
    if not Path(args.source).exists():
        emit(event="error", text=tr('Origem não encontrada: {0}').format(args.source))
        return 1
    if not Path(args.destination).is_dir():
        emit(event="error", text=tr('Destino precisa ser uma pasta existente: {0}').format(args.destination))
        return 1

    cancelled = threading.Event()

    def cancel(*_):
        cancelled.set()
        if args.format == "lz4":
            cancel_active()
        else:
            engine.library().converter_cancel()

    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGTERM, cancel)

    def check():
        if cancelled.is_set():
            raise InterruptedError(CANCELLED)

    stop = threading.Event()

    def report():
        while not stop.wait(.5):
            done, total, written, current = engine.progress()
            emit(event="progress", done=done, total=total, written=written,
                 current=current, timestamp=time.monotonic())

    thread = None
    if args.format == "ffpfsc":
        thread = threading.Thread(target=report, daemon=True)
        thread.start()
    try:
        if args.format == "lz4":
            output, stats = lz4_convert(
                args.source, args.destination, args.apr_module,
                args.workers, args.level, args.profile,
                callback=lambda status: emit(event="status", text=status),
                progress=lambda done, total, phase: emit(
                    event="progress", done=done, total=total, written=0,
                    current=phase, timestamp=time.monotonic(), phase=phase), check=check)
            emit(event="done", path=str(output), apr=True, stats=stats)
        else:
            output, apr = run_native(args, check)
            emit(event="done", path=str(output), apr=apr)
    except Exception as exc:
        emit(event="error", text=CANCELLED if cancelled.is_set() else str(exc))
        return 1
    finally:
        stop.set()
        if thread:
            thread.join()
    return 0


def run_native(args, check):
    """Run the C writer off the main thread.

    Python signal handlers only execute on the main thread between bytecodes, so
    a long ctypes call would block SIGTERM until the conversion finished. Keeping
    the main thread in short joins lets cancel() reach the native flag at once.
    """
    outcome = {}

    def job():
        try:
            outcome["value"] = run(args.source, args.destination, args.apr_module,
                                   args.workers, args.level,
                                   callback=lambda status: emit(event="status", text=status),
                                   check=check)
        except BaseException as exc:
            outcome["error"] = exc

    native = threading.Thread(target=job)
    native.start()
    while native.is_alive():
        native.join(0.2)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


if __name__ == "__main__":
    sys.exit(main())
