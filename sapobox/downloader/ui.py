"""Interface GTK 4 + libadwaita do Downloader."""

from __future__ import annotations

import os
from collections import Counter

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .core import (  # noqa: E402
    COMPLETED,
    DOWNLOADING,
    ERROR,
    MAX_CONCURRENT_LIMIT,
    MAX_CONNECTIONS,
    PAUSED,
    QUEUED,
    Download,
    DownloadManager,
    Settings,
    guess_name,
    load_settings,
    save_settings,
)
from ..i18n import tr  # noqa: E402

APP_ID = "org.sapobox.Downloader"
APP_NAME = "SapoBox Downloader"

STATUS_TEXT = {
    QUEUED: tr("Na fila"),
    DOWNLOADING: tr("Baixando"),
    PAUSED: tr("Pausado"),
    COMPLETED: tr("Concluído"),
    ERROR: tr("Erro"),
}
STATUS_CSS = {
    QUEUED: "dim-label",
    DOWNLOADING: "accent",
    PAUSED: "warning",
    COMPLETED: "success",
    ERROR: "error",
}
TOGGLE_BUTTON = {  # rótulo e ícone do botão principal de cada linha
    QUEUED: (tr("Pausar"), "media-playback-pause-symbolic"),
    DOWNLOADING: (tr("Pausar"), "media-playback-pause-symbolic"),
    PAUSED: (tr("Retomar"), "media-playback-start-symbolic"),
    ERROR: (tr("Tentar novamente"), "view-refresh-symbolic"),
}
REFRESH_MS = 500


def format_size(n: float) -> str:
    return GLib.format_size(int(n))


def format_eta_curto(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


COLUNAS = (tr("Nome"), tr("Tamanho"), tr("Progresso"), tr("Velocidade"), tr("Restante"), tr("Status"), "")
COLUNAS_NUMERICAS = {1, 3, 4}  # alinhadas à direita, como em planilha


def _celula(widget: Gtk.Widget) -> Gtk.Widget:
    widget.set_margin_start(8)
    widget.set_margin_end(8)
    widget.set_margin_top(6)
    widget.set_margin_bottom(6)
    widget.set_valign(Gtk.Align.CENTER)
    return widget


def _rotulo_fixo(largura: int, xalign: float, css_classes: list[str]) -> Gtk.Label:
    """Rótulo com largura fixa: o texto corta com '…' em vez de empurrar a coluna."""
    return Gtk.Label(xalign=xalign, width_chars=largura, max_width_chars=largura,
                     ellipsize=Pango.EllipsizeMode.END, css_classes=css_classes)


class DownloadRow:
    """Uma linha da tabela (um download). Os widgets ficam na Gtk.Grid compartilhada."""

    def __init__(self, download_id: str, window: MainWindow):
        self.download_id = download_id
        self._dest_dir = ""
        self._status = None
        self._erro = None

        self.nome = _celula(Gtk.Label(xalign=0, hexpand=True, width_chars=28, max_width_chars=80,
                                      ellipsize=Pango.EllipsizeMode.MIDDLE))
        # Largura fixa em caracteres: o texto muda a cada atualização, e sem isso as colunas se redimensionam e balançam.
        self.tamanho = _celula(_rotulo_fixo(22, xalign=1, css_classes=["numeric"]))
        self.barra = _celula(Gtk.ProgressBar(show_text=True, width_request=150))
        self.velocidade = _celula(_rotulo_fixo(11, xalign=1, css_classes=["numeric"]))
        self.restante = _celula(_rotulo_fixo(9, xalign=1, css_classes=["numeric"]))
        self.status = _celula(_rotulo_fixo(12, xalign=0, css_classes=["caption-heading"]))

        self._alternar = Gtk.Button(css_classes=["flat"])
        self._alternar.connect("clicked", lambda _b: window.toggle_download(self.download_id))
        pasta = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Abrir a pasta de destino"),
                           css_classes=["flat"])
        pasta.connect("clicked", lambda _b: window.open_folder(self._dest_dir))
        remover = Gtk.Button(icon_name="user-trash-symbolic",
                             tooltip_text=tr("Remover da lista (apaga o arquivo parcial)"), css_classes=["flat"])
        remover.connect("clicked", lambda _b: window.remove_download(self.download_id))
        acoes = Gtk.Box(spacing=2)
        for botao in (self._alternar, pasta, remover):
            acoes.append(botao)
        self.acoes = _celula(acoes)

        self.celulas = [self.nome, self.tamanho, self.barra, self.velocidade,
                        self.restante, self.status, self.acoes]

    def update(self, d: Download) -> None:
        self._dest_dir = d.dest_dir
        nome = d.filename or guess_name(d.url) or d.url
        self.nome.set_label(nome)
        self.nome.set_tooltip_text(f"{nome}\n{d.url}")

        self.tamanho.set_label(self._tamanho(d))
        self._atualizar_barra(d)
        ativo = d.status == DOWNLOADING and d.speed > 0
        self.velocidade.set_label(f"{format_size(d.speed)}/s" if ativo else "—")
        self.restante.set_label(format_eta_curto((d.total - d.downloaded) / d.speed) if ativo and d.total else "—")

        if d.status != self._status or d.error != self._erro:
            self._status, self._erro = d.status, d.error
            self._definir_status(d)

    @staticmethod
    def _tamanho(d: Download) -> str:
        feito = format_size(d.downloaded)
        if d.total:
            return f"{feito} / {format_size(d.total)}"
        return feito

    def _atualizar_barra(self, d: Download) -> None:
        if d.total:
            fracao = min(1.0, d.downloaded / d.total)
            self.barra.set_fraction(fracao)
            self.barra.set_text(f"{fracao * 100:.0f}%")
        elif d.status == COMPLETED:
            self.barra.set_fraction(1.0)
            self.barra.set_text("100%")
        elif d.status == DOWNLOADING:
            self.barra.pulse()  # tamanho desconhecido
            self.barra.set_text("")
        else:
            self.barra.set_fraction(0.0)
            self.barra.set_text("")

    def _definir_status(self, d: Download) -> None:
        self.status.set_label(STATUS_TEXT[d.status])
        for css in STATUS_CSS.values():
            self.status.remove_css_class(css)
        self.status.add_css_class(STATUS_CSS[d.status])
        self.status.set_tooltip_text(f"Falhou: {d.error}" if d.status == ERROR and d.error else None)

        label, icone = TOGGLE_BUTTON.get(d.status, ("", ""))
        # Esconder o botão mudaria a largura da coluna de ações; então ele só fica transparente e desativado.
        concluido = d.status == COMPLETED
        self._alternar.set_opacity(0.0 if concluido else 1.0)
        self._alternar.set_sensitive(not concluido)
        self._alternar.set_icon_name(icone)
        self._alternar.set_tooltip_text(label)


class AddDialog(Adw.Dialog):
    def __init__(self, window: MainWindow, download_dir: str, on_submit, on_folder_chosen):
        super().__init__(title=tr("Adicionar downloads"), content_width=580, content_height=540)
        self._window = window
        self._download_dir = download_dir
        self._on_submit = on_submit
        self._on_folder_chosen = on_folder_chosen

        cancel = Gtk.Button(label=tr("Cancelar"))
        cancel.connect("clicked", lambda _b: self.close())
        self._add_button = Gtk.Button(label=tr("Adicionar"), css_classes=["suggested-action"], sensitive=False)
        self._add_button.connect("clicked", self._submit)
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(self._add_button)

        self._folder_row = Adw.ActionRow(title=tr("Pasta de destino"), subtitle=download_dir)
        choose = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=tr("Escolher outra pasta"), css_classes=["flat"])
        choose.connect("clicked", lambda _b: window.choose_folder(self._download_dir, self._set_folder))
        self._folder_row.add_suffix(choose)
        self._folder_row.set_activatable_widget(choose)
        folders = Adw.PreferencesGroup()
        folders.add(self._folder_row)

        label = Gtk.Label(label=tr("Links (um por linha)"), xalign=0, css_classes=["heading"])
        self._text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True,
                                  top_margin=10, bottom_margin=10, left_margin=10, right_margin=10)
        self._text.get_buffer().connect("changed", self._on_text_changed)
        text_scroll = Gtk.ScrolledWindow(child=self._text, vexpand=True, min_content_height=180)
        frame = Gtk.Frame(child=text_scroll)

        paste = Gtk.Button(child=Adw.ButtonContent(label=tr("Colar da área de transferência"),
                                                   icon_name="edit-paste-symbolic"))
        paste.connect("clicked", self._paste_from_clipboard)
        self._count = Gtk.Label(xalign=1, hexpand=True, css_classes=["dim-label", "caption"])
        footer = Gtk.Box(spacing=12)
        footer.append(paste)
        footer.append(self._count)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        for widget in (folders, label, frame, footer):
            page.append(widget)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)
        self._on_text_changed()

    def _links(self) -> list[str]:
        buf = self._text.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _on_text_changed(self, *_args) -> None:
        count = len(self._links())
        self._count.set_label((tr("{0} link") if count == 1 else tr("{0} links")).format(count))
        self._add_button.set_sensitive(count > 0)

    def _set_folder(self, path: str) -> None:
        self._download_dir = path
        self._folder_row.set_subtitle(path)
        self._on_folder_chosen(path)  # vira a pasta padrão, também na próxima abertura do app

    def _paste_from_clipboard(self, _button) -> None:
        Gdk.Display.get_default().get_clipboard().read_text_async(None, self._on_clipboard_read)

    def _on_clipboard_read(self, clipboard, result) -> None:
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        if not text or not text.strip():
            return
        buf = self._text.get_buffer()
        end = buf.get_end_iter()
        if buf.get_char_count() and not buf.get_text(buf.get_start_iter(), end, False).endswith("\n"):
            buf.insert(end, "\n")
        buf.insert(buf.get_end_iter(), text.strip() + "\n")

    def _submit(self, _button) -> None:
        self._on_submit(self._links(), self._download_dir)
        self.close()


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, window: MainWindow, app: DownloaderApp):
        super().__init__(transient_for=window, modal=True, search_enabled=False, title=tr("Preferências"))
        self._window = window
        self._app = app

        group = Adw.PreferencesGroup(title="Downloads")
        page = Adw.PreferencesPage()
        page.add(group)
        self.add(page)

        self._dir_row = Adw.ActionRow(title=tr("Pasta de destino padrão"), subtitle=app.settings.download_dir)
        choose = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=tr("Escolher pasta"), css_classes=["flat"])
        choose.connect("clicked", self._on_choose_folder)
        self._dir_row.add_suffix(choose)
        self._dir_row.set_activatable_widget(choose)
        group.add(self._dir_row)

        self._max_row = Adw.SpinRow.new_with_range(1, MAX_CONCURRENT_LIMIT, 1)
        self._max_row.set_title(tr("Downloads simultâneos"))
        self._max_row.set_subtitle(tr("Quantos arquivos baixam ao mesmo tempo"))
        self._max_row.set_value(app.settings.max_concurrent)
        self._max_row.connect("notify::value", self._on_max_changed)
        group.add(self._max_row)

        self._conn_row = Adw.SpinRow.new_with_range(1, MAX_CONNECTIONS, 1)
        self._conn_row.set_title(tr("Partes por download"))
        self._conn_row.set_subtitle(tr("Divide cada arquivo em partes baixadas juntas, quando o servidor permite"))
        self._conn_row.set_value(app.settings.connections)
        self._conn_row.connect("notify::value", lambda row, _p: app.set_connections(int(row.get_value())))
        group.add(self._conn_row)

    def _on_choose_folder(self, _button) -> None:
        self._window.choose_folder(self._app.settings.download_dir, self._set_download_dir)

    def _set_download_dir(self, path: str) -> None:
        self._app.set_download_dir(path)
        self._dir_row.set_subtitle(path)

    def _on_max_changed(self, row, _pspec) -> None:
        self._app.set_max_concurrent(int(row.get_value()))


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: DownloaderApp):
        super().__init__(application=app, title=APP_NAME, default_width=1100, default_height=660)
        self.set_size_request(1020, 420)  # abaixo disso a tabela cortaria colunas
        self._app = app
        self._manager = app.manager
        self._rows: dict[str, DownloadRow] = {}
        self._ids_mostrados: list[str] = []

        self._title = Adw.WindowTitle(title=APP_NAME, subtitle="")
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)

        add_button = Gtk.Button(child=Adw.ButtonContent(label=tr("Adicionar"), icon_name="list-add-symbolic"),
                                tooltip_text=tr("Adicionar links (Ctrl+N)"), css_classes=["suggested-action"])
        add_button.set_action_name("app.add")
        header.pack_start(add_button)

        menu = Gio.Menu()
        menu.append(tr("Limpar concluídos"), "app.clear-completed")
        menu.append(tr("Preferências"), "app.preferences")
        menu.append(tr("Sair"), "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Menu", menu_model=menu))

        resume_all = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text=tr("Retomar todos"))
        resume_all.connect("clicked", lambda _b: self._run(self._manager.resume_all))
        pause_all = Gtk.Button(icon_name="media-playback-pause-symbolic", tooltip_text=tr("Pausar todos"))
        pause_all.connect("clicked", lambda _b: self._run(self._manager.pause_all))
        header.pack_end(resume_all)
        header.pack_end(pause_all)

        self._empty = Adw.StatusPage(icon_name="folder-download-symbolic", title=tr("Nenhum download"),
                                     description=tr("Clique em Adicionar e cole um ou mais links."))
        self._tabela = Gtk.Grid(css_classes=["card"], hexpand=True, valign=Gtk.Align.START,
                                margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vexpand=True, hexpand=True,
                                      child=self._tabela)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._empty, "empty")
        self._stack.add_named(scroller, "list")
        self._toasts = Adw.ToastOverlay(child=self._stack)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._toasts)
        self.set_content(toolbar)

        self._refresh()
        GLib.timeout_add(REFRESH_MS, self._on_tick)

    # ----- ações -----------------------------------------------------------

    def open_add_dialog(self) -> None:
        AddDialog(self, self._app.settings.download_dir, self._add_links, self._app.set_download_dir).present(self)

    def open_preferences(self) -> None:
        PreferencesWindow(self, self._app).present()

    def clear_completed(self) -> None:
        self._run(self._manager.clear_completed)

    def toggle_download(self, download_id: str) -> None:
        self._run(lambda: self._manager.toggle(download_id))

    def remove_download(self, download_id: str) -> None:
        self._run(lambda: self._manager.remove(download_id))

    def open_folder(self, path: str) -> None:
        if not os.path.isdir(path):
            self._toast(tr("A pasta de destino não existe mais"))
            return
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error as exc:
            self._toast(tr('Não foi possível abrir a pasta: {0}').format(exc.message))

    def choose_folder(self, initial: str, callback) -> None:
        dialog = Gtk.FileDialog(title=tr("Escolher pasta"), modal=True)
        if os.path.isdir(initial):
            dialog.set_initial_folder(Gio.File.new_for_path(initial))
        dialog.select_folder(self, None, lambda d, res: self._folder_chosen(d, res, callback))

    @staticmethod
    def _folder_chosen(dialog, result, callback) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:  # cancelado
            return
        callback(folder.get_path())

    # ----- internos --------------------------------------------------------

    def _add_links(self, lines: list[str], dest_dir: str) -> None:
        result = self._manager.add_urls(lines, dest_dir)
        parts = []
        if result.added:
            parts.append((tr("{0} adicionado") if result.added == 1 else tr("{0} adicionados")).format(result.added))
        if result.duplicates:
            parts.append(tr('{0} já estava na lista').format(result.duplicates))
        if result.invalid:
            parts.append(tr('{0} ignorado(s): não são links http/https').format(result.invalid))
        self._toast(" · ".join(parts) or tr("Nenhum link encontrado"))
        self._refresh()

    def _run(self, action) -> None:
        action()
        self._refresh()

    def _toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _refresh(self) -> None:
        downloads = self._manager.snapshot()
        ids = [d.id for d in downloads]

        for download_id in [i for i in self._rows if i not in ids]:
            del self._rows[download_id]
        for d in downloads:
            if d.id not in self._rows:
                self._rows[d.id] = DownloadRow(d.id, self)
        if ids != self._ids_mostrados:
            self._montar_tabela(ids)
        for d in downloads:
            self._rows[d.id].update(d)

        self._stack.set_visible_child_name("list" if downloads else "empty")
        self._title.set_subtitle(self._summary(downloads))

    def _montar_tabela(self, ids: list[str]) -> None:
        """Refaz a grade só quando a lista muda (adição ou remoção). Atualizações são feitas nas próprias células."""
        tabela = self._tabela
        while (filho := tabela.get_first_child()) is not None:
            tabela.remove(filho)
        for coluna, titulo in enumerate(COLUNAS):
            rotulo = Gtk.Label(label=titulo, xalign=1 if coluna in COLUNAS_NUMERICAS else 0,
                               css_classes=["dim-label", "caption-heading"])
            tabela.attach(_celula(rotulo), coluna, 0, 1, 1)
        for indice, download_id in enumerate(ids):
            linha = 2 * indice + 1
            tabela.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, linha, len(COLUNAS), 1)
            for coluna, widget in enumerate(self._rows[download_id].celulas):
                tabela.attach(widget, coluna, linha + 1, 1, 1)
        self._ids_mostrados = list(ids)

    @staticmethod
    def _summary(downloads: list[Download]) -> str:
        counts = Counter(d.status for d in downloads)
        labels = (
            (DOWNLOADING, tr("baixando")), (QUEUED, tr("na fila")),
            (PAUSED, tr("pausados")), (ERROR, tr("com erro")), (COMPLETED, tr("concluídos")),
        )
        return " · ".join(f"{counts[status]} {label}" for status, label in labels if counts[status])

    def _on_tick(self) -> bool:
        self._refresh()
        return GLib.SOURCE_CONTINUE


class DownloaderApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)
        self.settings: Settings = load_settings()
        self.manager = DownloadManager(self.settings, on_completed=self._on_download_completed)
        self.window: MainWindow | None = None

        actions = (
            ("add", lambda *_: self.window.open_add_dialog()),
            ("preferences", lambda *_: self.window.open_preferences()),
            ("clear-completed", lambda *_: self.window.clear_completed()),
            ("quit", lambda *_: self.quit()),
        )
        for name, handler in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.set_accels_for_action("app.add", ["<primary>n"])
        self.set_accels_for_action("app.preferences", ["<primary>comma"])
        self.set_accels_for_action("app.quit", ["<primary>q"])

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()

    def do_shutdown(self) -> None:
        self.manager.shutdown()
        save_settings(self.settings)
        Adw.Application.do_shutdown(self)

    def set_download_dir(self, path: str) -> None:
        self.settings.download_dir = path
        save_settings(self.settings)

    def set_connections(self, value: int) -> None:
        self.manager.set_connections(value)
        save_settings(self.settings)

    def set_max_concurrent(self, value: int) -> None:
        self.manager.set_max_concurrent(value)
        save_settings(self.settings)

    def _on_download_completed(self, filename: str) -> None:
        # chamado da thread do download: devolve o trabalho para a thread principal
        GLib.idle_add(self._notify_completed, filename)

    def _notify_completed(self, filename: str) -> bool:
        notification = Gio.Notification.new(tr("Download concluído"))
        notification.set_body(filename)
        self.send_notification(None, notification)
        return GLib.SOURCE_REMOVE
