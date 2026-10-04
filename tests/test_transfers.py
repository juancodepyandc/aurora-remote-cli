import unittest
import tempfile
import hashlib
from pathlib import Path
from aurora_cli.transfers import _digest, receive_file

class TestAuroraTransfers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_digest(self):
        test_file = self.workspace / "test.txt"
        test_content = b"Hello Aurora Architecture Master!"
        test_file.write_bytes(test_content)
        expected_hash = hashlib.sha256(test_content).hexdigest()
        self.assertEqual(_digest(test_file), expected_hash)

    def test_reject_path_traversal(self):
        evil_events = [
            {"filename": "../evil.sh"},
            {"filename": "/etc/passwd"},
            {"filename": "subdir/../../secret.txt"},
            {"filename": "c:\\windows\\system32"},
            {"filename": "evil\x00file.txt"},
        ]
        for ev in evil_events:
            with self.subTest(filename=ev["filename"]):
                with self.assertRaises(ValueError):
                    receive_file(ev, client=None, workspace=self.workspace)

    def test_receive_inline_base64_file(self):
        content = b"Inline file content for test"
        import base64
        b64_content = base64.b64encode(content).decode("utf-8")
        event = {
            "filename": "output/test.txt",
            "data": b64_content,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        target = receive_file(event, client=None, workspace=self.workspace)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), content)

if __name__ == "__main__":
    unittest.main()
