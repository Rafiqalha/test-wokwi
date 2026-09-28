import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import configure_local


class ConfigureLocalTests(unittest.TestCase):
    def test_uses_studio_key_when_status_has_urls_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "supabase").mkdir()
            (root / "supabase" / "config.toml").write_text(
                'project_id = "wokwi-uts"\n', encoding="utf-8")
            (root / ".env.example").write_text(
                'SUPABASE_URL=http://127.0.0.1:55421\n'
                'SUPABASE_SECRET_KEY=sb_secret_REPLACE_ME\n', encoding="utf-8")
            cli = root / "supabase.cmd"
            cli.touch()
            status = SimpleNamespace(returncode=0,
                stdout="API_URL=http://127.0.0.1:55421\n")
            docker = SimpleNamespace(returncode=0,
                stdout=json.dumps([{"Config": {"Env": [
                    "SUPABASE_SECRET_KEY=sb_secret_test_value"]}}]))
            output = io.StringIO()
            with patch.object(configure_local, "ROOT", root), \
                 patch.object(configure_local, "CLI", cli), \
                 patch.object(configure_local.subprocess, "run",
                              side_effect=[status, docker]) as run, \
                 contextlib.redirect_stdout(output):
                configure_local.main()
            self.assertEqual(run.call_args_list[1].args[0],
                             ["docker", "inspect", "supabase_studio_wokwi-uts"])
            self.assertIn("SUPABASE_SECRET_KEY=sb_secret_test_value",
                          (root / ".env").read_text(encoding="utf-8"))
            self.assertNotIn("sb_secret_test_value", output.getvalue())


if __name__ == "__main__":
    unittest.main()
