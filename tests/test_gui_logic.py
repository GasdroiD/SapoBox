"""Regras da interface que não dependem de GTK: validação, textos e progresso."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapobox.converter import gui_logic as logic
from sapobox.converter.gui_logic import FFPFSC, LZ4, Form, Progress, human, duration, problems


def fields(issues):
    return {p.field for p in issues}


class ProblemsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.out = self.root / "out"
        self.out.mkdir()

    def ready_form(self, **changes):
        values = {"source": str(self.game), "dest": str(self.out), "detected": "unknown"}
        values.update(changes)
        return Form(**values)

    def test_empty_form_asks_for_the_game_first(self):
        issues = problems(Form())
        self.assertEqual(issues[0].field, "source")
        self.assertIn("Escolha o jogo", issues[0].short)

    def test_ready_when_everything_is_fine(self):
        self.assertEqual(problems(self.ready_form()), [])

    def test_probing_blocks_start(self):
        issues = problems(self.ready_form(probing=True))
        self.assertIn("Verificando", issues[0].short)

    def test_unreadable_game_blocks_start_with_reason(self):
        issues = problems(self.ready_form(probe_error="Não encontrei sce_sys/param.json."))
        self.assertEqual(issues[0].short, "Não foi possível ler este jogo")
        self.assertIn("param.json", issues[0].detail)

    def test_destination_inside_folder_game_is_blocked(self):
        inside = self.game / "saida"
        inside.mkdir()
        self.assertIn("dest", fields(problems(self.ready_form(dest=str(inside)))))

    def test_emulator_needs_a_module(self):
        module = [p for p in problems(self.ready_form(detected="emulator")) if p.field == "module"]
        self.assertEqual(len(module), 1)
        self.assertEqual(module[0].short, "Este jogo precisa de um módulo APR")

    def test_import_alone_does_not_need_a_module(self):
        self.assertEqual(problems(self.ready_form(detected="import")), [])

    def test_lz4_refuses_games_without_libsceampr(self):
        issues = problems(self.ready_form(output_format=LZ4, detected="unknown"))
        self.assertEqual(issues[0].short, "Este jogo não usa libSceAmpr")
        self.assertEqual(problems(self.ready_form(output_format=LZ4, detected="import"))[0].field, "module")

    def test_lz4_needs_runtime_and_the_exact_hash(self):
        self.assertIn("module", fields(problems(self.ready_form(output_format=LZ4, detected="emulator"))))
        wrong = self.root / "wrong.sprx"
        wrong.write_bytes(b"not the packed runtime")
        issues = problems(self.ready_form(output_format=LZ4, detected="emulator", module=str(wrong)))
        self.assertEqual(issues[0].short, "Runtime incompatível")

    def test_missing_module_file_is_reported(self):
        issues = problems(self.ready_form(detected="emulator", module=str(self.root / "gone.sprx")))
        self.assertEqual(issues[0].short, "Módulo não encontrado")

    def test_missing_profile_only_matters_for_lz4(self):
        profile = str(self.root / "gone.toml")
        self.assertEqual(problems(self.ready_form(profile=profile)), [])
        self.assertIn("profile", fields(problems(self.ready_form(output_format=LZ4, profile=profile))))

    def test_ffpfsc_source_must_be_lz4(self):
        image = self.root / "jogo.ffpfsc"
        image.write_bytes(b"x")
        issues = problems(Form(source=str(image), dest=str(self.out), detected="import", output_format=FFPFSC))
        self.assertEqual(issues[0].short, "Arquivo .ffpfsc só vira LZ4")
        issues = problems(Form(source=str(image), dest=str(self.out), detected="import", output_format=LZ4))
        self.assertFalse(any(p.field == "source" for p in issues))

    def test_lz4_from_image_needs_fuse_and_says_how_to_fix_it(self):
        image = self.root / "jogo.exfat"
        image.write_bytes(b"x")
        with patch.object(logic, "fuse_available", return_value=False):
            issues = problems(Form(source=str(image), dest=str(self.out), detected="import", output_format=LZ4))
        self.assertEqual(issues[0].short, "Falta o FUSE para ler esta imagem")
        self.assertIn("instalar_dependencias.sh", issues[0].detail)
        with patch.object(logic, "fuse_available", return_value=True):
            issues = problems(Form(source=str(image), dest=str(self.out), detected="import", output_format=LZ4))
        self.assertFalse(any(p.field == "source" for p in issues))

    def test_module_section_visibility(self):
        self.assertFalse(logic.show_module_section(Form(detected="unknown")))
        self.assertTrue(logic.show_module_section(Form(detected="emulator")))
        self.assertTrue(logic.show_module_section(Form(output_format=LZ4)))


class TextTest(unittest.TestCase):
    def test_worker_levels_match_backend_limits(self):
        for _label, level, _help in logic.COMPRESSION[FFPFSC]:
            self.assertTrue(1 <= level <= 9)
        for _label, level, _help in logic.COMPRESSION[LZ4]:
            self.assertTrue(1 <= level <= 12)

    def test_saving_line_says_how_much_the_game_shrank(self):
        gib = 1 << 30
        self.assertEqual(logic.saving_text(80 * gib, 50 * gib),
                         "Diminuiu 30,0 GiB (de 80,0 GiB para 50,0 GiB).")
        self.assertEqual(logic.saving_text(50 * gib, 80 * gib),
                         "Aumentou 30,0 GiB (de 50,0 GiB para 80,0 GiB).")
        self.assertEqual(logic.saving_text(gib, gib), "O tamanho não mudou (1,0 GiB).")

    def test_default_levels_keep_previous_behaviour(self):
        self.assertEqual(logic.default_level(FFPFSC), 1)
        self.assertEqual(logic.default_level(LZ4), 9)

    def test_level_index_falls_back_to_default(self):
        self.assertEqual(logic.COMPRESSION[LZ4][logic.level_index(LZ4, 12)][1], 12)
        self.assertEqual(logic.COMPRESSION[LZ4][logic.level_index(LZ4, 999)][1], 9)

    def test_threads_default_is_bounded(self):
        self.assertEqual(logic.default_workers(1), 2)
        self.assertEqual(logic.default_workers(4), 4)
        self.assertEqual(logic.default_workers(64), 8)

    def test_no_text_mentions_qt_or_english_errno(self):
        texts = [logic.THREADS_HELP, logic.OUTPUT_HELP[FFPFSC], logic.OUTPUT_HELP[LZ4]]
        for text in texts:
            self.assertNotIn("Qt", text)
            self.assertNotIn("Errno", text)

    def test_ffpfsc_detection_is_case_insensitive(self):
        self.assertTrue(logic.is_ffpfsc("/x/PPSA.FFPFSC"))
        self.assertFalse(logic.is_ffpfsc("/x/PPSA.exfat"))
        self.assertFalse(logic.is_ffpfsc(""))

    def test_short_path_keeps_the_end_of_long_paths(self):
        long = "/media/disco/PS5/jogos/PPSA03671/PPSA03671-app"
        short = logic.short_path(long)
        self.assertTrue(short.endswith("PPSA03671/PPSA03671-app"))
        self.assertLessEqual(len(short), len(long))
        self.assertEqual(logic.short_path(""), "")


class ProgressTest(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(human(1536), "1,5 KiB")
        self.assertEqual(duration(3661), "01:01:01")
        self.assertEqual(duration(-1), "calculando…")

    def test_fraction_speed_and_remaining_time(self):
        progress = Progress()
        progress.update({"done": 0, "total": 1000, "timestamp": 0.0})
        fraction, text = progress.update({"done": 500, "total": 1000, "timestamp": 1.0})
        self.assertEqual(fraction, 0.5)
        self.assertIn("restam 00:01", text)

    def test_unknown_total_is_indeterminate(self):
        fraction, text = Progress().update({"done": 10, "total": 0, "timestamp": 0.0})
        self.assertIsNone(fraction)
        self.assertIn("processados", text)

    def test_lz4_plan_counts_files_in_plain_words(self):
        fraction, text = Progress().update(
            {"done": 3, "total": 5, "timestamp": 0.0, "phase": "lz4-plan"})
        self.assertEqual(fraction, 0.6)
        self.assertEqual(text, "3 de 5 arquivos analisados")

    def test_phase_change_resets_speed(self):
        progress = Progress()
        progress.update({"done": 0, "total": 100, "timestamp": 0.0, "phase": "lz4-copy"})
        progress.update({"done": 100, "total": 100, "timestamp": 1.0, "phase": "lz4-copy"})
        self.assertGreater(progress.speed, 0)
        progress.update({"done": 0, "total": 10, "timestamp": 2.0, "phase": "lz4-verify"})
        self.assertEqual(progress.speed, 0.0)

    def test_runtime_hash_check_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.sprx")
            Path(path).write_bytes(b"other")
            self.assertFalse(logic.runtime_matches(path))
            self.assertFalse(logic.runtime_matches(os.path.join(tmp, "missing.sprx")))


if __name__ == "__main__":
    unittest.main()
