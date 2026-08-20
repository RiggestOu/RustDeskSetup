#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台构建脚本：根据运行平台用 PyInstaller 把 rustdesk_setup_wizard.py
打包成单文件可执行程序。

  - Windows : 输出 dist/RustDeskSetup.exe        (带 --uac-admin 管理员清单)
  - Linux   : 输出 dist/RustDeskSetup-Linux      (通用 x86_64 ELF)
  - macOS   : 输出 dist/RustDeskSetup-macOS      (Mach-O, 未签名)

用法: python build_app.py
GitHub Actions 中各平台 runner 直接调用本脚本即可。
"""
import sys
import subprocess

# 强制 stdout/stderr 使用 UTF-8：Windows CI runner 默认代码页为 cp1252，
# 打印中文(如“执行”)会抛 UnicodeEncodeError；本地中文 Windows(cp936) 不受影响。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

SCRIPT = "rustdesk_setup_wizard.py"

PLATFORM_NAMES = {
    "win32": "RustDeskSetup",
    "linux": "RustDeskSetup-Linux",
    "darwin": "RustDeskSetup-macOS",
}


def main():
    plat = sys.platform
    if plat not in PLATFORM_NAMES:
        print(f"[build_app] 未支持的平台: {plat}")
        sys.exit(1)

    name = PLATFORM_NAMES[plat]
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--clean",
        "--noconfirm",
        "--name", name,
        SCRIPT,
    ]
    # Windows 需要管理员清单(操作服务/写系统配置)
    if plat == "win32":
        cmd.append("--uac-admin")
    # PySide6 的 Qt 平台插件在 onefile 下容易被漏打，三平台统一强制收集
    cmd += ["--collect-all", "PySide6"]

    print("[build_app] 执行:", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print(f"[build_app] 构建失败, 返回码 {rc}")
        sys.exit(rc)
    ext = ".exe" if plat == "win32" else ""
    print(f"[build_app] 完成 -> dist/{name}{ext}")


if __name__ == "__main__":
    main()
