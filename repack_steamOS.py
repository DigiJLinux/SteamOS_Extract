#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repack_steamOS.py
Repack a SteamOS recovery superimage by replacing rootfs/var/home
from a directory tree previously extracted with img2dsk.

Works on Debian/Ubuntu, Fedora/RHEL/CentOS, Arch.

DEPENDENCIES:
  # Debian/Ubuntu
  sudo apt install -y util-linux rsync e2fsprogs file squashfs-tools
  # Fedora/RHEL
  sudo dnf install -y util-linux rsync e2fsprogs file squashfs-tools
  # Arch
  sudo pacman -S --needed util-linux rsync e2fsprogs file squashfs-tools

USAGE:
  sudo ./repack_steamOS.py \
      --old steamdeck.img \
      --root /mnt/steamOS \
      --out new_steamdeck.img
  # skip /var and/or /home partitions in the repack:
  sudo ./repack_steamOS.py --old steamdeck.img --root /mnt/steamOS --out new.img --no-var
  sudo ./repack_steamOS.py --old steamdeck.img --root /mnt/steamOS --out new.img --no-home --no-var
"""

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------- shell helpers ----------

def sh(cmd, check=True, capture=True):
    """Run shell command; text mode; optionally capture output."""
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def is_squashfs(path: Path) -> bool:
    try:
        out = sh(["file", "-b", str(path)]).stdout.lower()
        return "squashfs" in out
    except Exception:
        return False

def is_ext_image(path: Path) -> bool:
    try:
        out = sh(["file", "-b", str(path)]).stdout.lower()
        return any(t in out for t in ("ext2", "ext3", "ext4"))
    except Exception:
        return False

def mount_rw(dev_or_img: str, target: Path, fstype: str | None = None, loop: bool = False):
    ensure_dir(target)
    opts = "rw,loop" if loop else "rw"
    cmd = ["mount", "-o", opts]
    if fstype:
        cmd += ["-t", fstype]
    cmd += [dev_or_img, str(target)]
    sh(cmd)

def umount(target: Path):
    subprocess.run(["umount", str(target)], check=False)

def du_bytes(path: Path) -> int:
    """Fast directory size using du; fallback to Python walk."""
    try:
        out = sh(["du", "-sb", str(path)]).stdout.strip().split()[0]
        return int(out)
    except Exception:
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except Exception:
                    pass
        return total

def round_up(n: int, block: int) -> int:
    return ((n + block - 1) // block) * block

# ---------- image builders ----------

def build_squashfs(src_dir: Path, out_file: Path):
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(out_file.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    sh(["mksquashfs", str(src_dir), str(tmp), "-comp", "xz", "-noappend"])
    tmp.rename(out_file)

def build_ext4_image(src_dir: Path, out_file: Path, label: str = ""):
    """Create an ext4 filesystem image and populate it from src_dir."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    # size: data + 15% overhead + 64MiB padding, min 256MiB, round to 64MiB
    data = du_bytes(src_dir)
    size = data + data // 6 + (64 << 20)
    size = max(size, 256 << 20)
    size = round_up(size, 64 << 20)

    tmp = out_file.with_suffix(out_file.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    # allocate, mkfs, mount loop, rsync, tune
    with open(tmp, "wb") as f:
        f.truncate(size)

    mkfs_cmd = ["mkfs.ext4", "-F", "-E", "lazy_itable_init=0,lazy_journal_init=0"]
    if label:
        mkfs_cmd += ["-L", label]
    mkfs_cmd.append(str(tmp))
    sh(mkfs_cmd)
    # set 0% reserved
    sh(["tune2fs", "-m", "0", str(tmp)], check=False)

    mnt = Path(tempfile.mkdtemp(prefix="extimg_"))
    try:
        mount_rw(str(tmp), mnt, loop=True)
        # rsync (preserve xattrs/ACLs/ids)
        sh(["rsync", "-aAXH", "--numeric-ids", f"{src_dir}/", f"{mnt}/"])
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

    tmp.rename(out_file)

# ---------- replacement helpers ----------

def replace_nested_image_in_partition(part_dev: str, name_candidates: list[str],
                                      new_inner: Path) -> None:
    """
    Mount partition RW, replace the nested image file by name.
    If not found but partition empty => just place new file using the first candidate name.
    """
    mnt = Path(tempfile.mkdtemp(prefix="partmnt_"))
    try:
        mount_rw(part_dev, mnt)  # auto fstype
        # Find existing nested filename (or choose first candidate)
        target_path = None
        for nm in name_candidates:
            cand = mnt / nm
            if cand.exists() and cand.is_file():
                target_path = cand
                break
        if target_path is None:
            # Fallback: pick any single large file present
            files = [p for p in mnt.iterdir() if p.is_file()]
            if files:
                # replace the largest file
                target_path = max(files, key=lambda p: p.stat().st_size)
            else:
                # empty? then create with the first candidate name
                target_path = mnt / name_candidates[0]

        # Ensure enough free space
        try:
            st = os.statvfs(mnt)
            free_bytes = st.f_bavail * st.f_frsize
            need = new_inner.stat().st_size
            if need > free_bytes + (target_path.stat().st_size if target_path.exists() else 0):
                raise RuntimeError(f"Not enough free space in {part_dev} to place {new_inner.name}")
        except Exception:
            # best-effort; if statvfs fails we try copy anyway
            pass

        # Copy atomically
        tmp = target_path.with_suffix(target_path.suffix + ".tmp")
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(new_inner, tmp)
        os.replace(tmp, target_path)
        # sync to be safe
        sh(["sync"], check=False)
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

def wipe_and_fill_partition_direct(part_dev: str, src_dir: Path):
    """
    If the partition itself is the filesystem (no nested image), replace its contents
    by rsyncing the new tree directly into it.
    """
    mnt = Path(tempfile.mkdtemp(prefix="partmnt_"))
    try:
        mount_rw(part_dev, mnt)
        # Remove everything except lost+found
        for child in mnt.iterdir():
            if child.name == "lost+found":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try: child.unlink()
                except Exception: pass
        # Copy in new tree
        sh(["rsync", "-aAXH", "--numeric-ids", f"{src_dir}/", f"{mnt}/"])
        sh(["sync"], check=False)
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

# ---------- detection ----------

def detect_existing_inner(part_dev: str, preferred_names: list[str]) -> tuple[Path | None, bool]:
    """
    Mount partition RO and try to find an existing nested image file.
    Returns (path, is_squashfs) or (None, False) if partition seems to be direct fs.
    """
    mnt = Path(tempfile.mkdtemp(prefix="detmnt_"))
    path = None
    sq = False
    try:
        mount_rw(part_dev, mnt)  # mount RW just for simplicity
        # Try preferred names
        for nm in preferred_names:
            cand = mnt / nm
            if cand.exists() and cand.is_file():
                path = cand
                sq = is_squashfs(cand)
                return (path, sq)
        # Fallback: pick largest file and probe
        files = [p for p in mnt.iterdir() if p.is_file()]
        if files:
            cand = max(files, key=lambda p: p.stat().st_size)
            if is_squashfs(cand) or is_ext_image(cand):
                path = cand
                sq = is_squashfs(cand)
                return (path, sq)
        return (None, False)
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

# ---------- main repack ----------

def repack(old_img: Path, root_tree: Path, out_img: Path, include_var: bool, include_home: bool):
    if not old_img.exists():
        raise FileNotFoundError(f"Old superimage not found: {old_img}")
    if not root_tree.exists():
        raise FileNotFoundError(f"Root tree not found: {root_tree}")

    workdir = Path(tempfile.mkdtemp(prefix="repack_"))
    mounts = []
    loops = []

    def cleanup():
        for m in reversed(mounts):
            try: umount(m)
            except Exception: pass
        for ld in reversed(loops):
            try: sh(["losetup", "-d", ld], check=False)
            except Exception: pass
        shutil.rmtree(workdir, ignore_errors=True)
    atexit.register(cleanup)

    # 1) Copy the old superimage to the new output (preserve GPT + ESP/EFI partitions as-is)
    print(f"[+] Copying base image:\n    {old_img} → {out_img}")
    shutil.copy2(old_img, out_img)

    # 2) Attach the NEW image and expose partitions
    loopdev = sh(["losetup", "--find", "--show", "-P", str(out_img)]).stdout.strip()
    if not loopdev:
        raise RuntimeError("Failed to attach loop device for output image")
    loops.append(loopdev)

    # According to your layout:
    p3 = f"{loopdev}p3"  # rootfs-A
    p4 = f"{loopdev}p4"  # var-A
    p5 = f"{loopdev}p5"  # home

    # 3) REPLACE ROOTFS (partition 3)
    print("[+] Replacing rootfs ...")
    preferred_root_names = ["rootfs-A.img", "rootfs.img", "rootfs.squashfs", "filesystem.squashfs", "arch.squashfs"]
    exists_path, was_squashfs = detect_existing_inner(p3, preferred_root_names)

    # Build a new inner rootfs from the tree on disk:
    tmpdir = workdir / "build"
    ensure_dir(tmpdir)
    new_root_inner = tmpdir / (exists_path.name if exists_path else ("rootfs.squashfs"))  # default name
    if was_squashfs or (exists_path and is_squashfs(exists_path)):
        print("    - Detected squashfs root; rebuilding squashfs ...")
        build_squashfs(root_tree, new_root_inner)
    else:
        print("    - Using ext4 image for root; building ext4 ...")
        # If there is an existing ext image, keep its name; else default
        if exists_path and is_ext_image(exists_path):
            new_root_inner = tmpdir / exists_path.name
        else:
            new_root_inner = tmpdir / "rootfs-A.img"
        build_ext4_image(root_tree, new_root_inner, label="rootfs-A")

    # Place new inner into partition (or overwrite partition content if direct)
    if exists_path:
        replace_nested_image_in_partition(p3, preferred_root_names, new_root_inner)
    else:
        # No nested image previously -> the partition itself is the rootfs; wipe & fill
        print("    - Partition appears to be direct rootfs; replacing contents directly ...")
        wipe_and_fill_partition_direct(p3, root_tree)

    # 4) REPLACE VAR (partition 4)
    if include_var:
        var_src = root_tree / "var"
        if not var_src.exists():
            print("    - WARNING: /var tree not found in root tree; skipping var")
        else:
            print("[+] Replacing /var ...")
            preferred_var_names = ["var-A.img", "var.img"]
            v_exists, _ = detect_existing_inner(p4, preferred_var_names)
            new_var_inner = tmpdir / (v_exists.name if v_exists else "var-A.img")
            build_ext4_image(var_src, new_var_inner, label="var-A")
            if v_exists:
                replace_nested_image_in_partition(p4, preferred_var_names, new_var_inner)
            else:
                wipe_and_fill_partition_direct(p4, var_src)
    else:
        print("[+] Skipping /var (per --no-var)")

    # 5) REPLACE HOME (partition 5)
    if include_home:
        home_src = root_tree / "home"
        if not home_src.exists():
            print("    - WARNING: /home tree not found; skipping home")
        else:
            print("[+] Replacing /home ...")
            preferred_home_names = ["home.img"]
            h_exists, _ = detect_existing_inner(p5, preferred_home_names)
            new_home_inner = tmpdir / (h_exists.name if h_exists else "home.img")
            build_ext4_image(home_src, new_home_inner, label="home")
            if h_exists:
                replace_nested_image_in_partition(p5, preferred_home_names, new_home_inner)
            else:
                wipe_and_fill_partition_direct(p5, home_src)
    else:
        print("[+] Skipping /home (per --no-home)")

    # 6) Flush and detach
    print("[+] Sync and detach ...")
    sh(["sync"], check=False)
    # losetup -d handled by atexit cleanup
    print(f"[✓] Repack complete:\n    {out_img}")
    print("NOTE: ESP/EFI partitions were kept as-is from the old superimage.")

# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description="Repack a SteamOS/Steam-Deck style superimage with new rootfs/var/home.")
    ap.add_argument("--old", required=True, type=Path, help="Path to the old/original superimage (.img)")
    ap.add_argument("--root", required=True, type=Path, help="Path to extracted filesystem tree from img2dsk")
    ap.add_argument("--out", required=True, type=Path, help="Path to write the new superimage (.img)")
    ap.add_argument("--no-var", action="store_true", help="Do not include/replace the var partition (keep old)")
    ap.add_argument("--no-home", action="store_true", help="Do not include/replace the home partition (keep old)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("Please run as root (sudo).", file=sys.stderr)
        sys.exit(1)

    try:
        repack(args.old, args.root, args.out, include_var=not args.no_var, include_home=not args.no_home)
    except subprocess.CalledProcessError as e:
        sys.stderr.write((e.stderr or e.stdout or str(e)) + "\n")
        sys.exit(e.returncode)
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
