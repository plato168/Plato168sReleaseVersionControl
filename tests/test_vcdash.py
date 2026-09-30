import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vcdash  # noqa: E402


class VcdashTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = vcdash.load_config(self.root)
        self.write("tool.py", '__version__ = "1.0.0"\nprint("hi")\n')
        self.write("web/index.html", '<meta name="version" content="2.0">\n<p>a</p>\n')
        self.write("web/css/site.css", "body{}\n")
        self.write("svc/package.json", json.dumps({"name": "svc", "version": "0.1.0"}))
        self.write("svc/lib/nested/pyproject.toml", 'version = "9.9"\n')
        self.write("notes.txt", "not an app\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def scan(self, **kw):
        return {a: e for a, e in vcdash.scan(self.root, self.cfg, **kw)}

    def db(self):
        return vcdash.load_db(self.root)

    def test_discovery_and_versions(self):
        snaps = vcdash.discover(self.root, self.cfg)
        self.assertEqual(set(snaps), {"tool.py", "web", "svc", "svc/lib/nested"})
        self.assertEqual(snaps["tool.py"]["version"], "1.0.0")
        self.assertEqual(snaps["web"]["version"], "2.0")
        self.assertEqual(snaps["svc"]["version"], "0.1.0")
        self.assertEqual(snaps["svc/lib/nested"]["version"], "9.9")
        self.assertEqual(set(snaps["web"]["files"]), {"index.html", "css/site.css"})
        self.assertNotIn("lib/nested/pyproject.toml", snaps["svc"]["files"])

    def test_history_lifecycle(self):
        first = self.scan(note="init")
        self.assertTrue(all(e["event"] == "created" for e in first.values()))
        self.assertEqual(self.scan(), {})

        self.write("tool.py", '__version__ = "1.1.0"\nprint("hi")\nprint("more")\n')
        (self.root / "web/css/site.css").unlink()
        status = self.scan(dry_run=True)
        self.assertEqual(status["tool.py"]["event"], "modified")
        self.assertEqual(len(self.db()["apps"]["tool.py"]["versions"]), 1)  # dry run 不寫入

        second = self.scan()
        tool = second["tool.py"]
        self.assertEqual((tool["rev"], tool["version"]), (2, "1.1.0"))
        self.assertEqual(tool["lines"], {"added": 2, "removed": 1})
        self.assertEqual(second["web"]["changes"]["removed"], ["css/site.css"])

        (self.root / "tool.py").unlink()
        self.assertEqual(self.scan()["tool.py"]["event"], "removed")
        self.write("tool.py", '__version__ = "1.1.0"\n')
        self.assertEqual(self.scan()["tool.py"]["event"], "restored")
        self.assertEqual(len(self.db()["scans"]), 5)

    def test_version_warnings(self):
        self.scan()
        self.write("tool.py", '__version__ = "1.0.0"\nprint("changed")\n')
        self.assertIn("版本號仍為 1.0.0", self.scan()["tool.py"]["warnings"][0])
        self.write("tool.py", '__version__ = "0.9"\n')
        self.assertIn("版本號倒退", self.scan()["tool.py"]["warnings"][0])

    def test_diff_restore_and_report(self):
        self.scan()
        self.write("tool.py", '__version__ = "1.1.0"\nprint("hi")\n')
        self.scan(note="bump")
        rec = self.db()["apps"]["tool.py"]
        store = vcdash.BlobStore(self.root / vcdash.DATA_DIR / "objects")
        diffs = vcdash.diff_versions(rec["versions"][0]["files"], rec["versions"][1]["files"], store, "r1", "r2")
        self.assertIn('+__version__ = "1.1.0"', diffs[0]["lines"])

        out = self.root / "restored"
        with redirect_stdout(io.StringIO()):
            vcdash.main(["restore", "tool.py", "1", "--root", str(self.root), "--to", str(out)])
        self.assertIn('"1.0.0"', (out / "tool.py").read_text(encoding="utf-8"))

        report = vcdash.generate_report(self.root, self.cfg)
        page = report.read_text(encoding="utf-8")
        self.assertIn("tool.py", page)
        self.assertIn("bump", page)
        with open(report.parent / "history.csv", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 1 + 5)  # 標題 + 4 個新增 + 1 次修改

    def test_report_and_restore_dirs_are_not_scanned(self):
        self.scan()
        vcdash.generate_report(self.root, self.cfg)
        self.write("vcdash-restore/x/app.py", "print(1)\n")
        self.assertEqual(self.scan(), {})

    def test_config_extra_extensions(self):
        (self.root / "vcdash.json").write_text('{"extra_app_extensions": [".txt"]}', encoding="utf-8")
        cfg = vcdash.load_config(self.root)
        self.assertIn("notes.txt", vcdash.discover(self.root, cfg))


if __name__ == "__main__":
    unittest.main()
