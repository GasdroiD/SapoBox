"""Janela inicial do SapoBox: três ferramentas, um clique para abrir cada uma."""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .theme import ROOT  # noqa: E402
from . import i18n  # noqa: E402
from .i18n import tr  # noqa: E402

APP_ID = "org.sapobox.SapoBox"

TOOLS = (
    ("converter", "org.sapobox.Converter", tr("Conversor"),
     tr("Converte jogos para FF-PFSC ou LZ4, sem mexer no original.")),
    ("extrator", "org.sapobox.Extrator", tr("Extrator"),
     tr("Extrai 7z, ZIP e RAR, inclusive arquivos divididos e com senha.")),
    ("downloader", "org.sapobox.Downloader", "Downloader",
     tr("Baixa links HTTP/HTTPS com fila, pausa e retomada.")),
)


def launch(tool):
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
    launcher.setenv("PYTHONPATH", str(ROOT), True)
    launcher.set_cwd(str(ROOT))
    try:
        launcher.spawnv([sys.executable, "-m", "sapobox", tool])
    except GLib.Error as erro:
        print(tr('Não foi possível abrir {0}: {1}').format(tool, erro.message), file=sys.stderr)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="SapoBox", default_width=460, default_height=600, resizable=False)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        idiomas = Gio.Menu()
        for codigo, nome in i18n.IDIOMAS.items():
            idiomas.append(nome, f"app.idioma('{codigo}')")
        menu = Gio.Menu()
        menu.append_section("Idioma / Language", idiomas)
        header.pack_end(Gtk.MenuButton(icon_name="preferences-desktop-locale-symbolic", menu_model=menu,
                                       tooltip_text="Idioma / Language"))
        view.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12,
                      margin_bottom=24, margin_start=24, margin_end=24)
        logo = Gtk.Image(icon_name=APP_ID, pixel_size=112)
        title = Gtk.Label(label="SapoBox")
        title.add_css_class("sapo-title")
        subtitle = Gtk.Label(label=tr("Pule para a ferramenta que precisa"), wrap=True)
        subtitle.add_css_class("dim-label")
        for widget in (logo, title, subtitle):
            box.append(widget)
        lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lista.add_css_class("boxed-list")
        for tool, icon, name, text in TOOLS:
            row = Adw.ActionRow(title=name, subtitle=text, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=icon, pixel_size=48))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, t=tool: launch(t))
            lista.append(row)
        box.append(lista)
        clamp = Adw.Clamp(maximum_size=520, child=box)
        view.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_content(view)


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        sobre = Gio.SimpleAction.new("sobre", None)
        sobre.connect("activate", self.sobre)
        self.add_action(sobre)
        sair = Gio.SimpleAction.new("quit", None)
        sair.connect("activate", lambda *_: self.quit())
        self.add_action(sair)
        idioma = Gio.SimpleAction.new_stateful("idioma", GLib.VariantType.new("s"), GLib.Variant.new_string(i18n.IDIOMA))
        idioma.connect("activate", self.mudar_idioma)
        self.add_action(idioma)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self):
        (self.props.active_window or Window(self)).present()

    def mudar_idioma(self, action, valor):
        codigo = valor.get_string()
        if codigo == i18n.IDIOMA:
            return
        i18n.salvar(codigo)
        os.environ["SAPOBOX_LANG"] = codigo
        os.chdir(ROOT)
        os.environ["PYTHONPATH"] = str(ROOT)
        os.execv(sys.executable, [sys.executable, "-m", "sapobox"])

    def sobre(self, *_):
        Adw.AboutDialog(application_name="SapoBox", application_icon=APP_ID, version=__version__,
                        comments=tr("Conversor de jogos, Extrator de arquivos e Downloader, numa só caixa."),
                        license_type=Gtk.License.GPL_3_0).present(self.props.active_window)


def run(argv):
    GLib.set_prgname("sapobox")
    GLib.set_application_name("SapoBox")
    return Application().run(argv)
