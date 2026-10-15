"""Janela do Extrator: GTK 4 + libadwaita, a mesma base do Conversor V2.

Uma janela só, com três telas (como no Conversor): "configurar", "executando" e
"resultado". Ao clicar em Extrair, a janela troca para a tela de progresso.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import limpeza  # noqa: E402
from .arquivos import ConjuntoCompactado, ErroArquivo, identificar  # noqa: E402
from .progresso import Medidor, formatar_tamanho, linha_progresso  # noqa: E402
from .sevenzip import (  # noqa: E402
    Extracao,
    Resultado,
    Verificacao,
    encontrar_7z,
    montar_comando_extracao,
    tem_decodificador_rar,
    verificar,
)
from ..i18n import tr  # noqa: E402

APP_ID = "org.sapobox.Extrator"
NOME = "Extrator"

# Ordem dos itens do seletor "Se o arquivo já existir" (a chave vai para sevenzip.MODOS_EXISTENTE).
OPCOES_EXISTENTE = (
    ("pular", tr("Pular os que já existem")),
    ("sobrescrever", tr("Sobrescrever")),
    ("renomear", tr("Manter os dois (renomear)")),
)

# Sufixos do filtro da janela de abrir arquivo. "001" cobre os .7z.001, .zip.001...
SUFIXOS_FILTRO = ("7z", "zip", "rar", "001", "z01", "r00")

# Classe CSS do texto de estado, por resultado.
CLASSE_POR_ESTADO = {"ok": "success", "aviso": "warning"}

# Ícone e título da tela de resultado, por resultado.
ICONE_E_TITULO_RESULTADO = {
    "ok": ("object-select-symbolic", tr("Extração concluída")),
    "aviso": ("dialog-warning-symbolic", tr("Concluída com avisos")),
    "cancelado": ("process-stop-symbolic", tr("Extração cancelada")),
}

# Tamanho fixo: a janela não muda quando a mensagem de estado cresce nem quando a tela muda.
# O conteúdo que não cabe rola dentro dela.
LARGURA_JANELA = 600
ALTURA_JANELA = 760


def _pasta_existente(caminho: Path) -> Path:
    """O caminho ou o primeiro pai que existe (o 7z cria a pasta de destino depois)."""
    while not caminho.exists() and caminho != caminho.parent:
        caminho = caminho.parent
    return caminho


def _eh_rar(conjunto: ConjuntoCompactado) -> bool:
    return any(parte.name.lower().endswith(".rar") for parte in conjunto.partes)


def _comando_para_registro(comando: list[str]) -> str:
    """Comando para o registro, com a senha escondida."""
    return shlex.join("-p********" if item.startswith("-p") else item for item in comando)


class JanelaExtrator(Adw.ApplicationWindow):
    def __init__(self, aplicativo: Adw.Application) -> None:
        super().__init__(
            application=aplicativo,
            title=NOME,
            default_width=LARGURA_JANELA,
            default_height=ALTURA_JANELA,
        )
        self.set_resizable(False)
        self._programa = encontrar_7z()
        self._rar_disponivel = self._programa is not None and tem_decodificador_rar(self._programa)
        self._conjunto: ConjuntoCompactado | None = None
        self._tamanho_compactado = 0
        self._verificacao: Verificacao | None = None
        self._destino: Path | None = None
        self._pasta_final: Path | None = None
        self._extracao: Extracao | None = None
        self._acoes: dict[str, Gio.SimpleAction] = {}
        self._medidor: Medidor | None = None
        self._percentual_7z = 0
        self._escrito = 0
        self._pedir_senha_ao_voltar = False
        self._buffer_registro = Gtk.TextBuffer()
        self._texto_registro: Gtk.TextView | None = None
        self._janela_registro: Adw.Window | None = None

        self._montar_interface()
        self._registrar_acoes()
        self._habilitar_arrastar_soltar()
        self.connect("close-request", self._ao_fechar)
        self._registrar(tr('7-Zip em uso: {0} (decodificador RAR: {1})').format(self._programa or tr('nenhum encontrado'), tr('sim') if self._rar_disponivel else tr('não')))
        self._atualizar_tela()

    # ------------------------------------------------------------------ interface

    def _montar_interface(self) -> None:
        self._titulo = Adw.WindowTitle.new(tr("Nova extração"), "")
        cabecalho = Adw.HeaderBar()
        cabecalho.set_title_widget(self._titulo)
        cabecalho.pack_end(
            Gtk.Button(
                icon_name="utilities-terminal-symbolic",
                tooltip_text=tr("Registro técnico (Ctrl+L)"),
                action_name="win.registro",
            )
        )

        self._pilha = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            hhomogeneous=False,
            vhomogeneous=False,
        )
        self._pilha.add_named(self._montar_configuracao(), "configurar")
        self._pilha.add_named(self._montar_executando(), "executando")
        self._pilha.add_named(self._montar_resultado(), "resultado")

        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecalho)
        vista.set_content(self._pilha)
        self.set_content(vista)

    def _montar_configuracao(self) -> Gtk.Widget:
        # Arquivo
        grupo_arquivo = Adw.PreferencesGroup(title=tr("Arquivo"))
        self._linha_arquivo = Adw.ActionRow(title=tr("Arquivo compactado"))
        self._linha_arquivo.set_subtitle_lines(0)
        self._linha_arquivo.add_suffix(
            self._botao(tr("Escolher…"), "win.abrir-arquivo", tr("Escolher arquivo (Ctrl+O)"))
        )
        self._linha_formato = Adw.ActionRow(title=tr("Formato"))
        self._linha_formato.set_subtitle_lines(0)
        self._linha_faltando = Adw.ActionRow(
            title=tr("Partes faltando"), icon_name="dialog-warning-symbolic"
        )
        self._linha_faltando.set_subtitle_lines(0)
        self._linha_faltando.set_visible(False)
        for linha in (self._linha_arquivo, self._linha_formato, self._linha_faltando):
            grupo_arquivo.add(linha)

        # Senha
        self._grupo_senha = Adw.PreferencesGroup(title=tr("Senha"))
        self._campo_senha = Adw.PasswordEntryRow(title=tr("Senha"))
        self._campo_senha.connect("changed", lambda *_: self._atualizar_tela())
        self._grupo_senha.add(self._campo_senha)

        # Destino
        grupo_destino = Adw.PreferencesGroup(title=tr("Destino"))
        self._linha_destino = Adw.ActionRow(title=tr("Pasta de destino"))
        self._linha_destino.set_subtitle_lines(0)
        self._linha_destino.add_suffix(
            self._botao(tr("Escolher…"), "win.escolher-destino", tr("Escolher pasta (Ctrl+D)"))
        )
        self._chave_pasta = Adw.SwitchRow(title=tr("Criar uma pasta com o nome do arquivo"), active=True)
        self._chave_pasta.connect("notify::active", lambda *_: self._atualizar_tela())
        self._seletor_existente = Adw.ComboRow(
            title=tr("Se o arquivo já existir"),
            model=Gtk.StringList.new([texto for _chave, texto in OPCOES_EXISTENTE]),
        )
        self._linha_previa = Adw.ActionRow(title=tr("Será extraído em"))
        self._linha_previa.set_subtitle_lines(0)
        for linha in (
            self._linha_destino,
            self._chave_pasta,
            self._seletor_existente,
            self._linha_previa,
        ):
            grupo_destino.add(linha)

        # Ações da tela de configuração
        self._estado = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self._estado.add_css_class("dim-label")
        self._botao_extrair = Gtk.Button(label=tr("Extrair"), halign=Gtk.Align.FILL)
        self._botao_extrair.add_css_class("suggested-action")
        self._botao_extrair.add_css_class("pill")
        self._botao_extrair.set_action_name("win.extrair")
        self._botao_extrair.set_tooltip_text(tr("Extrair (Ctrl+Enter)"))

        area_acoes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        area_acoes.append(self._estado)
        area_acoes.append(self._botao_extrair)

        conteudo = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=18,
            margin_bottom=18,
            margin_start=12,
            margin_end=12,
        )
        for widget in (grupo_arquivo, self._grupo_senha, grupo_destino, area_acoes):
            conteudo.append(widget)

        grade = Adw.Clamp(maximum_size=640)
        grade.set_child(conteudo)
        rolagem = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        rolagem.set_child(grade)
        return rolagem

    def _montar_executando(self) -> Gtk.Widget:
        self._pagina_executando = Adw.StatusPage(
            icon_name="system-run-symbolic", title="", description=tr("Preparando…")
        )
        self._barra = Gtk.ProgressBar(show_text=True)
        self._detalhe = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._detalhe.add_css_class("dim-label")
        self._botao_cancelar = Gtk.Button(
            label=tr("Cancelar"),
            halign=Gtk.Align.CENTER,
            tooltip_text=tr("Cancelar (Ctrl+.)"),
            action_name="win.cancelar",
        )
        self._botao_cancelar.add_css_class("destructive-action")
        self._botao_cancelar.add_css_class("pill")

        caixa = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            width_request=340,
            halign=Gtk.Align.CENTER,
        )
        for widget in (self._barra, self._detalhe, self._botao_cancelar):
            caixa.append(widget)
        self._pagina_executando.set_child(caixa)
        return self._pagina_executando

    def _montar_resultado(self) -> Gtk.Widget:
        self._pagina_resultado = Adw.StatusPage(
            icon_name="object-select-symbolic", title="", description=""
        )
        self._botao_abrir = Gtk.Button(
            label=tr("Abrir pasta de destino"),
            halign=Gtk.Align.CENTER,
            visible=False,
        )
        self._botao_abrir.add_css_class("suggested-action")
        self._botao_abrir.add_css_class("pill")
        self._botao_abrir.connect("clicked", lambda *_: self._abrir_pasta())

        botao_registro = Gtk.Button(
            label=tr("Ver registro técnico"), halign=Gtk.Align.CENTER, css_classes=["flat"]
        )
        botao_registro.connect("clicked", lambda *_: self._mostrar_registro())

        botao_nova = Gtk.Button(
            label=tr("Nova extração"), halign=Gtk.Align.CENTER, css_classes=["pill"]
        )
        botao_nova.connect("clicked", lambda *_: self._nova_extracao())

        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        for widget in (self._botao_abrir, botao_registro, botao_nova):
            caixa.append(widget)
        self._pagina_resultado.set_child(caixa)
        return self._pagina_resultado

    @staticmethod
    def _botao(rotulo: str, acao: str, dica: str) -> Gtk.Button:
        botao = Gtk.Button(label=rotulo, valign=Gtk.Align.CENTER, tooltip_text=dica)
        botao.set_action_name(acao)
        return botao

    def _registrar_acoes(self) -> None:
        funcoes = {
            "abrir-arquivo": self._escolher_arquivo,
            "escolher-destino": self._escolher_destino,
            "extrair": self.extrair,
            "cancelar": self.cancelar,
            "registro": self._mostrar_registro,
        }
        for nome, funcao in funcoes.items():
            acao = Gio.SimpleAction.new(nome, None)
            acao.connect("activate", lambda _a, _p, f=funcao: f())
            self.add_action(acao)
            self._acoes[nome] = acao

    def _habilitar_arrastar_soltar(self) -> None:
        alvo = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        alvo.connect("drop", self._ao_soltar)
        self.add_controller(alvo)

    def _ao_soltar(self, _alvo: Gtk.DropTarget, arquivo: Gio.File, _x: float, _y: float) -> bool:
        if arquivo is None or arquivo.get_path() is None:
            return False
        self.definir_arquivo(arquivo.get_path())
        return True

    # ------------------------------------------------------------- escolhas

    def _escolher_arquivo(self) -> None:
        if self._extracao is not None:
            return
        filtro = Gtk.FileFilter()
        filtro.set_name(tr("Arquivos compactados"))
        for sufixo in SUFIXOS_FILTRO:
            filtro.add_suffix(sufixo)
        todos = Gtk.FileFilter()
        todos.set_name(tr("Todos os arquivos"))
        todos.add_pattern("*")
        filtros = Gio.ListStore.new(Gtk.FileFilter)
        filtros.append(filtro)
        filtros.append(todos)
        dialogo = Gtk.FileDialog(
            title=tr("Escolher arquivo compactado"), filters=filtros, default_filter=filtro
        )
        dialogo.open(self, None, self._arquivo_escolhido)

    def _arquivo_escolhido(self, dialogo: Gtk.FileDialog, resultado: Gio.AsyncResult) -> None:
        try:
            arquivo = dialogo.open_finish(resultado)
        except GLib.Error:
            return  # o usuário cancelou
        self.definir_arquivo(arquivo.get_path())

    def _escolher_destino(self) -> None:
        if self._extracao is not None:
            return
        dialogo = Gtk.FileDialog(title=tr("Escolher pasta de destino"))
        dialogo.select_folder(self, None, self._destino_escolhido)

    def _destino_escolhido(self, dialogo: Gtk.FileDialog, resultado: Gio.AsyncResult) -> None:
        try:
            pasta = dialogo.select_folder_finish(resultado)
        except GLib.Error:
            return
        self._destino = Path(pasta.get_path())
        self._mostrar_pronto()
        self._atualizar_tela()

    def definir_arquivo(self, caminho: str) -> None:
        """Ponto de entrada para escolha, arrastar e abrir pela linha de comando."""
        if self._extracao is not None:
            return
        try:
            conjunto = identificar(caminho)
        except ErroArquivo as erro:
            self._conjunto = None
            self._verificacao = None
            self._destino = None
            self._pasta_final = None
            self._definir_estado(str(erro), "error")
            self._atualizar_tela()
            return

        self._conjunto = conjunto
        self._tamanho_compactado = conjunto.tamanho_total()
        self._verificacao = None
        self._destino = conjunto.entrada.parent
        self._pasta_final = None
        self._campo_senha.set_text("")
        self._definir_estado(tr("Verificando o arquivo…"), "dim-label")
        self._atualizar_tela()
        self._verificar_em_segundo_plano(conjunto)

    # ----------------------------------------------------------- verificação

    def _verificar_em_segundo_plano(self, conjunto: ConjuntoCompactado) -> None:
        programa = self._programa

        def trabalho() -> None:
            if programa is None:
                resultado = Verificacao(
                    erro=tr("7-Zip não encontrado. Rode o instalar_dependencias.sh.")
                )
            else:
                resultado = verificar(programa, conjunto.entrada)
            GLib.idle_add(self._verificacao_pronta, conjunto, resultado)

        threading.Thread(target=trabalho, daemon=True).start()

    def _verificacao_pronta(self, conjunto: ConjuntoCompactado, resultado: Verificacao) -> bool:
        if conjunto is not self._conjunto:  # o usuário já escolheu outro arquivo
            return GLib.SOURCE_REMOVE
        self._verificacao = resultado
        self._mostrar_pronto()
        self._atualizar_tela()
        return GLib.SOURCE_REMOVE

    def _mostrar_pronto(self) -> None:
        """Mensagem de estado conforme a verificação e o espaço livre no destino."""
        if self._extracao is not None:
            return
        verificacao = self._verificacao
        if verificacao is None:
            return
        if self._conjunto is not None and _eh_rar(self._conjunto) and not self._rar_disponivel:
            self._definir_estado(
                tr("Este arquivo é RAR, e nenhum 7-Zip instalado sabe ler RAR. Veja o README."),
                "error",
            )
            return
        if verificacao.erro:
            if self._conjunto is not None and self._conjunto.faltando:
                numeros = ", ".join(str(n) for n in self._conjunto.faltando)
                texto = tr('Faltam as partes nº {0}. Coloque-as na mesma pasta e escolha o arquivo de novo.').format(numeros)
            else:
                texto = verificacao.erro
            self._definir_estado(texto, "error")
            return
        if self._destino is not None and verificacao.tamanho is not None:
            livre = shutil.disk_usage(_pasta_existente(self._destino)).free
            if verificacao.tamanho > livre:
                self._definir_estado(
                    tr('Atenção: o conteúdo ocupa {0} e há {1} livres no destino.').format(formatar_tamanho(verificacao.tamanho), formatar_tamanho(livre)),
                    "warning",
                )
                return
        if verificacao.precisa_senha:
            self._definir_estado(tr("Informe a senha e clique em Extrair."), "dim-label")
        else:
            self._definir_estado(tr("Pronto para extrair."), "success")

    # ------------------------------------------------------------- tela

    def _atualizar_tela(self) -> None:
        conjunto = self._conjunto
        verificacao = self._verificacao

        if conjunto is None:
            self._linha_arquivo.set_subtitle(tr("Escolha qualquer parte do arquivo, ou arraste para cá"))
            self._linha_formato.set_subtitle("—")
            self._linha_faltando.set_visible(False)
        else:
            self._linha_arquivo.set_subtitle(str(conjunto.entrada))
            self._linha_formato.set_subtitle(
                f"{conjunto.descricao} · {formatar_tamanho(self._tamanho_compactado)}"
            )
            self._linha_faltando.set_visible(bool(conjunto.faltando))
            if conjunto.faltando:
                numeros = ", ".join(str(n) for n in conjunto.faltando)
                self._linha_faltando.set_subtitle(
                    tr('Faltam as partes nº {0}. Coloque-as na mesma pasta antes de extrair.').format(numeros)
                )

        if self._destino is None:
            self._linha_destino.set_subtitle("—")
            self._linha_previa.set_subtitle("—")
        else:
            self._linha_destino.set_subtitle(str(self._destino))
            self._linha_previa.set_subtitle(str(self._pasta_prevista()))

        self._grupo_senha.set_description(self._texto_da_senha(conjunto, verificacao))
        self._acoes["extrair"].set_enabled(self._pode_extrair())

    def _texto_da_senha(
        self, conjunto: ConjuntoCompactado | None, verificacao: Verificacao | None
    ) -> str:
        if conjunto is None:
            return tr("Escolha um arquivo para descobrir se ele pede senha.")
        if verificacao is None:
            return tr("Verificando se o arquivo pede senha…")
        if verificacao.erro:
            return ""
        if verificacao.precisa_senha:
            return tr("Este arquivo pede senha.")
        return tr("Este arquivo não pede senha. Deixe o campo vazio.")

    def _pasta_prevista(self) -> Path:
        assert self._destino is not None
        if self._chave_pasta.get_active() and self._conjunto is not None:
            return self._destino / self._conjunto.rotulo
        return self._destino

    def _pode_extrair(self) -> bool:
        if self._extracao is not None or self._programa is None:
            return False
        if self._conjunto is None or self._destino is None or self._verificacao is None:
            return False
        if _eh_rar(self._conjunto) and not self._rar_disponivel:
            return False
        if self._verificacao.erro:
            return False
        if self._verificacao.precisa_senha and not self._campo_senha.get_text():
            return False
        return os.access(_pasta_existente(self._destino), os.W_OK)

    def _definir_estado(self, texto: str, classe: str) -> None:
        for antiga in ("error", "warning", "success", "dim-label"):
            self._estado.remove_css_class(antiga)
        self._estado.add_css_class(classe)
        self._estado.set_label(texto)

    def _registrar(self, linha: str) -> bool:
        self._buffer_registro.insert(self._buffer_registro.get_end_iter(), linha + "\n")
        return GLib.SOURCE_REMOVE

    def _mostrar_registro(self) -> None:
        """Janelinha com o registro técnico. Compartilha o mesmo texto, então está sempre atualizada."""
        if self._janela_registro is None:
            self._texto_registro = Gtk.TextView(
                buffer=self._buffer_registro,
                editable=False,
                cursor_visible=False,
                monospace=True,
                wrap_mode=Gtk.WrapMode.WORD_CHAR,
                top_margin=8,
                bottom_margin=8,
                left_margin=8,
                right_margin=8,
            )
            rolagem = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
            rolagem.set_child(self._texto_registro)
            cabecalho = Adw.HeaderBar()
            cabecalho.set_title_widget(Adw.WindowTitle.new(tr("Registro técnico"), ""))
            visao = Adw.ToolbarView()
            visao.add_top_bar(cabecalho)
            visao.set_content(rolagem)
            self._janela_registro = Adw.Window(
                transient_for=self,
                title=tr("Registro técnico"),
                default_width=640,
                default_height=480,
                hide_on_close=True,
            )
            self._janela_registro.set_content(visao)
        self._janela_registro.present()
        self._texto_registro.scroll_to_iter(
            self._buffer_registro.get_end_iter(), 0.0, False, 0.0, 0.0
        )

    # ------------------------------------------------------------- extração

    def extrair(self) -> None:
        if not self._pode_extrair():
            return
        assert self._conjunto is not None and self._programa is not None
        destino = self._pasta_prevista()
        senha = self._campo_senha.get_text() or None
        modo = OPCOES_EXISTENTE[self._seletor_existente.get_selected()][0]
        comando = montar_comando_extracao(
            self._programa, self._conjunto.entrada, destino, senha, modo
        )

        self._pasta_final = destino
        self._medidor = Medidor(self._verificacao.tamanho if self._verificacao else None)
        self._percentual_7z = 0
        self._escrito = 0

        # Troca a janela para a tela de progresso, como o Conversor.
        self._pagina_executando.set_title(self._conjunto.rotulo)
        self._pagina_executando.set_description("Preparando…")
        self._barra.set_fraction(0)
        self._barra.set_text("0%")
        self._detalhe.set_label(tr("calculando a velocidade…"))
        self._titulo.set_title(tr("Extraindo"))
        self._pilha.set_visible_child_name("executando")
        self._definir_estado("Extraindo…", "dim-label")
        self._registrar(f"$ {_comando_para_registro(comando)}")

        extracao = Extracao(
            comando,
            ao_progresso=lambda p: GLib.idle_add(self._mostrar_progresso, p),
            ao_linha=lambda linha: GLib.idle_add(self._registrar, linha),
        )
        self._extracao = extracao
        self._atualizar_tela()
        threading.Thread(
            target=self._trabalho_extracao, args=(extracao, destino), daemon=True
        ).start()

    def _trabalho_extracao(self, extracao: Extracao, pasta: Path) -> None:
        # A fotografia vem antes do 7z: depois, só o que não estava ali é candidato à remoção.
        # Se ela falhar, a limpeza fica desligada: "None" significa "pasta criada agora",
        # então não pode servir de substituto para uma fotografia que não aconteceu.
        try:
            antes = limpeza.fotografar(pasta)
        except OSError as erro:
            antes = None
            fotografia_erro = erro
        else:
            fotografia_erro = None
        # Bytes já no destino antes da extração; o que passar disso foi gravado agora.
        base = limpeza.tamanho_em_disco(pasta)
        parar = threading.Event()
        threading.Thread(
            target=self._amostrar_escrita, args=(pasta, base, parar), daemon=True
        ).start()
        resultado = extracao.executar()
        parar.set()
        complemento = ""
        if resultado.estado not in ("ok", "aviso"):
            if fotografia_erro is not None:
                complemento = tr('Não consegui conferir a pasta, então nada foi removido: {0}').format(fotografia_erro)
            else:
                try:
                    removidos = limpeza.remover_criados(pasta, antes)
                except OSError as erro:
                    complemento = tr('Não consegui remover tudo: {0}').format(erro)
                else:
                    if removidos:
                        complemento = tr('Os {0} itens criados nesta tentativa foram removidos.').format(removidos)
        GLib.idle_add(self._extracao_terminou, resultado, complemento)

    def _amostrar_escrita(self, pasta: Path, base: int, parar: threading.Event) -> None:
        """Thread que mede, a cada segundo, quanto a extração já gravou no destino."""
        while not parar.wait(1.0):
            escrito = max(0, limpeza.tamanho_em_disco(pasta) - base)
            GLib.idle_add(self._mostrar_escrito, escrito)

    def _mostrar_escrito(self, escrito: int) -> bool:
        self._escrito = escrito
        self._atualizar_estimativa()
        return GLib.SOURCE_REMOVE

    def _mostrar_progresso(self, percentual: int) -> bool:
        self._percentual_7z = percentual
        self._atualizar_estimativa()
        return GLib.SOURCE_REMOVE

    def _atualizar_estimativa(self) -> None:
        """Barra e linha de detalhes a partir do que foi gravado no destino.

        Com o tamanho total conhecido, a fração são os bytes gravados: andam a cada
        segundo e não dependem da porcentagem do 7-Zip. Ela começa alta durante a
        leitura dos cabeçalhos dos volumes e estragaria a velocidade. Sem o total
        (cabeçalho criptografado), usa a porcentagem do 7-Zip.
        """
        if self._medidor is None:
            return
        total = self._medidor.total
        if total:
            fracao = min(self._escrito / total, 1.0)
        else:
            fracao = self._percentual_7z / 100
        estimativa = self._medidor.atualizar(fracao * 100, time.monotonic())
        self._pagina_executando.set_description(tr("Extraindo os arquivos…"))
        self._barra.set_fraction(estimativa.percentual / 100)
        self._barra.set_text(f"{estimativa.percentual}%")
        self._detalhe.set_label(linha_progresso(estimativa))

    def _extracao_terminou(self, resultado: Resultado, complemento: str) -> bool:
        self._extracao = None
        self._medidor = None
        texto = f"{resultado.mensagem} {complemento}".strip()
        self._registrar(f"[{resultado.estado}] {texto}")
        self._definir_estado(texto, CLASSE_POR_ESTADO.get(resultado.estado, "error"))

        icone, titulo = ICONE_E_TITULO_RESULTADO.get(
            resultado.estado, ("dialog-error-symbolic", tr("Extração não concluída"))
        )
        self._pagina_resultado.set_icon_name(icone)
        self._pagina_resultado.set_title(titulo)
        self._pagina_resultado.set_description(texto)
        self._botao_abrir.set_visible(resultado.estado in ("ok", "aviso"))
        self._pedir_senha_ao_voltar = resultado.estado in ("senha_incorreta", "senha_necessaria")
        self._titulo.set_title(tr("Resultado"))
        self._pilha.set_visible_child_name("resultado")
        self._atualizar_tela()
        return GLib.SOURCE_REMOVE

    def _nova_extracao(self) -> None:
        self._titulo.set_title(tr("Nova extração"))
        self._pilha.set_visible_child_name("configurar")
        self._atualizar_tela()
        if self._pedir_senha_ao_voltar:
            self._campo_senha.grab_focus()
        self._pedir_senha_ao_voltar = False

    def cancelar(self) -> None:
        if self._extracao is None:
            return
        self._extracao.cancelar()
        self._detalhe.set_label(tr("Cancelando…"))
        self._definir_estado("Cancelando…", "dim-label")

    def _abrir_pasta(self) -> None:
        if self._pasta_final is None or not self._pasta_final.exists():
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(self._pasta_final))).launch(self, None, None)

    def _ao_fechar(self, _janela: Gtk.Window) -> bool:
        # Fechar a janela no meio de uma extração interrompe o 7z.
        if self._extracao is not None:
            self._extracao.cancelar()
        return False


class ExtratorApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self._janela: JanelaExtrator | None = None

        sair = Gio.SimpleAction.new("sair", None)
        sair.connect("activate", lambda *_: self.quit())
        self.add_action(sair)
        self.set_accels_for_action("app.sair", ["<Control>q"])
        self.set_accels_for_action("win.abrir-arquivo", ["<Control>o"])
        self.set_accels_for_action("win.escolher-destino", ["<Control>d"])
        self.set_accels_for_action("win.extrair", ["<Control>Return"])
        self.set_accels_for_action("win.cancelar", ["<Control>period"])
        self.set_accels_for_action("win.registro", ["<Control>l"])

    def do_activate(self) -> None:
        self._janela_principal().present()

    def do_open(self, arquivos: list[Gio.File], _quantidade: int, _dica: str) -> None:
        janela = self._janela_principal()
        janela.present()
        if arquivos and arquivos[0].get_path():
            janela.definir_arquivo(arquivos[0].get_path())

    def _janela_principal(self) -> JanelaExtrator:
        if self._janela is None:
            self._janela = JanelaExtrator(self)
        return self._janela


def main(argv: list[str] | None = None) -> int:
    return ExtratorApp().run(argv if argv is not None else sys.argv)
