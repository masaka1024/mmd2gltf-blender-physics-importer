"""vendor 取り込み / 同期検査で共有する規則 (除外規則と正規化)。"""
import hashlib
import re
from pathlib import Path

SUBDIRS = ("Public", "Private")
SOURCE_EXTS = {".h", ".cpp", ".inl"}

# 取り込まない物 (Private/Tests、モジュール定義、GLB マテリアル読み、UTF-8 CType ガード)
EXCLUDED_DIR_PARTS = {"Tests"}
EXCLUDED_STEMS = {"MmdPhysicsCoreModule", "MmdGlbMaterialReader", "MmdScopedUtf8CType"}


def is_excluded(rel: Path) -> bool:
    if any(p in EXCLUDED_DIR_PARTS for p in rel.parts):
        return True
    return rel.stem in EXCLUDED_STEMS


def list_core_files(core_dir: Path):
    """core_dir/{Public,Private} 以下の取り込み対象ファイルの相対パス (昇順)。"""
    out = []
    for sub in SUBDIRS:
        base = core_dir / sub
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file() and f.suffix in SOURCE_EXTS:
                rel = f.relative_to(core_dir)
                if not is_excluded(rel):
                    out.append(rel)
    return out


_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE = re.compile(r"//[^\n]*")


def normalized_hash(path: Path) -> str:
    """コメント (// と /* */)・空白・BOM を除去して SHA-256 (C# SyncGuard と同じ規則)。"""
    text = path.read_bytes().decode("utf-8", errors="replace").replace("﻿", "")
    text = _BLOCK.sub("", text)
    text = _LINE.sub("", text)
    text = re.sub(r"\s+", "", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
