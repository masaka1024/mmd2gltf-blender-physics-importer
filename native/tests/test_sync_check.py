#!/usr/bin/env python3
"""sync_check.py の 3 ケース: 同一 -> 0、コメント/空白だけ違う -> 0、コード 1 文字違う -> 1。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
SYNC = TOOLS / "sync_check.py"

BASE = "int Add(int a, int b)\n{\n\treturn a + b;\n}\n"


def make_pair(tmp: Path, src_text: str, vendor_text: str):
    src, ven = tmp / "src", tmp / "ven"
    for root, text in ((src, src_text), (ven, vendor_text)):
        (root / "Public").mkdir(parents=True)
        (root / "Private").mkdir(parents=True)
        (root / "Public" / "A.h").write_text(text, encoding="utf-8")
        (root / "Private" / "A.cpp").write_text(BASE, encoding="utf-8")
    return src, ven


def run(src, ven):
    return subprocess.run([sys.executable, str(SYNC), str(src), str(ven)],
                          capture_output=True, text=True)


class SyncCheckTest(unittest.TestCase):
    def test_identical(self):
        with tempfile.TemporaryDirectory() as t:
            src, ven = make_pair(Path(t), BASE, BASE)
            self.assertEqual(run(src, ven).returncode, 0)

    def test_comment_and_whitespace_only(self):
        other = ("﻿// 名前スクラブ済みコメント\nint Add( int a,int b )\n{ /* block\n comment */\n"
                 "  return a+b; // tail\n}\n")
        with tempfile.TemporaryDirectory() as t:
            src, ven = make_pair(Path(t), BASE, other)
            self.assertEqual(run(src, ven).returncode, 0)

    def test_code_differs(self):
        with tempfile.TemporaryDirectory() as t:
            src, ven = make_pair(Path(t), BASE, BASE.replace("a + b", "a - b"))
            r = run(src, ven)
            self.assertEqual(r.returncode, 1)
            self.assertIn("A.h", r.stdout + r.stderr)

    def test_missing_source_warns_and_passes(self):
        with tempfile.TemporaryDirectory() as t:
            r = run(Path(t) / "nope", Path(t))
            self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
