# -*- coding: utf-8 -*-
"""
简易 Windows 安装器：解压 PyInstaller onedir 产物，创建桌面和开始菜单快捷方式。
"""

import ctypes
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP_NAME = "Desktop interaction"
EXE_NAME = "Desktop interaction.exe"
ZIP_NAME = "Desktop interaction.zip"


def _message(title: str, text: str, flags: int = 0x40):
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def _resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _create_shortcut(shortcut_path: Path, target_path: Path, working_dir: Path, icon_path: Path):
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    script = "\n".join(
        [
            "$shell = New-Object -ComObject WScript.Shell",
            f"$shortcut = $shell.CreateShortcut({_powershell_quote(str(shortcut_path))})",
            f"$shortcut.TargetPath = {_powershell_quote(str(target_path))}",
            f"$shortcut.WorkingDirectory = {_powershell_quote(str(working_dir))}",
            f"$shortcut.IconLocation = {_powershell_quote(str(icon_path))}",
            "$shortcut.Save()",
        ]
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main():
    zip_path = _resource_path(ZIP_NAME)
    if not zip_path.exists():
        raise FileNotFoundError(f"未找到安装资源：{zip_path}")

    install_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / APP_NAME
    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(install_dir)

    target = install_dir / EXE_NAME
    if not target.exists():
        raise FileNotFoundError(f"安装后未找到程序：{target}")

    # PyInstaller onedir 资源位于 _internal 下；兼容旧的平铺布局
    icon_candidates = [
        install_dir / "_internal" / "assets" / "image" / "Redstone.ico",
        install_dir / "assets" / "image" / "Redstone.ico",
    ]
    icon = next((p for p in icon_candidates if p.exists()), target)

    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / f"{APP_NAME}.lnk"
    start_menu = (
        Path(os.environ.get("APPDATA", str(Path.home())))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / f"{APP_NAME}.lnk"
    )

    _create_shortcut(desktop, target, install_dir, icon)
    _create_shortcut(start_menu, target, install_dir, icon)

    if _message("安装完成", f"{APP_NAME} 已安装到：\n{install_dir}\n\n是否立即启动？", 0x24) == 6:
        subprocess.Popen([str(target)], cwd=str(install_dir))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _message("安装失败", str(exc), 0x10)
        raise
