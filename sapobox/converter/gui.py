"""Interface GTK 4 + libadwaita do SapoBox Conversor (Debian 13).

Fala com o motor só por subprocessos (Gio.Subprocess): o motor nativo e o packer LZ4
continuam isolados do processo da janela. As regras de validação e os textos ficam em
converter/gui_logic.py, testáveis sem display.
"""
import configparser
import contextlib
import json
from pathlib import Path
import shlex
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .gui_logic import (  # noqa: E402
    APP_NAME, COMPRESSION, DEFAULT_LEVEL, FFPFSC, FORMATS, LZ4, OUTPUT_HELP, OUTPUT_TITLES,
    PHASE_TITLES, PROBE_TONE, THREADS_HELP, Form, Progress, default_level, default_workers,
    is_ffpfsc, level_index, local_runtime, module_needed, problems, saving_text, short_path)
from .sources import occupied_bytes  # noqa: E402
from .worker import CANCELLED  # noqa: E402
from ..i18n import tr  # noqa: E402

APP_ID = "org.sapobox.Converter"
ROOT = Path(__file__).resolve().parent.parent.parent
CANCEL_GRACE_S = 15
PROBE_DELAY_MS = 350
COLUMN_SPACING = 16
CARD_MIN_WIDTH = 380
# Tamanho mínimo da janela: comporta as duas colunas inteiras, inclusive com LZ4 e módulo.
MIN_WIDTH = 880
MIN_HEIGHT = 720


# ----- configurações ---------------------------------------------------

def settings_path():
    return Path(GLib.get_user_config_dir()) / "ps5-converter" / "settings.json"


def load_settings():
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    # Migra os caminhos salvos pela versão Qt anterior, se existirem.
    legacy = Path(GLib.get_user_config_dir()) / "PS5Converter" / "Desktop.conf"
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(legacy, encoding="utf-8")
    except configparser.Error:
        return {}
    return dict(parser["General"]) if parser.has_section("General") else {}


def save_settings(values):
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # preferências são opcionais; nunca devem impedir a conversão


# ----- janela ----------------------------------------------------------

class Card(Gtk.Box):
    """Bloco com título, legenda e lista de linhas. Estica para ocupar a célula da grade."""

    def __init__(self, title, caption=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True,
                         vexpand=True, valign=Gtk.Align.FILL)
        # Largura mínima do cartão: textos com quebra de linha medem altura na largura mínima,
        # então sem isso o GTK pediria uma altura enorme para a janela.
        self.set_size_request(CARD_MIN_WIDTH, -1)
        self.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
        self.caption = Gtk.Label(label=caption, xalign=0, wrap=True, css_classes=["dim-label", "caption"])
        self.append(self.caption)
        self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                vexpand=True, valign=Gtk.Align.FILL)
        self.append(self.list)

    def add(self, row):
        self.list.append(row)


def toggle_row(title, caption, widget):
    """Linha de largura total com título, legenda e um seletor segmentado."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                  margin_start=12, margin_end=12)
    note = Gtk.Label(label=caption, xalign=0, wrap=True, css_classes=["dim-label", "caption"])
    box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
    box.append(note)
    box.append(widget)
    return Gtk.ListBoxRow(activatable=False, selectable=False, child=box), note


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=MIN_WIDTH,
                         default_height=MIN_HEIGHT + 40)
        # Abaixo deste tamanho o conteúdo não cabe e a janela não pode encolher.
        self.set_size_request(MIN_WIDTH, MIN_HEIGHT)
        self.form = Form()
        self.runtime = local_runtime()
        self.manual_module = False
        self.auto_module = False
        self.level = DEFAULT_LEVEL[FFPFSC]
        self.progress = Progress()
        self.game = None           # (código, nome, texto, tipo) depois da detecção
        self.worker = None         # Gio.Subprocess da conversão
        self.probe = None          # Gio.Subprocess da detecção
        self.probe_generation = 0
        self.probe_timer = 0
        self.cancel_timer = 0
        self.pulse_timer = 0
        self.pending = set()
        self.exit_code = None
        self.cancelling = False
        self.closing = False
        self.result = None         # ("done" | "cancelled" | "error", texto)
        self.run_source = ""       # origem da conversão em curso, medida ao terminar
        self.current_output = ""   # caminho do resultado, quando a conversão terminou bem
        self.log_lines = []
        self._quiet = 0

        self.build_ui()
        self.restore()
        self.connect_signals()
        self.install_actions()
        self.install_drop_target()
        self.connect("close-request", self.on_close_request)
        self.refresh()
        if self.form.source:
            self.schedule_probe()

    # ----- montagem da tela -------------------------------------------

    def build_ui(self):
        # Só a página visível define o tamanho: a tela de resultado, com ícone grande, não pode
        # aumentar a altura mínima da janela de configuração.
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               hhomogeneous=False, vhomogeneous=False)
        self.stack.add_named(self.build_setup(), "setup")
        self.stack.add_named(self.build_running(), "running")
        self.stack.add_named(self.build_result(), "result")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        self.set_content(self.toasts)

    def build_setup(self):
        # Grade 2x2: cada linha tem a mesma altura, então os blocos ficam simétricos e sem rolagem.
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=False,
                        column_spacing=COLUMN_SPACING, row_spacing=COLUMN_SPACING,
                        margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
                        hexpand=True, vexpand=True, valign=Gtk.Align.FILL)
        grid.attach(self.group_game(), 0, 0, 1, 1)
        grid.attach(self.group_performance(), 1, 0, 1, 1)
        grid.attach(self.group_output(), 0, 1, 1, 1)
        self.module_group = self.group_module()
        grid.attach(self.module_group, 1, 1, 1, 1)

        self.hint = Gtk.Label(xalign=0, hexpand=True, wrap=True, css_classes=["dim-label"])
        self.start_button = Gtk.Button(label=tr("Converter"), valign=Gtk.Align.CENTER,
                                       css_classes=["suggested-action", "pill"])
        self.start_button.connect("clicked", lambda _b: self.start())
        bar = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=18, margin_end=18)
        bar.append(self.hint)
        bar.append(self.start_button)
        return self.toolbar(grid, tr("Nova conversão"), bottom=bar)
    def build_running(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      width_request=340, halign=Gtk.Align.CENTER)
        self.run_bar = Gtk.ProgressBar()
        self.run_stats = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        self.cancel_button = Gtk.Button(label=tr("Cancelar"), halign=Gtk.Align.CENTER,
                                        css_classes=["destructive-action", "pill"])
        self.cancel_button.connect("clicked", lambda _b: self.cancel_run())
        for widget in (self.run_bar, self.run_stats, self.cancel_button):
            box.append(widget)
        self.run_page = Adw.StatusPage(icon_name="system-run-symbolic", title=tr("Convertendo…"),
                                       description=tr("Preparando…"))
        self.run_page.set_child(box)
        return self.toolbar(self.run_page, tr("Convertendo"))

    def build_result(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.open_button = Gtk.Button(label=tr("Abrir pasta de saída"), halign=Gtk.Align.CENTER,
                                      css_classes=["suggested-action", "pill"])
        self.open_button.connect("clicked", lambda _b: self.open_destination())
        log_button = Gtk.Button(label=tr("Ver registro técnico"), halign=Gtk.Align.CENTER,
                                css_classes=["flat"])
        log_button.connect("clicked", lambda _b: self.show_log())
        again_button = Gtk.Button(label=tr("Nova conversão"), halign=Gtk.Align.CENTER,
                                  css_classes=["pill"])
        again_button.connect("clicked", lambda _b: self.new_conversion())
        for widget in (self.open_button, log_button, again_button):
            box.append(widget)
        self.result_page = Adw.StatusPage(icon_name="object-select-symbolic", title="", description="")
        self.result_page.set_child(box)
        return self.toolbar(self.result_page, tr("Resultado"))

    def toolbar(self, content, title, bottom=None):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle.new(title, ""))
        header.pack_end(self.menu_button())
        view.add_top_bar(header)
        view.set_content(content)
        if bottom is not None:
            view.add_bottom_bar(bottom)
        return view

    def menu_button(self):
        menu = Gio.Menu()
        menu.append(tr("Registro técnico"), "win.log")
        menu.append(tr("Sobre o SapoBox Conversor"), "app.about")
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                              tooltip_text=tr("Menu principal"))

    def button(self, label=None, icon=None, tooltip="", handler=None):
        if label is None:
            widget = Gtk.Button.new_from_icon_name(icon)
        else:
            widget = Gtk.Button(valign=Gtk.Align.CENTER)
            widget.set_child(Adw.ButtonContent(label=label, icon_name=icon or ""))
        widget.add_css_class("flat")
        widget.set_tooltip_text(tooltip)
        if handler is not None:
            widget.connect("clicked", lambda _b: handler())
        return widget

    def group_game(self):
        card = Card(tr("Jogo"), tr("O original nunca é alterado."))
        self.source_row = Adw.ActionRow(title=tr("Origem"), subtitle=tr("Nenhum jogo escolhido"))
        self.source_row.set_subtitle_lines(1)
        self.source_row.add_suffix(self.button(tr("Pasta"), "folder-open-symbolic",
                                               tr("Escolher a pasta do jogo"), self.choose_source_folder))
        self.source_row.add_suffix(self.button(tr("Arquivo"), "drive-harddisk-symbolic",
                                               tr("Imagem .exfat ou arquivo .ffpfsc"),
                                               self.choose_source_image))
        card.add(self.source_row)

        self.detect_row = Adw.ActionRow(title=tr("Detecção"), subtitle=tr("Escolha um jogo para começar"))
        self.detect_row.set_subtitle_lines(0)
        self.detect_icon = Gtk.Image.new_from_icon_name("applications-games-symbolic")
        self.detect_spinner = Adw.Spinner()
        self.detect_spinner.set_visible(False)
        self.detect_row.add_prefix(self.detect_icon)
        self.detect_row.add_prefix(self.detect_spinner)
        card.add(self.detect_row)
        return card

    def group_output(self):
        card = Card(tr("Saída"), tr("Onde o resultado é criado e em que formato."))
        self.dest_row = Adw.ActionRow(title=tr("Pasta de saída"), subtitle=tr("Nenhuma pasta escolhida"))
        self.dest_row.set_subtitle_lines(1)
        self.dest_row.add_suffix(self.button(tr("Escolher"), "folder-open-symbolic",
                                             tr("Escolher a pasta de saída"), self.choose_dest))
        card.add(self.dest_row)

        self.format_toggle = Adw.ToggleGroup(homogeneous=True)
        self.format_toggle.add(Adw.Toggle(name=FFPFSC, label=OUTPUT_TITLES[FFPFSC]))
        self.format_toggle.add(Adw.Toggle(name=LZ4, label=OUTPUT_TITLES[LZ4]))
        self.format_row, self.format_caption = toggle_row(tr("Formato"), OUTPUT_HELP[FFPFSC], self.format_toggle)
        card.add(self.format_row)
        return card

    def group_performance(self):
        card = Card(tr("Desempenho"), tr("Velocidade, tamanho e uso do processador."))
        self.level_toggle = Adw.ToggleGroup(homogeneous=True)
        for index, label in enumerate((tr("Rápida"), tr("Equilibrada"), tr("Menor"))):
            self.level_toggle.add(Adw.Toggle(name=str(index), label=label))
        row, self.level_caption = toggle_row(tr("Compressão"), COMPRESSION[FFPFSC][0][2], self.level_toggle)
        card.add(row)

        self.threads_row = Adw.SpinRow.new_with_range(1, 16, 1)
        self.threads_row.set_title(tr("Threads de compressão"))
        self.threads_row.set_subtitle(THREADS_HELP)
        self.threads_row.set_subtitle_lines(0)
        card.add(self.threads_row)
        return card

    def group_module(self):
        card = Card(tr("Módulo e perfil"), tr("APR Emu (libSceAmpr) e perfil de pacotes LZ4."))
        self.module_row = Adw.ActionRow(title="libSceAmpr", subtitle=tr("Não escolhido"))
        self.module_row.set_subtitle_lines(0)
        self.use_runtime_button = self.button(tr("Usar o do projeto"), "applications-system-symbolic",
                                              tr("Usar o runtime PackedStdio que vem com o projeto"),
                                              self.use_project_runtime)
        self.use_runtime_button.set_visible(bool(self.runtime))
        self.module_row.add_suffix(self.use_runtime_button)
        self.module_row.add_suffix(self.button(None, "document-open-symbolic",
                                               tr("Escolher um arquivo libSceAmpr.sprx"), self.choose_module))
        self.module_row.add_suffix(self.button(None, "edit-clear-symbolic",
                                               tr("Remover o módulo escolhido"), self.clear_module))
        card.add(self.module_row)

        # Recolhido por padrão: o perfil automático de referência serve na maioria dos casos.
        self.profile_row = Adw.ExpanderRow(title=tr("Perfil de pacotes (LZ4)"), subtitle=tr("Automático"))
        self.profile_pick = Adw.ActionRow(title=tr("Arquivo de perfil .toml"),
                                          subtitle=tr("Opcional. Sem perfil, usa a regra automática de referência."))
        self.profile_pick.set_subtitle_lines(3)
        self.profile_pick.add_suffix(self.button(tr("Escolher"), "document-open-symbolic",
                                                 tr("Escolher um perfil .toml"), self.choose_profile))
        self.profile_pick.add_suffix(self.button(None, "edit-clear-symbolic",
                                                 tr("Voltar ao perfil automático"), self.clear_profile))
        self.profile_row.add_row(self.profile_pick)
        card.add(self.profile_row)
        self.profile_group = self.profile_row  # visível só no LZ4
        return card

    # ----- ações e sinais ---------------------------------------------

    def install_actions(self):
        actions = {
            "choose-folder": self.choose_source_folder,
            "choose-image": self.choose_source_image,
            "choose-dest": self.choose_dest,
            "start": self.start,
            "cancel": self.cancel_run,
            "log": self.show_log,
            "new": self.new_conversion,
        }
        for name, handler in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            self.add_action(action)

    def install_drop_target(self):
        target = Gtk.DropTarget.new(Gio.File.__gtype__, Gdk.DragAction.COPY)
        target.connect("drop", self.on_drop)
        self.add_controller(target)

    def connect_signals(self):
        self.format_toggle.connect("notify::active-name", lambda *_: self.format_changed())
        self.level_toggle.connect("notify::active", lambda *_: self.level_changed())
        self.threads_row.connect("notify::value", lambda *_: self.save())

    @contextlib.contextmanager
    def quiet(self):
        """Muda widgets por código sem disparar os handlers do usuário."""
        self._quiet += 1
        try:
            yield
        finally:
            self._quiet -= 1

    def on_drop(self, _target, value, _x, _y):
        if self.worker is not None or value is None:
            return False
        path = value.get_path()
        if not path:
            return False
        self.set_source(path)
        return True

    def on_close_request(self, _window):
        if self.worker is None:
            self.cancel_probe()
            return False
        dialog = Adw.AlertDialog(heading=tr("Cancelar a conversão?"),
                                 body=tr("A origem não será alterada e nada fica no destino."))
        dialog.add_response("keep", tr("Continuar"))
        dialog.add_response("stop", tr("Cancelar e fechar"))
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("keep")
        dialog.set_close_response("keep")
        dialog.connect("response", self.on_close_response)
        dialog.present(self)
        return True

    def on_close_response(self, _dialog, response):
        if response == "stop":
            self.closing = True
            self.cancel_run()

    # ----- escolhas do usuário ----------------------------------------

    def choose_source_folder(self):
        self.pick_path(True, tr("Pasta do jogo"), self.set_source)

    def choose_source_image(self):
        self.pick_path(False, tr("Imagem .exfat ou arquivo .ffpfsc"), self.set_source,
                       ["*.exfat", "*.ffpfsc"])

    def choose_dest(self):
        self.pick_path(True, tr("Pasta de saída"), self.set_dest)

    def choose_module(self):
        self.pick_path(False, tr("Módulo libSceAmpr"), self.set_module, ["*.sprx", "*.prx"])

    def choose_profile(self):
        self.pick_path(False, tr("Perfil de pacotes"), self.set_profile, ["*.toml"])

    def pick_path(self, folder, title, apply, patterns=None):
        dialog = Gtk.FileDialog(title=title, modal=True)
        if patterns:
            file_filter = Gtk.FileFilter()
            for pattern in patterns:
                file_filter.add_pattern(pattern)
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(file_filter)
            dialog.set_filters(filters)
        if folder:
            dialog.select_folder(self, None, lambda d, r: self.picked(d.select_folder_finish, r, apply))
        else:
            dialog.open(self, None, lambda d, r: self.picked(d.open_finish, r, apply))

    def picked(self, finish, result, apply):
        try:
            file = finish(result)
        except GLib.Error:
            return  # o usuário cancelou o seletor
        if file is not None and file.get_path():
            apply(file.get_path())

    def set_source(self, path):
        if path == self.form.source:
            return
        self.form.source = path
        if is_ffpfsc(path) and self.form.output_format == FFPFSC:
            # Um .ffpfsc já é FF-PFSC: a conversão útil dele é para LZ4.
            self.format_toggle.set_active_name(LZ4)
            self.toast(tr("Arquivo .ffpfsc: formato definido para LZ4."))
        self.schedule_probe()
        self.save()

    def set_dest(self, path):
        self.form.dest = path
        self.save()
        self.refresh()  # o aviso "saída dentro do jogo" aparece na linha do destino

    def set_module(self, path):
        self.manual_module = True
        self.auto_module = False
        self.form.module = path
        self.save()
        self.refresh()

    def clear_module(self):
        self.manual_module = True
        self.auto_module = False
        self.form.module = ""
        self.save()
        self.refresh()

    def use_project_runtime(self):
        if self.runtime:
            self.set_module(self.runtime)

    def set_profile(self, path):
        self.form.profile = path
        self.save()
        self.refresh()

    def clear_profile(self):
        self.form.profile = ""
        self.save()
        self.refresh()

    def format_changed(self):
        if self._quiet:
            return
        fmt = self.format_toggle.get_active_name() or FFPFSC
        if fmt == self.form.output_format:
            return
        self.form.output_format = fmt
        self.level = default_level(fmt)
        self.populate_levels()
        self.update_auto_module()
        self.save()
        self.refresh()

    def set_output_format(self, value):
        self.format_toggle.set_active_name(value)
    def level_changed(self):
        if self._quiet:
            return
        index = self.level_toggle.get_active()
        if index < 0:
            return
        self.level = COMPRESSION[self.form.output_format][index][1]
        self.save()
        self.refresh()
    def populate_levels(self):
        with self.quiet():
            self.level_toggle.set_active(level_index(self.form.output_format, self.level))
    # ----- estado e textos --------------------------------------------

    def update_auto_module(self):
        """Preenche o runtime quando o formato ou a detecção exigem; nunca sobrescreve escolha manual."""
        if self.manual_module:
            return
        if module_needed(self.form.detected, self.form.output_format) and self.runtime:
            self.form.module = self.runtime
            self.auto_module = True
        elif self.auto_module:
            self.form.module = ""
            self.auto_module = False

    def refresh(self):
        f = self.form
        busy = self.worker is not None
        issues = [] if busy else problems(f)
        by_field = {p.field: p for p in issues}
        lz4 = f.output_format == LZ4

        self.refresh_game_row()
        self.dest_row.set_subtitle(by_field["dest"].detail if "dest" in by_field
                                   else (short_path(f.dest) or tr("Nenhuma pasta escolhida")))
        self.dest_row.set_tooltip_text(f.dest)
        self.source_row.set_subtitle(short_path(f.source) or tr("Nenhum jogo escolhido"))
        self.source_row.set_tooltip_text(f.source)

        self.format_caption.set_label(OUTPUT_HELP[f.output_format])
        level = COMPRESSION[f.output_format][level_index(f.output_format, self.level)]
        self.level_caption.set_label(level[2])

        if "module" in by_field:
            module_text = by_field["module"].detail
        elif f.module:
            name = Path(f.module).name
            module_text = tr('{0} · runtime do projeto').format(name) if f.module == self.runtime else name
        elif module_needed(f.detected, f.output_format):
            module_text = (tr("Obrigatório: runtime PackedStdio 0.4.2.1.") if lz4
                           else tr("Obrigatório: o jogo usa APR Emu."))
        else:
            module_text = tr("Não necessário para este jogo") if f.detected == "unknown" else tr("Opcional")
        self.module_row.set_subtitle(module_text)
        self.module_row.set_tooltip_text(f.module)
        self.use_runtime_button.set_visible(bool(self.runtime))

        self.profile_group.set_visible(lz4)
        if "profile" in by_field:
            profile_text = by_field["profile"].detail
        else:
            profile_text = Path(f.profile).name if f.profile else tr("Automático (padrão de referência)")
        self.profile_row.set_subtitle(profile_text)

        if issues:
            self.hint.set_label(issues[0].short)
        else:
            self.hint.set_label(tr("Tudo pronto. A conversão não altera o original."))
        self.start_button.set_sensitive(not issues and not busy)
        self.start_button.set_tooltip_text("\n".join(p.detail for p in issues) or tr("Iniciar a conversão"))

    def refresh_game_row(self):
        f = self.form
        self.detect_spinner.set_visible(f.probing)
        self.detect_icon.set_visible(not f.probing)
        if not f.source:
            self.detect_row.set_title(tr("Detecção"))
            self.detect_row.set_subtitle(tr("Escolha um jogo para começar"))
            self.detect_icon.set_from_icon_name("applications-games-symbolic")
        elif f.probing:
            self.detect_row.set_title(tr("Verificando o jogo…"))
            self.detect_row.set_subtitle(tr("Lendo o código do jogo e procurando sinais de APR Emu."))
        elif f.probe_error:
            self.detect_row.set_title(tr("Não foi possível ler este jogo"))
            self.detect_row.set_subtitle(f.probe_error)
            self.detect_icon.set_from_icon_name("dialog-error-symbolic")
        elif self.game:
            code, name, text, kind = self.game
            self.detect_row.set_title(f"{code} · {name}")
            self.detect_row.set_subtitle(text)
            tone = PROBE_TONE[kind]
            self.detect_icon.set_from_icon_name(
                "object-select-symbolic" if tone == "success" else "dialog-warning-symbolic")
        else:
            self.detect_row.set_title(tr("Jogo selecionado"))
            self.detect_row.set_subtitle(tr("Aguardando a verificação"))

    def restore(self):
        s = load_settings()
        f = self.form
        f.source = str(s.get("source") or "")
        if f.source and not Path(f.source).exists():
            f.source = ""
        f.dest = str(s.get("dest") or "")
        if f.dest and not Path(f.dest).is_dir():
            f.dest = ""
        f.output_format = s.get("format") if s.get("format") in FORMATS else FFPFSC
        f.profile = str(s.get("profile") or "")
        try:
            self.level = int(s.get("level") or default_level(f.output_format))
        except (TypeError, ValueError):
            self.level = default_level(f.output_format)
        if self.level not in [v for _l, v, _h in COMPRESSION[f.output_format]]:
            self.level = default_level(f.output_format)
        try:
            workers = int(s.get("workers") or default_workers())
        except (TypeError, ValueError):
            workers = default_workers()
        module = str(s.get("module") or "")
        if module and module != self.runtime and Path(module).is_file():
            f.module = module
            self.manual_module = True
        with self.quiet():
            self.format_toggle.set_active_name(f.output_format)
            self.threads_row.set_value(min(16, max(1, workers)))
            self.populate_levels()
        self.update_auto_module()

    def save(self):
        f = self.form
        values = {"source": f.source, "dest": f.dest, "format": f.output_format,
                  "workers": int(self.threads_row.get_value()), "level": self.level,
                  "profile": f.profile}
        if self.manual_module:
            values["module"] = f.module
        save_settings(values)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast.new(text))

    # ----- detecção (APR Emu) -----------------------------------------

    def schedule_probe(self):
        self.cancel_probe()
        f = self.form
        f.detected = None
        self.game = None
        f.probe_error = ""
        f.probing = bool(f.source) and Path(f.source).exists()
        self.update_auto_module()
        if f.probing:
            self.probe_timer = GLib.timeout_add(PROBE_DELAY_MS, self.run_probe)
        self.refresh()

    def run_probe(self):
        self.probe_timer = 0
        source = self.form.source
        generation = self.probe_generation
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE |
                                              Gio.SubprocessFlags.STDERR_SILENCE)
        launcher.set_cwd(str(ROOT))
        try:
            proc = launcher.spawnv([sys.executable, "-m", "sapobox.converter.probe", source])
        except GLib.Error as exc:
            self.apply_probe(json.dumps({"error": exc.message}))
            return False
        self.probe = proc
        proc.communicate_utf8_async(None, None,
                                    lambda p, r: self.probe_done(p, r, generation, source))
        return False

    def probe_done(self, proc, result, generation, source):
        try:
            _ok, stdout, _stderr = proc.communicate_utf8_finish(result)
        except GLib.Error:
            stdout = ""
        if self.probe is proc:
            self.probe = None
        if generation != self.probe_generation or source != self.form.source:
            return  # resultado de uma escolha antiga
        self.apply_probe(stdout or "")

    def apply_probe(self, raw):
        f = self.form
        f.probing = False
        try:
            data = json.loads(raw)
        except ValueError:
            data = {"error": tr("A detecção não devolveu um resultado válido.")}
        if "error" in data:
            f.probe_error = data["error"]
            f.detected = None
            self.game = None
        else:
            f.probe_error = ""
            f.detected = data["kind"]
            self.game = (data["code"], data["name"], data["text"], data["kind"])
        self.update_auto_module()
        self.refresh()

    def cancel_probe(self):
        self.probe_generation += 1
        if self.probe_timer:
            GLib.source_remove(self.probe_timer)
            self.probe_timer = 0
        if self.probe is not None:
            with contextlib.suppress(GLib.Error):
                self.probe.send_signal(signal.SIGKILL)
            self.probe = None

    # ----- conversão ---------------------------------------------------

    def start(self):
        if self.worker is not None or problems(self.form):
            return
        f = self.form
        self.save()
        self.cancel_probe()
        self.log_lines = []
        command = ["-m", "sapobox.converter.worker", f.source, f.dest,
                   "--workers", str(int(self.threads_row.get_value())),
                   "--level", str(self.level), "--format", f.output_format]
        if f.module:
            command += ["--apr-module", f.module]
        if f.output_format == LZ4 and f.profile:
            command += ["--profile", f.profile]
        self.log("python3 " + " ".join(shlex.quote(a) for a in command))

        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE |
                                              Gio.SubprocessFlags.STDERR_PIPE)
        launcher.set_cwd(str(ROOT))
        try:
            self.worker = launcher.spawnv([sys.executable, *command])
        except GLib.Error as exc:
            self.worker = None
            self.result = ("error", tr('Não foi possível iniciar o conversor: {0}').format(exc.message))
            self.finish_run()
            return
        self.result = None
        self.run_source = f.source
        self.cancelling = False
        self.exit_code = None
        self.pending = {"out", "err", "exit"}
        self.progress.reset()
        self.run_bar.set_fraction(0)
        self.run_page.set_title(self.game[1] if self.game else (Path(f.source).name or "Convertendo…"))
        self.run_page.set_description("Preparando…")
        self.run_stats.set_label("")
        self.cancel_button.set_sensitive(True)
        self.read_lines(self.worker.get_stdout_pipe(), "out", self.on_stdout_line)
        self.read_lines(self.worker.get_stderr_pipe(), "err", self.log)
        self.worker.wait_async(None, self.on_worker_exit)
        self.start_pulse()
        self.stack.set_visible_child_name("running")
        self.refresh()

    def read_lines(self, pipe, key, handler):
        self.read_next(Gio.DataInputStream.new(pipe), key, handler)

    def read_next(self, stream, key, handler):
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None,
                               lambda s, r: self.line_ready(s, r, key, handler))

    def line_ready(self, stream, result, key, handler):
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:  # fim do stream
            self.pending.discard(key)
            self.maybe_finish()
            return
        handler(line)
        self.read_next(stream, key, handler)

    def on_stdout_line(self, line):
        try:
            event = json.loads(line)
        except ValueError:
            self.log(line)
            return
        kind = event.get("event")
        if kind == "status":
            text = event.get("text", "")
            self.log(text)
            if self.form.output_format != LZ4:  # no LZ4 a fase vem do progresso
                self.run_page.set_description(text)
        elif kind == "progress":
            self.on_progress(event)
        elif kind == "done":
            self.result = ("done", event.get("path", ""))
        elif kind == "error":
            text = event.get("text", tr("Falha desconhecida"))
            self.result = ("cancelled" if text == CANCELLED else "error", text)
            self.log(f"{tr('Cancelado') if text == CANCELLED else tr('Erro')}: {text}")

    def on_progress(self, event):
        fraction, text = self.progress.update(event)
        phase = event.get("phase")
        if self.form.output_format == LZ4 and phase:
            self.run_page.set_description(PHASE_TITLES.get(phase, "Convertendo…"))
        self.run_stats.set_label(text)
        if fraction is None:
            self.start_pulse()
        else:
            self.stop_pulse()
            self.run_bar.set_fraction(fraction)

    def on_worker_exit(self, proc, result):
        with contextlib.suppress(GLib.Error):
            proc.wait_finish(result)
        self.exit_code = proc.get_exit_status()
        self.pending.discard("exit")
        self.maybe_finish()

    def maybe_finish(self):
        if self.pending:
            return
        self.worker = None
        self.stop_timers()
        if self.result is None:
            if self.cancelling:
                self.result = ("cancelled", CANCELLED)
            else:
                self.result = ("error", tr('O conversor parou inesperadamente (código {0}). A origem permanece intacta.').format(self.exit_code))
        self.finish_run()

    def finish_run(self):
        kind, text = self.result
        self.current_output = text if kind == "done" else ""
        self.open_button.set_visible(kind == "done")
        if kind == "done":
            icon, title, description = ("object-select-symbolic", tr("Conversão concluída"),
                                        tr("Salvo em:\n{0}").format(text))
            # O .vhash (FF-PFSC) conta como destino; no LZ4 ele não existe e soma zero.
            saving = saving_text(occupied_bytes(self.run_source),
                                 occupied_bytes(text) + occupied_bytes(text + ".vhash"))
            if saving:
                description += f"\n\n{saving}"
        elif kind == "cancelled":
            icon, title, description = ("process-stop-symbolic", tr("Conversão cancelada"),
                                        tr("Nada ficou no destino e a origem não foi alterada."))
        else:
            icon, title, description = ("dialog-error-symbolic", tr("A conversão não foi concluída"),
                                        text)
        self.result_page.set_icon_name(icon)
        self.result_page.set_title(title)
        self.result_page.set_description(description)
        self.stack.set_visible_child_name("result")
        self.refresh()
        if self.closing:
            self.close()

    def cancel_run(self):
        if self.worker is None or self.cancelling:
            return
        self.cancelling = True
        self.cancel_button.set_sensitive(False)
        self.run_page.set_description(tr("Cancelando… a origem não será alterada."))
        self.log(tr("Cancelamento solicitado."))
        with contextlib.suppress(GLib.Error):
            self.worker.send_signal(signal.SIGTERM)
        self.cancel_timer = GLib.timeout_add_seconds(CANCEL_GRACE_S, self.force_cancel)

    def force_cancel(self):
        self.cancel_timer = 0
        if self.worker is not None:
            self.log(tr("O conversor não respondeu ao cancelamento; encerrando à força."))
            with contextlib.suppress(GLib.Error):
                self.worker.send_signal(signal.SIGKILL)
        return False

    def start_pulse(self):
        if not self.pulse_timer:
            self.pulse_timer = GLib.timeout_add(120, self.pulse)

    def stop_pulse(self):
        if self.pulse_timer:
            GLib.source_remove(self.pulse_timer)
            self.pulse_timer = 0

    def pulse(self):
        self.run_bar.pulse()
        return True

    def stop_timers(self):
        self.stop_pulse()
        if self.cancel_timer:
            GLib.source_remove(self.cancel_timer)
            self.cancel_timer = 0

    def open_destination(self):
        if not self.current_output:
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(Path(self.current_output).parent)))
        launcher.launch(self, None, self.opened)

    def opened(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            self.toast(tr("Não foi possível abrir a pasta de saída."))

    def new_conversion(self):
        self.result = None
        self.stack.set_visible_child_name("setup")
        self.refresh()

    def log(self, text):
        self.log_lines.append(text)

    def show_log(self):
        text_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        text_view.get_buffer().set_text("\n".join(self.log_lines) or tr("Nenhuma atividade ainda."), -1)
        scroller = Gtk.ScrolledWindow(child=text_view, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle.new(tr("Registro técnico"), ""))
        view.add_top_bar(header)
        view.set_content(scroller)
        dialog = Adw.Dialog(title=tr("Registro técnico"), content_width=640, content_height=480)
        dialog.set_child(view)
        dialog.present(self)


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("startup", self.on_startup)
        self.connect("activate", self.on_activate)

    def on_startup(self, _app):
        icons = ROOT / "packaging" / "icons"
        display = Gdk.Display.get_default()
        if icons.is_dir() and display is not None:
            Gtk.IconTheme.get_for_display(display).add_search_path(str(icons))
        Gtk.Window.set_default_icon_name(APP_ID)
        for name, handler in (("about", self.show_about), ("quit", lambda: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            self.add_action(action)
        self.set_accels_for_action("win.choose-folder", ["<Primary>o"])
        self.set_accels_for_action("win.choose-image", ["<Primary><Shift>o"])
        self.set_accels_for_action("win.choose-dest", ["<Primary>d"])
        self.set_accels_for_action("win.start", ["<Primary>Return"])
        self.set_accels_for_action("win.cancel", ["<Primary>period"])
        self.set_accels_for_action("win.log", ["<Primary>l"])
        self.set_accels_for_action("win.new", ["<Primary>n"])
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def on_activate(self, _app):
        window = self.props.active_window or Window(self)
        window.present()

    def show_about(self):
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID,
                                version=__version__,
                                comments=tr("Converte pasta de jogo ou imagem .exfat para "
                                         "FF-PFSC ou LZ4 Asset Packs. O original nunca é alterado."))
        about.present(self.props.active_window)


def main(argv=None):
    GLib.set_prgname("sapobox-converter")
    GLib.set_application_name(APP_NAME)
    return Application().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
