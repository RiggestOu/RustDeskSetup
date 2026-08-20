#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 RustDesk 无人值守配置向导打包成单文件 exe（带管理员 manifest）。

用法:
    python build_exe.py
生成: dist/RustDeskSetup.exe

默认会用 PyInstaller 构建。若未安装，脚本会提示并自动尝试 pip install pyinstaller。
--uac-admin 会让生成的 exe 在启动时请求管理员权限（requestExecutionLevel=requireAdministrator），
无需再点工具内的『以管理员身份重启』按钮。
"""

import os
import sys
import subprocess
import shutil

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rustdesk_setup_wizard.py")
OUTPUT_NAME = "RustDeskSetup"


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa
        return True
    except ImportError:
        print("未检测到 PyInstaller，正在尝试安装…")
        rc = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"]).returncode
        return rc == 0


def build():
    if not os.path.exists(SCRIPT):
        print("找不到源文件:", SCRIPT)
        sys.exit(1)
    if not ensure_pyinstaller():
        print("PyInstaller 安装失败，请手动: pip install pyinstaller")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--uac-admin",            # 关键：请求管理员权限
        "--name", OUTPUT_NAME,
        "--clean",
        "--noconfirm",            # 避免 dist 已存在时交互卡住
        "--collect-all", "PySide6",  # 确保 Qt 平台插件(qwindows.dll)等被打包
        "--hidden-import", "PySide6.QtCore",
        "--hidden-import", "PySide6.QtWidgets",
        "--hidden-import", "PySide6.QtGui",
        SCRIPT,
    ]
    print("执行:", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc == 0:
        exe = os.path.join("dist", OUTPUT_NAME + ".exe")
        print("\n构建成功 ->", os.path.abspath(exe))
        print("可直接分发该 exe；运行时自动请求管理员权限。")
    else:
        print("\n构建失败，返回码", rc)
    sys.exit(rc)


if __name__ == "__main__":
    build()
