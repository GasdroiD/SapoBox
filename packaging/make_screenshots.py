#!/usr/bin/env python3
"""Gera as capturas de tela da pasta docs/screenshots (uma por janela e por idioma).

Uso: python3 packaging/make_screenshots.py [pasta_de_saida]
Cada janela é aberta em um processo próprio, renderizada pelo próprio GTK e fechada. Os dados do
usuário não entram nas imagens: configuração e dados apontam para uma pasta temporária.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("hub", "converter", "extrator", "downloader")
LANGS = ("pt", "en")


def capture(out_png: str, tool: str) -> int:
    """Executado no processo filho: abre a ferramenta e salva a janela em PNG."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gio, GLib, Gtk, Graphene

    def shot():
        windows = [w for w in Gtk.Window.list_toplevels() if w.get_visible()]
        if not windows:
            return True
        window = windows[0]
        paintable = Gtk.WidgetPaintable.new(window)
        width, height = window.get_width(), window.get_height()
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        texture = window.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
        texture.save_to_png(out_png)
        Gio.Application.get_default().quit()
        return False

    GLib.timeout_add(3500, shot)
    from sapobox.__main__ import main
    return main(["sapobox"] + ([] if tool == "hub" else [tool]))


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        sys.path.insert(0, str(ROOT))
        return capture(sys.argv[2], sys.argv[3])
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for lang in LANGS:
            for tool in TOOLS:
                target = out / f"{tool}-{lang}.png"
                env = dict(os.environ, SAPOBOX_LANG=lang, PYTHONPATH=str(ROOT),
                           XDG_CONFIG_HOME=f"{tmp}/config", XDG_DATA_HOME=f"{tmp}/data",
                           XDG_CACHE_HOME=f"{tmp}/cache")
                subprocess.run([sys.executable, __file__, "--child", str(target), tool], env=env, check=False, timeout=60)
                print(target, "ok" if target.exists() else "FALHOU")


if __name__ == "__main__":
    sys.exit(main())
