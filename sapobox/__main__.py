"""Ponto de entrada: `sapobox` abre a caixa; `sapobox converter|extrator|downloader` abre uma ferramenta."""
import os
import sys

TOOLS = {
    "converter": "org.sapobox.Converter",
    "extrator": "org.sapobox.Extrator",
    "downloader": "org.sapobox.Downloader",
}


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--lang" in argv:  # --lang pt|en vale para esta execução e para as ferramentas abertas por ela
        i = argv.index("--lang")
        os.environ["SAPOBOX_LANG"] = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    tool = argv[1] if len(argv) > 1 and argv[1] in TOOLS else None
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print("uso: sapobox [--lang pt|en] [converter|extrator|downloader] [argumentos]")
        return 0
    from . import theme
    from gi.repository import Adw
    Adw.init()
    if tool is None:
        from .hub import run
        theme.apply("org.sapobox.SapoBox")
        return run(argv)
    theme.apply(TOOLS[tool])
    rest = [argv[0]] + argv[2:]
    if tool == "converter":
        from .converter.gui import main as run_tool
    elif tool == "extrator":
        from .extrator.gui import main as run_tool
    else:
        from .downloader.ui import DownloaderApp
        return DownloaderApp().run(rest)
    return run_tool(rest)


if __name__ == "__main__":
    sys.exit(main())
