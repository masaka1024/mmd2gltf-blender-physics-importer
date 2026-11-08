#!/usr/bin/env python3
"""sync_check.py <ue5_core_dir> <vendor_dir>

UE5 プラグイン側の MmdPhysicsCore と native/vendor/MmdPhysicsCore を、
コメント・空白・BOM を除いた SHA-256 で比較する。
  一致            -> 終了コード 0
  不一致          -> 食い違ったファイル名を出して 1
  取り込み元なし  -> 警告して 0 (CI 等で片方しか無い環境)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mmd_sync_common import list_core_files, normalized_hash  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("usage: sync_check.py <ue5_core_dir> <vendor_dir>", file=sys.stderr)
        return 2
    src, ven = Path(argv[1]), Path(argv[2])
    if not src.is_dir():
        print(f"[sync_check] WARNING: 取り込み元が無い ({src})。検査を飛ばす。", file=sys.stderr)
        return 0

    src_files = set(list_core_files(src))
    ven_files = set(list_core_files(ven))
    problems = []
    for rel in sorted(src_files | ven_files):
        if rel not in ven_files:
            problems.append(f"{rel.as_posix()} が vendor に無い")
        elif rel not in src_files:
            problems.append(f"{rel.as_posix()} が取り込み元に無い")
        elif normalized_hash(src / rel) != normalized_hash(ven / rel):
            problems.append(f"{rel.as_posix()} が一致しない")
    if problems:
        print("[sync_check] vendor が取り込み元と同期していない:")
        for p in problems:
            print("  - " + p)
        print("  対処: python3 native/tools/import_vendor.py で取り込み直す (vendor は手で編集しない)。")
        return 1
    print(f"[sync_check] OK ({len(src_files)} ファイル)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
