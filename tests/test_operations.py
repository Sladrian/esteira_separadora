"""Preservação da configuração, rollback e identificação do processo de segundo plano."""
from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
fake_lock = SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4)
with patch.dict(sys.modules, {"fcntl": fake_lock}):
    updater = importlib.import_module("atualizar")
    background = importlib.import_module("controle")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/"package"
        self.target = self.root/"esteira"
        self.source.mkdir()
        self.target.mkdir()
        (self.source/"tests").mkdir()
        (self.source/"app.py").write_text("nova aplicação", encoding="utf-8")
        (self.source/"core.py").write_text("nova lógica", encoding="utf-8")
        (self.source/"config.json").write_text("config padrão NÃO deve ser instalada", encoding="utf-8")
        (self.target/"app.py").write_text("aplicação anterior", encoding="utf-8")
        (self.target/"core.py").write_text("lógica anterior", encoding="utf-8")
        self.original = b'{"cores":"calibradas","roi":[110,105,80,25]}'
        (self.target/"config.json").write_bytes(self.original)

    def tearDown(self):
        self.temp.cleanup()

    def run_update(self, test_command=None):
        with patch.object(updater, "__file__", str(self.source/"atualizar.py")), \
                patch.object(updater, "open", return_value=io.StringIO()), \
                patch.object(updater.subprocess, "run", side_effect=test_command) as run, \
                patch.object(sys, "argv", ["atualizar.py", str(self.target)]), redirect_stdout(io.StringIO()):
            updater.main()
        return run

    def test_install_preserves_config_and_makes_backup(self):
        run = self.run_update()
        self.assertEqual(run.call_count, 2)
        self.assertEqual((self.target/"app.py").read_text(encoding="utf-8"), "nova aplicação")
        self.assertEqual((self.target/"config.json").read_bytes(), self.original)
        backups = list((self.root/"esteira-backups").glob("*/config.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertEqual(backups[0].with_name("app.py").read_text(encoding="utf-8"), "aplicação anterior")

    def test_failed_checks_never_replace_code(self):
        with self.assertRaises(RuntimeError):
            self.run_update(RuntimeError("teste falhou"))
        self.assertEqual((self.target/"app.py").read_text(encoding="utf-8"), "aplicação anterior")
        self.assertEqual((self.target/"config.json").read_bytes(), self.original)

    def test_partial_failure_restores_previous_code(self):
        copy2 = shutil.copy2
        def copy(source, destination, *args, **kwargs):
            if Path(source).name == "core.py" and Path(destination).name.endswith(".atualizando"):
                raise OSError("falha na gravação")
            return copy2(source, destination, *args, **kwargs)
        with patch.object(updater.shutil, "copy2", side_effect=copy), self.assertRaises(OSError):
            self.run_update()
        self.assertEqual((self.target/"app.py").read_text(encoding="utf-8"), "aplicação anterior")
        self.assertEqual((self.target/"core.py").read_text(encoding="utf-8"), "lógica anterior")
        self.assertEqual((self.target/"config.json").read_bytes(), self.original)


class BackgroundTests(unittest.TestCase):
    def test_identity_requires_same_script_and_non_zombie_process(self):
        stat = "99 (python) "+" ".join(["S"]+["0"]*18+["555"])
        file = Mock()
        file.read_text.return_value = stat
        file.read_bytes.return_value = b"python\0"+str(background.BASE/"app.py").encode()+b"\0run\0"
        with patch.object(background, "Path", return_value=file):
            self.assertEqual(background.identity(99), "555")
            file.read_bytes.return_value = b"outro_programa\0"
            self.assertIsNone(background.identity(99))
            file.read_text.return_value = stat.replace(") S ", ") Z ")
            self.assertIsNone(background.identity(99))

    def test_reused_pid_does_not_match_previous_identity(self):
        with patch.object(background, "identity", return_value="novo"):
            self.assertFalse(background.running({"pid": 99, "identity": "anterior"}))


if __name__ == "__main__":
    unittest.main()
