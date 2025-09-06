#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
img2dsk.py
Extract the Linux filesystem from a Steam-Deck/“superimage” disk image
into a normal directory — including /var and /home.

USAGE:
  sudo img2dsk.py /path/to/steamdeck.img /mnt/steamOS

RESULT:
  /mnt/steamOS/        <- root filesystem
  /mnt/steamOS/var/    <- var from var-A
  /mnt/steamOS/home/   <- home from home.img

REQUIREMENTS (Ubuntu/Debian):
  sudo apt install util-linux rsync squashfs-tools file
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------- tiny helpers ----------

def run(cmd, check=True, capture=True):
    return subprocess.run(cmd, check=check, text=True,
                          capture_output=capture)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def is_squashfs(path: Path) -> bool:
    try:
        out = run(["file", "-b", str(path)]).stdout.lower()
        return "squashfs" in out
    except Exception:
        return False

def is_ext_image(path: Path) -> bool:
    try:
        out = run(["file", "-b", str(path)]).stdout.lower()
        return "ext2" in out or "ext3" in out or "ext4" in out
    except Exception:
        return False

def mount_ro(dev_or_img: str, target: Path, fstype: str|None=None, loop: bool=False):
    ensure_dir(target)
    opts = "ro,loop" if loop else "ro"
    cmd = ["mount", "-o", opts]
    if fstype:
        cmd += ["-t", fstype]
    cmd += [dev_or_img, str(target)]
    run(cmd)

def umount(path: Path):
    subprocess.run(["umount", str(path)], check=False)

# ---------- extraction logic ----------

def extract_rootfs_var_home(superimg: Path, outdir: Path):
    work = Path(tempfile.mkdtemp(prefix="img2dsk_"))
    mounts: list[Path] = []
    loops: list[str] = []

    def cleanup():
        # unmount in reverse order
        for m in reversed(mounts):
            try: umount(m)
            except Exception: pass
        # detach loops
        for ld in reversed(loops):
            try: subprocess.run(["losetup", "-d", ld], check=False)
            except Exception: pass
        shutil.rmtree(work, ignore_errors=True)
    atexit.register(cleanup)

    ensure_dir(outdir)

    # 1) Attach superimage with partition scanning
    print(f"[+] Attaching superimage: {superimg}")
    loopdev = run(["losetup", "--find", "--show", "-P", str(superimg)]).stdout.strip()
    if not loopdev:
        raise RuntimeError("Failed to attach loop device for superimage")
    loops.append(loopdev)

    # Partitions (by your layout)
    p3 = f"{loopdev}p3"  # rootfs-A
    p4 = f"{loopdev}p4"  # var-A
    p5 = f"{loopdev}p5"  # home

    # ---------- ROOTFS ----------
    print("[+] Extracting root filesystem…")
    root_part_mnt = work / "root_part"
    mount_ro(p3, root_part_mnt)         # mount the partition contents ro
    mounts.append(root_part_mnt)

    # Heuristic: prefer common names; else take largest file; else rsync the partition itself
    preferred = ["rootfs-A.img", "rootfs.img", "rootfs.squashfs", "filesystem.squashfs", "arch.squashfs"]
    inner_root = None
    for name in preferred:
        c = root_part_mnt / name
        if c.is_file():
            inner_root = c
            break
    if inner_root is None:
        files = [p for p in root_part_mnt.iterdir() if p.is_file()]
        inner_root = max(files, key=lambda x: x.stat().st_size) if files else None

    if inner_root and is_squashfs(inner_root):
        print(f"    - Found squashfs: {inner_root.name} → unsquashing into {outdir}")
        run(["unsquashfs", "-f", "-d", str(outdir), str(inner_root)], check=True)
    elif inner_root and is_ext_image(inner_root):
        print(f"    - Found ext image: {inner_root.name} → mounting and rsyncing")
        root_inner_mnt = work / "root_inner"
        mount_ro(str(inner_root), root_inner_mnt, loop=True)
        mounts.append(root_inner_mnt)
        run(["rsync", "-aAXH", "--numeric-ids", f"{root_inner_mnt}/", f"{outdir}/"], check=True)
    else:
        # maybe the partition itself is the root filesystem (ext4)
        print("    - No nested image detected; rsyncing partition contents")
        run(["rsync", "-aAXH", "--numeric-ids", f"{root_part_mnt}/", f"{outdir}/"], check=True)

    # ---------- VAR ----------
    print("[+] Extracting /var …")
    extract_into_subdir(p4, outdir, "var", work, mounts)

    # ---------- HOME ----------
    print("[+] Extracting /home …")
    extract_into_subdir(p5, outdir, "home", work, mounts)

    print(f"[✓] Done. Files extracted into: {outdir}")
    print("    (EFI partitions were ignored; this is the Linux filesystem tree.)")

def extract_into_subdir(dev: str, outdir: Path, sub: str, work: Path, mounts: li
