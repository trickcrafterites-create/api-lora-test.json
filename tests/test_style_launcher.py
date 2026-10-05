import base64
import contextlib
import hashlib
import io
from pathlib import Path
import shlex
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_style_launcher as launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.blobs = {"scripts/style_bootstrap.py": b"print('verified bootstrap')\n",
                      "catalog/style-loras.json": b"{}", "scripts/download_loras.py": b"verifier"}
        self.pins = launcher.manifest("a" * 40, self.blobs)

    def test_sources_are_exact_fixed_paths_at_full_commit_with_byte_hashes(self):
        for item in self.pins["sources"]:
            self.assertEqual(item["url"], launcher.REMOTE_PREFIX + "a" * 40 + "/" + item["path"])
            self.assertEqual(item["sha256"], hashlib.sha256(self.blobs[item["path"]]).hexdigest())
        for bad in ["main", "a" * 39, "../main", "a" * 40 + ";echo injected"]:
            with self.assertRaises(ValueError):
                launcher.manifest(bad, self.blobs)

    def test_command_roundtrips_shell_arguments_without_expansion(self):
        built = launcher.command(self.pins)
        self.assertEqual(shlex.split(built["dockerArgs"]), built["argv"])
        compiled = compile(built["launcherSource"], "launcher", "exec")
        self.assertIsNotNone(compiled)
        encoded = built["argv"][2].split("'")[1]
        self.assertEqual(base64.b64decode(encoded).decode(), built["launcherSource"])

    def test_default_off_does_no_source_fetch_and_preserves_original_start(self):
        calls = []
        class Started(BaseException):
            pass
        def start(*args):
            calls.append(args)
            raise Started()
        with patch.dict("os.environ", {}, clear=True), patch("os.execv", side_effect=start), patch("urllib.request.build_opener") as network:
            with self.assertRaises(Started):
                exec(launcher.command(self.pins)["launcherSource"], {})
        self.assertEqual(calls, [("/start.sh", ["/start.sh"])])
        network.assert_not_called()

    def test_existing_verifier_mismatch_never_downloads_or_executes_remote_code(self):
        calls = []
        class Started(BaseException):
            pass
        def start(*args):
            calls.append(args)
            raise Started()
        with patch.dict("os.environ", {"AELIX_STYLE_BOOTSTRAP": "1", "AELIX_STYLE_BROKER_TOKEN": "secret"}, clear=True):
            with patch("pathlib.Path.read_bytes", return_value=b"wrong verifier"), patch("os.execv", side_effect=start):
                with patch("urllib.request.build_opener") as network, self.assertRaises(Started):
                    exec(launcher.command(self.pins)["launcherSource"], {})
                network.assert_not_called()
        self.assertEqual(calls, [("/start.sh", ["/start.sh"])])

    def test_changed_source_url_is_rejected_before_network(self):
        self.pins["sources"][0]["url"] = "https://evil.example/code.py"
        class Started(BaseException):
            pass
        with patch.dict("os.environ", {"AELIX_STYLE_BOOTSTRAP": "1"}, clear=True):
            with patch("pathlib.Path.read_bytes", return_value=b"verifier"), patch("os.execv", side_effect=Started):
                with patch("urllib.request.build_opener") as network, contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(Started):
                        exec(launcher.command(self.pins)["launcherSource"], {})
                network.return_value.open.assert_not_called()

    def test_changed_source_bytes_are_not_written_or_executed(self):
        class Started(BaseException):
            pass
        class Response(io.BytesIO):
            status = 200
        with patch.dict("os.environ", {"AELIX_STYLE_BOOTSTRAP": "1"}, clear=True):
            with patch("pathlib.Path.read_bytes", return_value=b"verifier"), patch("os.execv", side_effect=Started):
                with patch("urllib.request.build_opener") as network, patch("tempfile.mkdtemp") as temporary:
                    network.return_value.open.return_value = Response(b"injected code")
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(Started):
                        exec(launcher.command(self.pins)["launcherSource"], {})
                    temporary.assert_not_called()
                    request = network.return_value.open.call_args.args[0]
                    self.assertIsNone(request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
