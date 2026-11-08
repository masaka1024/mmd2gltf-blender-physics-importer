#!/usr/bin/env python3
"""import_vendor.py [ue5_core_dir] [vendor_dir]

UE5 プラグインの MmdPhysicsCore/{Public,Private} を native/vendor/MmdPhysicsCore へバイト単位でコピーする。
除外: Private/Tests/、MmdPhysicsCoreModule.cpp、MmdGlbMaterialReader.*、MmdScopedUtf8CType.*
vendor は無改変のコピー。差異は native/shim/ で吸収する。
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mmd_sync_common import SUBDIRS, list_core_files  # noqa: E402

NATIVE = Path(__file__).resolve().parent.parent
DEFAULT_SRC = (NATIVE.parent.parent / "mmd2gltf-ue5-physics-importer" / "Plugins"
               / "MmdPhysicsImporter" / "Source" / "MmdPhysicsCore")
DEFAULT_DST = NATIVE / "vendor" / "MmdPhysicsCore"


def main(argv):
    src = Path(argv[1]) if len(argv) > 1 else DEFAULT_SRC
    dst = Path(argv[2]) if len(argv) > 2 else DEFAULT_DST
    if not src.is_dir():
        print(f"取り込み元が無い: {src}", file=sys.stderr)
        return 1
    for sub in SUBDIRS:
        shutil.rmtree(dst / sub, ignore_errors=True)
    files = list_core_files(src)
    for rel in files:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / rel, dst / rel)
    print(f"{len(files)} ファイルを {dst} へ取り込んだ")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
