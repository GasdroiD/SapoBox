"""Gera o .deb do SapoBox (amd64). Usa o Python e o GTK 4 / libadwaita do sistema."""
import hashlib
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "sapobox" / "__init__.py").read_text()).group(1)

DESKTOPS = (
    ("org.sapobox.SapoBox", "SapoBox", "Caixa de ferramentas", "sapobox", "Utility;", "Conversor;Extrator;Downloader;"),
    ("org.sapobox.Converter", "SapoBox Conversor", "Conversor de jogos", "sapobox converter", "Utility;",
     "FF-PFSC;LZ4;exFAT;APR;"),
    ("org.sapobox.Extrator", "SapoBox Extrator", "Extrator de 7z, ZIP e RAR", "sapobox extrator %f",
     "Utility;Archiving;", "7z;zip;rar;extrair;"),
    ("org.sapobox.Downloader", "SapoBox Downloader", "Gerenciador de downloads", "sapobox downloader",
     "Network;FileTransfer;", "download;baixar;"),
)


def main():
    if platform.machine() not in ("x86_64", "amd64"):
        raise SystemExit("Este pacote é para amd64.")
    subprocess.run(["make", "-C", str(ROOT), "all"], check=True)
    stage = ROOT / "build" / "package"
    if stage.exists():
        shutil.rmtree(stage)
    opt = stage / "opt" / "sapobox"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "sapobox", opt / "sapobox", ignore=ignore)
    shutil.copytree(ROOT / "vendor" / "7zip", opt / "vendor" / "7zip")
    shutil.copytree(ROOT / "packaging" / "icons", opt / "packaging" / "icons")
    shutil.copytree(ROOT / "extensao", opt / "extensao")
    (opt / "runtime").mkdir()
    sys.path.insert(0, str(ROOT))
    from sapobox.converter.lz4 import PACKED_RUNTIME_SHA256
    runtime = ROOT / "runtime" / "libSceAmpr.sprx"
    if runtime.is_file():
        if hashlib.sha256(runtime.read_bytes()).hexdigest() != PACKED_RUNTIME_SHA256:
            raise SystemExit("O runtime local não corresponde à versão PackedStdio exigida.")
        shutil.copy2(runtime, opt / "runtime" / runtime.name)

    bin_dir = stage / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    launcher = bin_dir / "sapobox"
    launcher.write_text('#!/bin/sh\nexport PYTHONPATH=/opt/sapobox\nexec /usr/bin/python3 -m sapobox "$@"\n')
    launcher.chmod(0o755)

    apps = stage / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    for app_id, name, comment, exec_, cats, keys in DESKTOPS:
        (apps / f"{app_id}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\nComment={comment}\nExec={exec_}\n"
            f"Icon={app_id}\nTerminal=false\nCategories={cats}\nKeywords={keys}\nStartupNotify=true\n"
            f"StartupWMClass={app_id}\n")
    icons = stage / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icons.mkdir(parents=True)
    for svg in (ROOT / "packaging" / "icons" / "hicolor" / "scalable" / "apps").glob("*.svg"):
        shutil.copy2(svg, icons / svg.name)

    meta = stage / "usr" / "share" / "metainfo"
    meta.mkdir(parents=True)
    shutil.copy2(ROOT / "packaging" / "metainfo" / "org.sapobox.SapoBox.metainfo.xml", meta)
    for png in (ROOT / "packaging" / "icons" / "hicolor").glob("*x*/apps/*.png"):
        dest_dir = stage / "usr" / "share" / "icons" / "hicolor" / png.parent.parent.name / "apps"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(png, dest_dir / png.name)
    shots = stage / "usr" / "share" / "doc" / "sapobox" / "screenshots"
    shutil.copytree(ROOT / "docs" / "screenshots", shots)
    docs = stage / "usr" / "share" / "doc" / "sapobox"
    docs.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "README.en.md", "NOTICE.md", "NOTICE.en.md", "LICENSE"):
        shutil.copy2(ROOT / name, docs / name)
    shutil.copy2(ROOT / "vendor" / "7zip" / "unRarLicense.txt", docs / "unRarLicense.txt")
    shutil.copy2(ROOT / "vendor" / "7zip" / "copyright", docs / "7zip-copyright")
    shutil.copy2(ROOT / "vendor" / "converter" / "lz4" / "LICENSE", docs / "LZ4-LICENSE")

    size = (sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) + 1023) // 1024
    debian = stage / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(f"""Package: sapobox
Version: {VERSION}
Section: utils
Priority: optional
Architecture: amd64
Installed-Size: {size}
Maintainer: SapoBox <noreply@example.org>
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.7), libc6 (>= 2.36), libstdc++6, libgcc-s1, zlib1g
Recommends: python3-fusepy, fuse3
Description: SapoBox - conversor de jogos, extrator de arquivos e downloader
 Reune em um so aplicativo GTK 4 / libadwaita: o Conversor (pasta ou imagem
 exFAT para FF-PFSC ou LZ4), o Extrator (7z, ZIP e RAR, com partes e senha) e
 o Downloader (fila, pausa e retomada). Inclui o 7-Zip com decodificador RAR.
""")
    for script, body in (("postinst", "gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true\n"
                          "update-desktop-database -q 2>/dev/null || true\n"),):
        path = debian / script
        path.write_text("#!/bin/sh\nset -e\nif [ \"$1\" = configure ]; then\n" + body + "fi\nexit 0\n")
        path.chmod(0o755)
    dest = ROOT / "dist"
    dest.mkdir(exist_ok=True)
    out = dest / f"sapobox_{VERSION}_amd64.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(stage), str(out)], check=True)
    print(out)


if __name__ == "__main__":
    main()
