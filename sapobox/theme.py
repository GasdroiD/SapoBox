"""Tema verde-sapo e ícones, aplicados a todas as ferramentas."""
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

CSS = """
@define-color accent_bg_color #2e7d32;
@define-color accent_fg_color #ffffff;
@define-color accent_color #66bb6a;
@define-color window_bg_color #262626;
@define-color window_fg_color #e6e6e6;
@define-color headerbar_bg_color #1a1a1a;
@define-color headerbar_fg_color #e6e6e6;
@define-color card_bg_color #303030;
@define-color card_fg_color #e6e6e6;
@define-color view_bg_color #2c2c2c;
@define-color view_fg_color #e6e6e6;
@define-color sidebar_bg_color #202020;
@define-color popover_bg_color #303030;
@define-color dialog_bg_color #2a2a2a;
@define-color success_bg_color #2e7d32;
@define-color success_color #66bb6a;
progressbar progress { background-color: #43a047; }
.sapo-title { font-weight: 800; font-size: 28pt; color: #81c784; }
"""


def apply(icon_name=None):
    """Chame depois de Adw.init(). Registra ícones, cores e o ícone padrão das janelas."""
    display = Gdk.Display.get_default()
    if display is None:
        return
    icons = ROOT / "packaging" / "icons"
    if icons.is_dir():
        Gtk.IconTheme.get_for_display(display).add_search_path(str(icons))
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    if icon_name:
        Gtk.Window.set_default_icon_name(icon_name)
