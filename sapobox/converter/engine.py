"""ctypes bridge to the upstream-derived native PFSC writer."""
import ctypes as c
import os
from pathlib import Path

from .sources import Entry
from ..i18n import tr  # noqa: E402


class Extent(c.Structure):
    _fields_ = [("logical", c.c_uint64), ("physical", c.c_uint64), ("size", c.c_uint64)]


class NativeEntry(c.Structure):
    _fields_ = [("rel", c.c_char_p), ("source", c.c_char_p), ("size", c.c_uint64),
                ("mtime", c.c_int64), ("extents", c.POINTER(Extent)), ("count", c.c_size_t)]


def library():
    path = Path(__file__).with_name("libconverter.so")
    if not path.exists():
        raise FileNotFoundError(tr("Motor nativo ausente. Execute 'make' na pasta do projeto."))
    lib = c.CDLL(str(path))
    lib.converter_run.argtypes = [c.POINTER(NativeEntry), c.c_size_t, c.c_char_p,
                                   c.c_char_p, c.c_char_p, c.c_int, c.c_int,
                                   c.c_char_p, c.c_size_t]
    lib.converter_run.restype = c.c_int
    lib.converter_index.argtypes = [c.POINTER(NativeEntry), c.c_size_t, c.c_char_p,
                                     c.c_char_p, c.c_size_t]
    lib.converter_index.restype = c.c_int
    lib.converter_progress.argtypes = [c.POINTER(c.c_uint64), c.POINTER(c.c_uint64),
                                        c.POINTER(c.c_uint64), c.c_char_p, c.c_size_t]
    lib.converter_cancel.argtypes = []
    return lib


def marshal(entries):
    arrays = []
    refs = []
    for e in entries:
        extents = (Extent * len(e.extents))(*(Extent(*x) for x in e.extents))
        arrays.append(extents)
        refs.append(NativeEntry(e.rel.encode(), e.source.encode(), e.size, e.mtime,
                                extents, len(extents)))
    return (NativeEntry * len(refs))(*refs), arrays


def make_index(entries, output):
    native, arrays = marshal(entries)
    error = c.create_string_buffer(1024)
    if library().converter_index(native, len(entries), os.fsencode(output), error, len(error)):
        raise RuntimeError(error.value.decode(errors="replace") or tr("Erro ao gerar índice APR"))


def convert(entries, code, source, output, workers=4, level=1):
    native, arrays = marshal(entries)
    error = c.create_string_buffer(1024)
    if library().converter_run(native, len(entries), code.encode(), os.fsencode(source),
                               os.fsencode(output), workers, level, error, len(error)):
        raise RuntimeError(error.value.decode(errors="replace") or tr("Falha na conversão"))


def progress():
    done, total, written = c.c_uint64(), c.c_uint64(), c.c_uint64()
    current = c.create_string_buffer(512)
    library().converter_progress(c.byref(done), c.byref(total), c.byref(written),
                                 current, len(current))
    return done.value, total.value, written.value, current.value.decode(errors="replace")
