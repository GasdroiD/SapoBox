"""Testes de ponta a ponta da janela GTK 4 + libadwaita.

Sobem um gtk4-broadwayd próprio, então não abrem janela no display do usuário e não
gravam preferências reais (XDG_CONFIG_HOME aponta para um diretório temporário).
Ignorados quando PyGObject, GTK 4 ou gtk4-broadwayd não estão instalados.
"""
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BROADWAY = shutil.which("gtk4-broadwayd")

gui = None
GLib = None
Gtk = None
broadway = None
config_dir = None
app = None


def setUpModule():
    global gui, GLib, Gtk, broadway, config_dir, app
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import GLib as _GLib, Gtk as _Gtk
    except (ImportError, ValueError) as exc:
        raise unittest.SkipTest(f"PyGObject/GTK 4 indisponível: {exc}")
    if BROADWAY is None:
        raise unittest.SkipTest("gtk4-broadwayd não encontrado (pacote libgtk-4-bin)")
    display = f":{random.randint(40, 90)}"
    broadway = subprocess.Popen([BROADWAY, display], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    os.environ["GDK_BACKEND"] = "broadway"
    os.environ["BROADWAY_DISPLAY"] = display
    config_dir = tempfile.TemporaryDirectory()
    os.environ["XDG_CONFIG_HOME"] = config_dir.name
    GLib, Gtk = _GLib, _Gtk
    if not Gtk.init_check():
        broadway.terminate()
        raise unittest.SkipTest("GTK não conseguiu abrir o display broadway")
    from sapobox.converter import gui as gui_module
    gui = gui_module
    app = gui.Application()
    app.register(None)  # como run(): janelas só depois do startup; uma aplicação por processo


def tearDownModule():
    if broadway is not None:
        broadway.terminate()
        broadway.wait(timeout=10)
    if config_dir is not None:
        config_dir.cleanup()


def pump(condition, timeout):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context.iteration(False)
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def make_game(root, size=0):
    (root / "sce_sys").mkdir(parents=True)
    (root / "sce_sys" / "param.json").write_text(json.dumps({
        "titleId": "PPSA22222",
        "localizedParameters": {"defaultLanguage": "en-US", "en-US": {"titleName": "Teste GTK"}},
    }))
    (root / "data.bin").write_bytes(b"x" * 4096)
    if size:
        with (root / "big.bin").open("wb") as stream:
            for _ in range(size // (1 << 20)):
                stream.write(os.urandom(1 << 20))
    return root


class GuiWindowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "saida"
        self.out.mkdir()
        self.game = make_game(self.root / "jogo")

        for patcher in (patch.object(gui, "load_settings", return_value={}),
                        patch.object(gui, "save_settings")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.open_window()

    def open_window(self, runtime="default"):
        if runtime != "default":
            patcher = patch.object(gui, "local_runtime", return_value=runtime)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.win = gui.Window(app)
        self.win.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.addCleanup(self.close_window)

    def close_window(self):
        if self.win.worker is not None:
            self.win.cancel_run()
            pump(lambda: self.win.worker is None, 30)
        self.win.cancel_probe()
        self.win.destroy()

    def test_starts_by_asking_for_the_game(self):
        self.assertFalse(self.win.start_button.get_sensitive())
        self.assertEqual(self.win.hint.get_label(), "Escolha o jogo para começar")

    def test_detection_then_ready(self):
        self.win.set_source(str(self.game))
        self.win.set_dest(str(self.out))
        self.assertTrue(pump(lambda: not self.win.form.probing and self.win.start_button.get_sensitive(), 30))
        self.assertIn("PPSA22222", self.win.detect_row.get_title())
        self.assertEqual(self.win.hint.get_label(), "Tudo pronto. A conversão não altera o original.")

    def test_apr_game_without_module_is_blocked_with_plain_reason(self):
        self.close_window()
        emulator = make_game(self.root / "apr")
        (emulator / "fakelib").mkdir()
        (emulator / "fakelib" / "libSceAmpr.sprx").write_bytes(b"module")
        self.open_window(runtime=None)
        self.win.set_source(str(emulator))
        self.win.set_dest(str(self.out))
        pump(lambda: not self.win.form.probing, 30)
        self.assertFalse(self.win.start_button.get_sensitive())
        self.assertEqual(self.win.hint.get_label(), "Este jogo precisa de um módulo APR")

    def test_conversion_end_to_end_through_the_ui(self):
        self.win.set_source(str(self.game))
        self.win.set_dest(str(self.out))
        pump(lambda: not self.win.form.probing and self.win.start_button.get_sensitive(), 30)
        self.win.start()
        self.assertTrue(pump(lambda: self.win.stack.get_visible_child_name() == "result", 120))
        self.assertEqual(self.win.result[0], "done")
        self.assertEqual(self.win.result_page.get_title(), "Conversão concluída")
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(len(names), 2)
        self.assertTrue(any(n.endswith(".ffpfsc") for n in names))
        description = self.win.result_page.get_description()
        self.assertIn("Salvo em:", description)
        self.assertRegex(description, r"\n\n(Diminuiu|Aumentou|O tamanho não mudou)")

    def test_cancel_stops_fast_and_keeps_nothing(self):
        big = make_game(self.root / "grande", size=300 << 20)
        self.win.set_source(str(big))
        self.win.set_dest(str(self.out))
        pump(lambda: not self.win.form.probing and self.win.start_button.get_sensitive(), 30)
        self.win.start()
        self.assertTrue(pump(lambda: "de " in self.win.run_stats.get_label(), 60))
        started = time.monotonic()
        self.win.cancel_run()
        self.assertTrue(pump(lambda: self.win.stack.get_visible_child_name() == "result", 30))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(self.win.result[0], "cancelled")
        self.assertEqual(list(self.out.iterdir()), [])

    def test_ffpfsc_source_switches_to_lz4_and_explains_the_rule(self):
        from sapobox.converter import catalog
        from sapobox.converter.workflow import run
        images = self.root / "imagens"
        images.mkdir()
        with patch.object(catalog, "lookup", return_value=None):
            image, _ = run(str(self.game), str(images), workers=2)
        self.win.set_source(str(image))
        self.win.set_dest(str(self.out))
        self.assertEqual(self.win.form.output_format, "lz4")
        self.assertTrue(pump(lambda: not self.win.form.probing, 30))
        self.assertEqual(self.win.hint.get_label(), "Este jogo não usa libSceAmpr")
        self.win.set_output_format("ffpfsc")
        self.assertEqual(self.win.hint.get_label(), "Arquivo .ffpfsc só vira LZ4")

    def test_window_fits_its_minimum_size_in_the_largest_state(self):
        self.win.set_output_format("lz4")  # LZ4 mostra o perfil
        self.assertTrue(pump(lambda: self.win.module_group.get_visible(), 5))
        # Mede a página de configuração (cabeçalho e barra inferior incluídos), sem o pedido de tamanho da janela.
        page = self.win.stack.get_child_by_name("setup")
        natural_h = page.measure(Gtk.Orientation.VERTICAL, gui.MIN_WIDTH)[1]
        self.assertLessEqual(natural_h, gui.MIN_HEIGHT)
        self.win.profile_row.set_expanded(True)
        expanded_extra = self.win.profile_pick.measure(Gtk.Orientation.VERTICAL, 400)[1]
        self.assertLessEqual(natural_h + expanded_extra, gui.MIN_HEIGHT)
        self.assertLessEqual(page.measure(Gtk.Orientation.HORIZONTAL, -1)[0], gui.MIN_WIDTH)


if __name__ == "__main__":
    unittest.main()
