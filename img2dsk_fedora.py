#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
img2dsk_fedora.py 
Extract a SteamOS superimage into a normal
directory tree on Fedora / RHEL / CentOS — including rootfs, /var, and /home — via
either CLI or a Tkinter GUI (--gui).

Dependencies (Fedora / RHEL / CentOS Stream):
  sudo dnf install -y util-linux rsync squashfs-tools file python3-tkinter

USAGE (CLI):
  sudo ./img2dsk_fedora.py --image steamdeck.img --out /mnt/steamOS
  # Defaults: include /var and /home
  sudo ./img2dsk_fedora.py --image steamdeck.img --out /mnt/steamOS --no-var
  sudo ./img2dsk_fedora.py --image steamdeck.img --out /mnt/steamOS --no-home

USAGE (GUI):
  sudo ./img2dsk_fedora.py --gui

Layout assumptions (typical):
  p1: esp.img (FAT32), p2: efi-A.fat (FAT32),
  p3: rootfs-A (contains rootfs-A.img or squashfs/ext4),
  p4: var-A (nested var-A.img or ext4),
  p5: home  (nested home.img or ext4)
"""

import os
import sys
import atexit
import shutil
import tempfile
import threading
import subprocess
from pathlib import Path
from dataclasses import dataclass

# -------------------- helpers --------------------

def run(cmd, check=True, capture=True):
    """Run a command; text mode. Return CompletedProcess."""
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)

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
        return any(tag in out for tag in ("ext2", "ext3", "ext4"))
    except Exception:
        return False

def mount_ro(dev_or_img: str, target: Path, fstype: str | None = None, loop: bool = False):
    ensure_dir(target)
    opts = "ro,loop" if loop else "ro"
    cmd = ["mount", "-o", opts]
    if fstype:
        cmd += ["-t", fstype]
    cmd += [dev_or_img, str(target)]
    run(cmd)

def umount(path: Path):
    subprocess.run(["umount", str(path)], check=False)

# -------------------- core extraction --------------------

@dataclass
class ExtractOptions:
    include_var: bool = True
    include_home: bool = True

class SuperimageExtractor:
    def __init__(self, log_fn=print, progress_fn=None):
        self.log = log_fn
        self.set_progress = progress_fn or (lambda _pct: None)
        self.mounts: list[Path] = []
        self.loops: list[str] = []
        self.work = Path(tempfile.mkdtemp(prefix="img2dsk_"))
        atexit.register(self.cleanup)

    def cleanup(self):
        for m in reversed(self.mounts):
            try:
                umount(m)
            except Exception:
                pass
        for ld in reversed(self.loops):
            try:
                subprocess.run(["losetup", "-d", ld], check=False)
            except Exception:
                pass
        try:
            shutil.rmtree(self.work, ignore_errors=True)
        except Exception:
            pass

    def _extract_into_subdir(self, dev: str, outdir: Path, sub: str):
        subdir = outdir / sub
        ensure_dir(subdir)

        part_mnt = self.work / f"{sub}_part"
        mount_ro(dev, part_mnt)
        self.mounts.append(part_mnt)

        files = sorted([p for p in part_mnt.iterdir() if p.is_file()],
                       key=lambda x: x.stat().st_size, reverse=True)

        if files and is_ext_image(files[0]):
            inner = files[0]
            inner_mnt = self.work / f"{sub}_inner"
            mount_ro(str(inner), inner_mnt, loop=True)
            self.mounts.append(inner_mnt)
            self.log(f"  rsync → {subdir} (from {inner.name})")
            run(["rsync", "-aAXH", "--numeric-ids", f"{inner_mnt}/", f"{subdir}/"], check=True)
        else:
            self.log(f"  rsync → {subdir} (from partition)")
            run(["rsync", "-aAXH", "--numeric-ids", f"{part_mnt}/", f"{subdir}/"], check=True)

    def extract_all(self, superimg: Path, outdir: Path, opts: ExtractOptions):
        self.set_progress(0)
        self.log(f"[+] Attaching superimage: {superimg}")
        loopdev = run(["losetup", "--find", "--show", "-P", str(superimg)]).stdout.strip()
        if not loopdev:
            raise RuntimeError("Failed to attach loop device for superimage")
        self.loops.append(loopdev)

        # Partitions aligned with your layout
        p3 = f"{loopdev}p3"  # rootfs-A
        p4 = f"{loopdev}p4"  # var-A
        p5 = f"{loopdev}p5"  # home

        ensure_dir(outdir)

        # ----- ROOTFS -----
        self.set_progress(10)
        self.log("[+] Extracting root filesystem…")
        root_part_mnt = self.work / "root_part"
        mount_ro(p3, root_part_mnt)
        self.mounts.append(root_part_mnt)

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
            self.log(f"  unsquashfs → {outdir} (from {inner_root.name})")
            run(["unsquashfs", "-f", "-d", str(outdir), str(inner_root)], check=True)
        elif inner_root and is_ext_image(inner_root):
            root_inner_mnt = self.work / "root_inner"
            mount_ro(str(inner_root), root_inner_mnt, loop=True)
            self.mounts.append(root_inner_mnt)
            self.log(f"  rsync → {outdir} (from {inner_root.name})")
            run(["rsync", "-aAXH", "--numeric-ids", f"{root_inner_mnt}/", f"{outdir}/"], check=True)
        else:
            self.log("  rsync → root (from partition)")
            run(["rsync", "-aAXH", "--numeric-ids", f"{root_part_mnt}/", f"{outdir}/"], check=True)

        # ----- VAR -----
        if opts.include_var:
            self.set_progress(55)
            self.log("[+] Extracting /var …")
            self._extract_into_subdir(p4, outdir, "var")

        # ----- HOME -----
        if opts.include_home:
            self.set_progress(80)
            self.log("[+] Extracting /home …")
            self._extract_into_subdir(p5, outdir, "home")

        self.set_progress(100)
        self.log(f"[✓] Done. Files extracted into: {outdir}")
        self.log("    (EFI partitions ignored by design.)")

# -------------------- CLI + GUI --------------------

def run_cli(args):
    if os.geteuid() != 0:
        print("Please run as root (sudo).", file=sys.stderr)
        sys.exit(1)
    img = Path(args.image).resolve()
    out = Path(args.out).resolve()
    if not img.exists():
        print(f"Image not found: {img}", file=sys.stderr)
        sys.exit(2)

    opts = ExtractOptions(include_var=not args.no_var, include_home=not args.no_home)
    ex = SuperimageExtractor()
    try:
        ex.extract_all(img, out, opts)
    except subprocess.CalledProcessError as e:
        sys.stderr.write((e.stderr or e.stdout or str(e)) + "\n")
        sys.exit(e.returncode)
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, N, S, E, W
    from tkinter.ttk import Frame, Label, Entry, Button, Progressbar, Separator

    class App:
        def __init__(self, root: tk.Tk):
            root.title("img2dsk (Fedora/RHEL) — Superimage → Directory (rootfs + var + home)")
            root.minsize(760, 300)

            self.image_path = tk.StringVar()
            self.output_dir = tk.StringVar()
            self.log_var = tk.StringVar(value="")

            frm = Frame(root, padding=12)
            frm.grid(row=0, column=0, sticky=N+S+E+W)
            for i in range(3):
                frm.columnconfigure(i, weight=1 if i == 1 else 0)

            r = 0
            Label(frm, text="Superimage (.img):").grid(row=r, column=0, sticky=E, padx=6, pady=6)
            Entry(frm, textvariable=self.image_path).grid(row=r, column=1, sticky=E+W, padx=6, pady=6)
            Button(frm, text="Browse", command=self.pick_img).grid(row=r, column=2, sticky=W, padx=6, pady=6)

            r += 1
            Label(frm, text="Output directory:").grid(row=r, column=0, sticky=E, padx=6, pady=6)
            Entry(frm, textvariable=self.output_dir).grid(row=r, column=1, sticky=E+W, padx=6, pady=6)
            Button(frm, text="Browse", command=self.pick_out).grid(row=r, column=2, sticky=W, padx=6, pady=6)

            r += 1
            Separator(frm).grid(row=r, column=0, columnspan=3, sticky=E+W, pady=6)

            r += 1
            Button(frm, text="Extract", command=self.start).grid(row=r, column=0, sticky=E+W, padx=6, pady=8)
            Button(frm, text="Quit", command=root.quit).grid(row=r, column=2, sticky=E+W, padx=6, pady=8)

            r += 1
            self.pb = Progressbar(frm, maximum=100)
            self.pb.grid(row=r, column=0, columnspan=3, sticky=E+W, padx=6, pady=6)

            r += 1
            Label(frm, text="Log:").grid(row=r, column=0, sticky=W, padx=6)
            r += 1
            self.log_label = Label(frm, textvariable=self.log_var, anchor="w", justify="left")
            self.log_label.grid(row=r, column=0, columnspan=3, sticky=E+W, padx=6)

            self.root = root
            self.worker = None
            self.extractor = None

            if os.geteuid() != 0:
                messagebox.showerror("Root required", "Please run this program with sudo (root).")

        def pick_img(self):
            p = filedialog.askopenfilename(title="Select superimage (.img)",
                                           filetypes=[("Disk images", "*.img"), ("All files", "*.*")])
            if p:
                self.image_path.set(p)

        def pick_out(self):
            p = filedialog.askdirectory(title="Select output directory")
            if p:
                self.output_dir.set(p)

        def append_log(self, line: str):
            prev = self.log_var.get()
            new = (prev + ("\n" if prev else "") + line)[-25000:]
            self.log_var.set(new)
            self.root.update_idletasks()

        def set_progress(self, pct: int):
            self.pb["value"] = max(0, min(100, pct))
            self.root.update_idletasks()

        def start(self):
            img = Path(self.image_path.get().strip()) if self.image_path.get().strip() else None
            out = Path(self.output_dir.get().strip()) if self.output_dir.get().strip() else None
            if not img or not img.exists():
                messagebox.showerror("Missing image", "Please choose a valid .img file.")
                return
            if not out:
                messagebox.showerror("Missing output", "Please choose an output directory.")
                return

            self.extractor = SuperimageExtractor(self.append_log, self.set_progress)
            self.worker = threading.Thread(target=self._do_extract, args=(img, out), daemon=True)
            self.worker.start()

        def _do_extract(self, img: Path, out: Path):
            try:
                opts = ExtractOptions(include_var=True, include_home=True)
                self.extractor.extract_all(img, out, opts)
                messagebox.showinfo("Done", f"Extraction complete:\n{out}")
            except subprocess.CalledProcessError as e:
                err = e.stderr or e.stdout or str(e)
                self.append_log(err.strip())
                messagebox.showerror("Error", err)
            except Exception as e:
                self.append_log(str(e))
                messagebox.showerror("Error", str(e))

    root = tk.Tk()
    App(root)
    root.mainloop()

# -------------------- entrypoint --------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract a superimage (.img) into a directory (rootfs + /var + /home). Fedora / RHEL."
    )
    parser.add_argument("--gui", action="store_true", help="Launch the Tkinter GUI")
    parser.add_argument("--image", type=str, help="Path to superimage (.img)")
    parser.add_argument("--out", type=str, help="Destination directory")
    # Defaults include var/home; allow skipping with --no-var / --no-home
    try:
        # Python 3.9+: BooleanOptionalAction available
        parser.add_argument("--no-var", action=argparse.BooleanOptionalAction, default=False,
                            help="Skip extracting /var (default: include)")
        parser.add_argument("--no-home", action=argparse.BooleanOptionalAction, default=False,
                            help="Skip extracting /home (default: include)")
    except AttributeError:
        # Fallback for older Python: use simple flags
        parser.add_argument("--no-var", action="store_true", help="Skip extracting /var")
        parser.add_argument("--no-home", action="store_true", help="Skip extracting /home")

    args = parser.parse_args()

    if args.gui:
        run_gui()
        return

    if not args.image or not args.out:
        print("Usage (CLI): sudo ./img2dsk_fedora.py --image steamdeck.img --out /mnt/steamOS [--no-var] [--no-home]", file=sys.stderr)
        print("Or launch GUI: sudo ./img2dsk_fedora.py --gui", file=sys.stderr)
        sys.exit(2)

    run_cli(args)

if __name__ == "__main__":
    main()
