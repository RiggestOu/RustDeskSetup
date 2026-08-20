#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RustDesk 无人值守访问配置工具（跨平台：Windows / Linux / macOS）
================================================================
只做官方客户端/安装包不具备的两件事，让"重启后也能看到并操作锁屏/登录界面"成立：
  ① 确保 RustDesk 服务已安装、开机自启并正在运行（锁屏前就要起来抓取登录界面）
  ② 启用『允许登录屏幕密码』（allow-logon-screen-password，受控端 ≥1.3.1）

自动寻找本机 RustDesk 安装路径（Windows: 服务 ImagePath / 注册表 / 常见目录 / PATH；
Linux: /usr/bin/rustdesk 等 / PATH；macOS: /Applications/RustDesk.app / PATH）。

服务管理按平台分派：
  - Windows : sc / net（服务名 RustDesk）
  - Linux   : systemd（单元 rustdesk.service，以 root 运行，读 /root/.config/rustdesk）
  - macOS   : launchd（/Library/LaunchDaemons/com.carriez.rustdesk_service.plist）

平台差异提示：
  - macOS 还需在系统设置中手动授予 RustDesk「屏幕录制 / 辅助功能」权限，
    且未签名应用会被 Gatekeeper 拦截（需允许或签名公证）。
  - Linux 在 Wayland 登录界面下暂无法抓取（RustDesk 已知限制），X11 或虚拟显示可行。

永久密码、自托管(网络设置)、防火墙放行等均由官方客户端 GUI / 安装包负责，
本工具不再处理（避免覆盖用户在官方客户端里的既有设定）。

管理员/root 权限：修改服务/配置需要提权。Windows 打包 exe 自带 requireAdministrator
manifest（见 build_exe.py）；Linux/macOS 请以 root/sudo 运行，或在界面点『以提升权限重启』。
未提权时也能运行，但受限操作会被拦截并提示提权。

打包（跨平台，见 build_app.py / GitHub Actions）：
    python build_app.py
（依赖 pyinstaller，脚本会自动检测并提示安装）

Python: E:\\software\\python3   GUI: PySide6
"""

import os
import sys
import re
import ctypes
import subprocess
import platform
import shutil

try:
    import tomllib  # Python 3.11+
except ImportError:
    tomllib = None

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QMessageBox, QFrame,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
SERVICE_NAME = "RustDesk"          # Windows 服务名
CONFIG_FILENAME = "RustDesk2.toml"

# 平台识别与平台相关常量（路径 / 服务名 / 二进制提示）
PLAT = sys.platform                # "win32" / "linux" / "darwin"

if PLAT == "win32":
    BIN_LABEL = "RustDesk.exe"
    SVC_UNIT = "RustDesk"          # Windows 服务名 / systemd 单元 / launchd label 基础
    USER_CONFIG_DIR = os.path.join(os.environ.get("APPDATA", ""), "RustDesk", "config")
    # 服务模式(LocalService)真实配置路径
    SERVICE_CONFIG_DIR = (
        r"C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config"
    )
    BIN_HINTS = [
        r"C:\Program Files\RustDesk\RustDesk.exe",
        r"C:\Program Files (x86)\RustDesk\RustDesk.exe",
        r"E:\Program Files\RustDesk\RustDesk\RustDesk.exe",
        r"D:\Program Files\RustDesk\RustDesk.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Programs", "RustDesk", "RustDesk.exe"),
    ]
elif PLAT == "darwin":
    BIN_LABEL = "RustDesk"
    SVC_UNIT = "com.carriez.rustdesk_service"   # launchd label
    USER_CONFIG_DIR = os.path.expanduser(
        os.path.join("~", "Library", "Application Support", "RustDesk", "config"))
    SERVICE_CONFIG_DIR = "/Library/Application Support/RustDesk/config"
    BIN_HINTS = [
        "/Applications/RustDesk.app/Contents/MacOS/RustDesk",
        "/opt/RustDesk/RustDesk",
        "/usr/local/bin/rustdesk",
    ]
else:  # linux 及类 Unix
    BIN_LABEL = "rustdesk"
    SVC_UNIT = "rustdesk"           # systemd 单元名
    USER_CONFIG_DIR = os.path.expanduser(os.path.join("~", ".config", "rustdesk"))
    SERVICE_CONFIG_DIR = "/root/.config/rustdesk"
    BIN_HINTS = [
        "/usr/bin/rustdesk",
        "/usr/local/bin/rustdesk",
        "/opt/rustdesk/rustdesk",
        "/usr/share/rustdesk/rustdesk",
    ]


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def is_admin() -> bool:
    if platform.system() != "Windows":
        try:
            return os.geteuid() == 0
        except Exception:
            return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    if PLAT == "win32":
        params = '"' + os.path.abspath(sys.argv[0]) + '"'
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)
    elif PLAT == "linux":
        # 用 pkexec / sudo 以 root 重新启动（GUI 会话中可弹密码框）
        exe = sys.executable
        script = os.path.abspath(sys.argv[0])
        for tool in ("pkexec", "sudo"):
            rc, _ = run_cmd([tool, "--version"], shell=False)
            if rc == 0:
                try:
                    subprocess.run([tool, exe, script] + sys.argv[1:])
                except Exception:
                    pass
                sys.exit(0)
        # 无可用的提权工具：保持运行，由 _need_admin 提示手动以 root 运行
        return
    else:  # darwin
        script = os.path.abspath(sys.argv[0])
        cmd = ('do shell script "\\"{}\\" \\"{}\\"" with administrator privileges'
               .format(sys.executable, script))
        try:
            subprocess.run(["osascript", "-e", cmd])
        except Exception:
            pass
        sys.exit(0)


def _decode(b: bytes) -> str:
    """命令行输出在中文 Windows 多为 GBK，优先 utf-8 再退到 gbk/latin-1。"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("utf-8", errors="replace")


def run_cmd(args, shell=False, timeout=60):
    """执行命令，返回 (returncode, combined_output)。以字节读取避免中文乱码。"""
    try:
        # CREATE_NO_WINDOW 仅 Windows 有效；其它平台传 0
        creationflags = 0x08000000 if PLAT == "win32" else 0
        proc = subprocess.run(
            args, shell=shell, capture_output=True, timeout=timeout,
            creationflags=creationflags,
        )
        out = _decode(proc.stdout or b"") + _decode(proc.stderr or b"")
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return -1, "命令执行超时"
    except Exception as e:  # noqa
        return -1, f"执行失败: {e}"


def val_repr(v):
    """把 Python 值序列化为 RustDesk TOML 风格：字符串用单引号，其余原样。"""
    if isinstance(v, bool):
        return "'Y'" if v else "'N'"
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace("'", "\\'")
        return "'" + escaped + "'"
    return str(v)


# ----------------------------------------------------------------------------
# RustDesk 业务封装
# ----------------------------------------------------------------------------
class RustDeskManager:
    def __init__(self):
        self.exe_path, self.portable_hint = self._find_exe()

    # ---- 路径探测（按平台寻找本机安装）----
    def _find_exe(self):
        if PLAT == "win32":
            return self._find_exe_windows()
        # Linux / macOS：先标准路径，再 PATH，最后目录兜底
        for c in BIN_HINTS:
            if os.path.exists(c):
                return c, self._is_portable(c)
        p = shutil.which("rustdesk") or shutil.which("RustDesk")
        if p and os.path.exists(p):
            return p, self._is_portable(p)
        roots = ["/usr", "/opt", os.path.expanduser("~")]
        for base in roots:
            if PLAT == "darwin":
                cand = os.path.join(base, "Applications", "RustDesk.app",
                                    "Contents", "MacOS", "RustDesk")
            else:
                cand = os.path.join(base, "rustdesk", "rustdesk")
            if os.path.exists(cand):
                return cand, self._is_portable(cand)
            try:
                for d in os.listdir(base):
                    if PLAT == "darwin":
                        cand2 = os.path.join(base, d, "RustDesk.app",
                                            "Contents", "MacOS", "RustDesk")
                    else:
                        cand2 = os.path.join(base, d, "rustdesk", "rustdesk")
                    if os.path.exists(cand2):
                        return cand2, True
            except Exception:
                pass
        return None, False

    def _find_exe_windows(self):
        # 1) 服务 ImagePath（最权威，安装版必注册）
        rc, out = run_cmd(
            ["reg", "query",
             r"HKLM\SYSTEM\CurrentControlSet\Services\RustDesk",
             "/v", "ImagePath"], shell=False)
        if rc == 0:
            m = re.search(r'"([^"]+\.exe)"', out)
            if m and os.path.exists(m.group(1)):
                return m.group(1), False
        # 2) 注册表卸载项（InstallLocation / DisplayIcon）
        for root in (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"):
            rc, out = run_cmd(["reg", "query", root, "/s", "/f", "RustDesk"], shell=False)
            if rc == 0:
                for line in out.splitlines():
                    m = re.search(r"DisplayIcon\s+REG_[A-Z_]*\s+(.+)", line)
                    if m:
                        p = m.group(1).strip().strip('"')
                        if p.lower().endswith(".exe") and os.path.exists(p):
                            return p, False
                    m2 = re.search(r"InstallLocation\s+REG_[A-Z_]*\s+(.+)", line)
                    if m2:
                        p = os.path.join(m2.group(1).strip().strip('"'), "RustDesk.exe")
                        if os.path.exists(p):
                            return p, False
        # 3) 常见安装目录
        for c in BIN_HINTS:
            if os.path.exists(c):
                return c, False
        # 4) PATH
        rc, out = run_cmd(["where", "rustdesk"], shell=False)
        if rc == 0:
            for line in out.splitlines():
                line = line.strip()
                if line.lower().endswith(".exe") and os.path.exists(line):
                    return line, False
        # 5) 兜底目录扫描
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", ""),
                     "D:/", "E:/", "F:/"):
            cand = os.path.join(base, "RustDesk", "RustDesk.exe")
            if os.path.exists(cand):
                return cand, False
            try:
                for d in os.listdir(base):
                    cand2 = os.path.join(base, d, "RustDesk.exe")
                    if os.path.exists(cand2):
                        return cand2, True
            except Exception:
                pass
        return None, False

    def _is_portable(self, path):
        """非系统标准目录视为便携/用户目录安装（无法作为系统服务稳定运行）。"""
        norm = path.replace("\\", "/").lower()
        for s in ("/usr/bin/", "/usr/local/bin/", "/opt/", "/applications/",
                  "c:/program files", "c:/program files (x86)",
                  "e:/program files", "d:/program files"):
            if norm.startswith(s):
                return False
        return True

    def set_exe_path(self, path):
        if os.path.exists(path):
            self.exe_path = path
            self.portable_hint = self._is_portable(path)
            return True
        return False

    # ---- 服务（按平台分派：Windows=sc/net / Linux=systemctl / macOS=launchctl）----
    def get_service_status(self):
        if PLAT == "win32":
            rc, out = run_cmd(["sc", "query", SERVICE_NAME], shell=False)
            if rc != 0 or "STATE" not in out:
                return {"installed": False, "state": None, "start_type": None}
            state = start_type = None
            m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", out)
            if m:
                state = m.group(1)
            rc2, out2 = run_cmd(["sc", "qc", SERVICE_NAME], shell=False)
            if rc2 == 0:
                m2 = re.search(r"START_TYPE\s*:\s*\d+\s+(\w+)", out2)
                if m2:
                    start_type = m2.group(1)
            return {"installed": True, "state": state, "start_type": start_type}
        elif PLAT == "linux":
            rc, out = run_cmd(["systemctl", "is-enabled", SVC_UNIT], shell=False)
            enabled = rc == 0
            rc2, out2 = run_cmd(["systemctl", "is-active", SVC_UNIT], shell=False)
            active = rc2 == 0
            if "not-found" in out.lower():
                return {"installed": False, "state": None, "start_type": None}
            return {
                "installed": True,
                "state": "RUNNING" if active else "STOPPED",
                "start_type": "AUTO_START" if enabled else "DEMAND_START",
            }
        else:  # darwin
            rc, out = run_cmd(["launchctl", "print", f"system/{SVC_UNIT}"], shell=False)
            if rc != 0:
                return {"installed": False, "state": None, "start_type": None}
            active = "state = active" in out
            enabled = ("disabled = false" in out) or ("enabled" in out)
            return {
                "installed": True,
                "state": "RUNNING" if active else "STOPPED",
                "start_type": "AUTO_START" if enabled else "DEMAND_START",
            }

    def install_service(self):
        if not self.exe_path:
            return False, "未找到 RustDesk 可执行文件，请先安装系统安装版。"
        rc, out = run_cmd([self.exe_path, "--install-service"], shell=False)
        if rc != 0:
            return False, f"安装服务失败(rc={rc}):\n{out}"
        return True, "服务已安装。\n" + out

    def set_service_auto(self):
        if PLAT == "win32":
            rc, out = run_cmd(["sc", "config", SERVICE_NAME, "start=", "auto"], shell=False)
            if rc != 0:
                return False, f"设置自动启动失败(rc={rc}):\n{out}"
            return True, "已设为开机自动启动。\n" + out
        elif PLAT == "linux":
            rc, out = run_cmd(["systemctl", "enable", SVC_UNIT], shell=False)
            if rc != 0:
                return False, f"设置自动启动失败(rc={rc}):\n{out}"
            return True, "已设为开机自动启动。\n" + out
        else:  # darwin
            rc, out = run_cmd(["launchctl", "enable", f"system/{SVC_UNIT}"], shell=False)
            if rc != 0:
                return False, f"启用服务失败(rc={rc}):\n{out}"
            return True, "已设为开机自启。\n" + out

    def start_service(self):
        if PLAT == "win32":
            rc, out = run_cmd(["net", "start", SERVICE_NAME], shell=False)
            if rc != 0 and "already" not in out.lower():
                rc2, out2 = run_cmd(["sc", "start", SERVICE_NAME], shell=False)
                if rc2 != 0:
                    return False, f"启动服务失败:\n{out}\n{out2}"
            return True, "服务已启动。\n" + out
        elif PLAT == "linux":
            rc, out = run_cmd(["systemctl", "start", SVC_UNIT], shell=False)
            if rc != 0:
                return False, f"启动服务失败(rc={rc}):\n{out}"
            return True, "服务已启动。\n" + out
        else:  # darwin
            rc, out = run_cmd(["launchctl", "kickstart", "-k", f"system/{SVC_UNIT}"], shell=False)
            if rc != 0:
                return False, f"启动服务失败(rc={rc}):\n{out}"
            return True, "服务已启动。\n" + out

    def restart_service(self):
        import time
        if PLAT == "win32":
            run_cmd(["net", "stop", SERVICE_NAME], shell=False)
            run_cmd(["sc", "stop", SERVICE_NAME], shell=False)
            time.sleep(2)
            rc, out = run_cmd(["net", "start", SERVICE_NAME], shell=False)
            if rc != 0 and "already" not in out.lower():
                rc2, out2 = run_cmd(["sc", "start", SERVICE_NAME], shell=False)
                if rc2 != 0:
                    return False, f"重启服务失败:\n{out}\n{out2}"
            return True, "服务已重启。\n" + out
        elif PLAT == "linux":
            rc, out = run_cmd(["systemctl", "restart", SVC_UNIT], shell=False)
            if rc != 0:
                return False, f"重启服务失败(rc={rc}):\n{out}"
            return True, "服务已重启。\n" + out
        else:  # darwin
            rc, out = run_cmd(["launchctl", "kickstart", "-k", f"system/{SVC_UNIT}"], shell=False)
            if rc != 0:
                return False, f"重启服务失败(rc={rc}):\n{out}"
            return True, "服务已重启。\n" + out

    def stop_and_demand_service(self):
        if PLAT == "win32":
            run_cmd(["net", "stop", SERVICE_NAME], shell=False)
            run_cmd(["sc", "stop", SERVICE_NAME], shell=False)
            rc, out = run_cmd(["sc", "config", SERVICE_NAME, "start=", "demand"], shell=False)
            if rc != 0:
                return False, f"取消开机自启失败(rc={rc}):\n{out}"
            return True, "已停止服务并改为『手动(按需)』启动（服务本体保留）。\n" + out
        elif PLAT == "linux":
            run_cmd(["systemctl", "stop", SVC_UNIT], shell=False)
            rc, out = run_cmd(["systemctl", "disable", SVC_UNIT], shell=False)
            if rc != 0:
                return False, f"取消开机自启失败(rc={rc}):\n{out}"
            return True, "已停止服务并改为『手动(按需)』启动（服务本体保留）。\n" + out
        else:  # darwin
            run_cmd(["launchctl", "bootout", f"system/{SVC_UNIT}"], shell=False)
            rc, out = run_cmd(["launchctl", "disable", f"system/{SVC_UNIT}"], shell=False)
            if rc != 0:
                return False, f"取消开机自启失败(rc={rc}):\n{out}"
            return True, "已停止服务并取消开机自启（服务本体保留）。\n" + out

    # ---- 配置读写 ----
    def load_config(self, path):
        if not os.path.exists(path):
            return {"options": {}}
        try:
            if tomllib:
                with open(path, "rb") as f:
                    return tomllib.load(f)
        except Exception:
            pass
        data = {"options": {}}
        in_options = False
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line == "[options]":
                        in_options = True
                        continue
                    m = re.match(r"^([\w\-]+)\s*=\s*(.+)$", line)
                    if m:
                        k, v = m.group(1), m.group(2).strip().strip("'\"")
                        if in_options:
                            data.setdefault("options", {})[k] = v
                        else:
                            data[k] = v
        except Exception:
            pass
        data.setdefault("options", {})
        return data

    def save_config(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = []
        for k in ("rendezvous_server", "nat_type", "serial"):
            if k in data and data[k] not in (None, ""):
                lines.append(f"{k} = {val_repr(data[k])}")
        lines.append("")
        lines.append("[options]")
        opts = data.get("options", {})
        ordered = [
            "verification-method", "access-mode", "custom-rendezvous-server",
            "relay-server", "key", "allow-logon-screen-password",
            "allow-remote-config-modification",
        ]
        for k in ordered:
            if k in opts and opts[k] not in (None, ""):
                lines.append(f"{k} = {val_repr(opts[k])}")
        for k, v in opts.items():
            if k not in ordered and v not in (None, ""):
                lines.append(f"{k} = {val_repr(v)}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _config_paths(self):
        paths = [os.path.join(USER_CONFIG_DIR, CONFIG_FILENAME)]
        # 服务模式(以 root / LocalService 运行)读取的配置路径：
        # 有管理员/root 权限时一并写入，确保登录屏前服务能读到 allow-logon-screen-password。
        if is_admin():
            sp = os.path.join(SERVICE_CONFIG_DIR, CONFIG_FILENAME)
            if sp not in paths:
                paths.append(sp)
        return paths

    # ---- 允许登录屏幕密码（allow-logon-screen-password）----
    def set_logon_screen_password(self, enabled):
        paths = self._config_paths()
        if not paths:
            return False, "未找到配置目录。"
        ok = True
        msgs = []
        for p in paths:
            data = self.load_config(p)
            data.setdefault("options", {})
            data["options"]["allow-logon-screen-password"] = "Y" if enabled else "N"
            try:
                self.save_config(p, data)
                msgs.append(f"已写入: {p}")
            except Exception as e:  # noqa
                ok = False
                msgs.append(f"写入失败 {p}: {e}")
        if ok:
            self.restart_service()
            msgs.append("已重启服务使配置生效。")
        return ok, "\n".join(msgs)

    def get_id(self):
        if not self.exe_path:
            return None
        rc, out = run_cmd([self.exe_path, "--get-id"], shell=False, timeout=15)
        if rc == 0 and out:
            return out.strip()
        return None


# ----------------------------------------------------------------------------
# 后台执行线程
# ----------------------------------------------------------------------------
class Worker(QThread):
    finished = Signal(bool, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            ok, msg = self.fn()
            self.finished.emit(bool(ok), str(msg))
        except Exception as e:  # noqa
            self.finished.emit(False, f"执行异常: {e}")


class DeployWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            ok, msg = self.fn()
            self.finished.emit(bool(ok), str(msg))
        except Exception as e:  # noqa
            self.finished.emit(False, f"执行异常: {e}")


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.mgr = RustDeskManager()
        self.worker = None
        self.deploy_worker = None
        self.setWindowTitle("RustDesk 无人值守配置工具  ·  v1.3")
        self.resize(860, 700)
        self._build_ui()
        self.refresh_all()

    # ---------------- UI 构建（无标签页，仅官方客户端不具备的功能）--------
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # 顶部状态条
        top = QFrame()
        top.setFrameShape(QFrame.StyledPanel)
        tl = QHBoxLayout(top)
        self.lbl_exe = QLabel(f"{BIN_LABEL}: 检测中…")
        self.lbl_admin = QLabel("权限: 检测中…")
        self.lbl_admin.setFont(QFont("Consolas", 9))
        btn_admin = QPushButton("以提升权限重启")
        btn_admin.clicked.connect(self.on_relaunch_admin)
        tl.addWidget(self.lbl_exe, 1)
        tl.addWidget(self.lbl_admin, 1)
        tl.addWidget(btn_admin)
        v.addWidget(top)

        # 说明：本工具只做官方客户端/安装包不具备的两件事
        tip = QLabel(
            "本工具只做官方客户端不具备的两件事：\n"
            "① 确保 RustDesk 服务已安装、开机自启并正在运行（锁屏前就要起来抓取登录界面）；\n"
            "② 启用『允许登录屏幕密码』（allow-logon-screen-password）。\n"
            "永久密码、自托管(网络设置)、防火墙放行等均由官方客户端/安装包负责，本工具不再处理。\n"
            "（Windows/macOS/Linux 通用；macOS 还需在系统设置手动授予『屏幕录制/辅助功能』权限）")
        tip.setWordWrap(True)
        v.addWidget(tip)

        # 一键部署（主操作）
        btn_deploy = QPushButton("一键部署无人值守（按推荐值）")
        btn_deploy.setMinimumHeight(42)
        btn_deploy.setStyleSheet("font-weight:bold;")
        btn_deploy.clicked.connect(self.on_deploy)
        self.btn_deploy = btn_deploy
        v.addWidget(btn_deploy)

        # 还原（次要操作：仅撤销本工具所设的两项）
        btn_restore = QPushButton("还原设置（关闭登录屏密码 + 停止并取消服务自启）")
        btn_restore.clicked.connect(self.on_restore)
        self.btn_restore = btn_restore
        v.addWidget(btn_restore)

        # 执行日志
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        self.log.setFont(QFont("Consolas", 9))
        v.addWidget(QLabel("执行日志："))
        v.addWidget(self.log, 1)

    # ---------------- 日志 ----------------
    def log_msg(self, msg):
        self.log.append(msg)
        self.log.moveCursor(QTextCursor.End)

    # ---------------- 刷新 ----------------
    def refresh_all(self):
        self.refresh_top()

    def refresh_top(self):
        admin = is_admin()
        self.lbl_admin.setText("权限: " + ("管理员/root ✓" if admin else "普通用户（部分操作受限）"))
        if self.mgr.exe_path:
            self.lbl_exe.setText(f"{BIN_LABEL}: " + self.mgr.exe_path)
        else:
            self.lbl_exe.setText(f"{BIN_LABEL}: 未找到")

    # ---------------- 操作 ----------------
    def _need_admin(self):
        if not is_admin():
            QMessageBox.warning(self, "需要管理员/root 权限",
                                "该操作需要管理员/root 权限。请点右上角『以提升权限重启』后重试。")
            return False
        return True

    def _run(self, fn, busy_text):
        if self.worker and self.worker.isRunning():
            return
        self.log_msg("▶ " + busy_text)
        self.worker = Worker(fn)
        self.worker.finished.connect(self._on_worker_done)
        self.worker.start()

    def _on_worker_done(self, ok, msg):
        self.log_msg(("✓ 成功\n" if ok else "✗ 失败\n") + msg + "\n" + "-" * 40)
        self.refresh_all()

    # 还原（仅撤销本工具所设的两项：登录屏密码 + 服务自启）
    def on_restore(self):
        if not self._need_admin():
            return

        def _do():
            results = []
            ok, m = self.mgr.set_logon_screen_password(False)
            results.append(("✓ " if ok else "✗ ") + "关闭登录屏密码: " + m)
            if not ok:
                return False, "\n".join(results)
            ok, m = self.mgr.stop_and_demand_service()
            results.append(("✓ " if ok else "✗ ") + "停止并取消服务自启: " + m)
            if not ok:
                return False, "\n".join(results)
            return True, "\n".join(results)

        self._run(_do, "还原设置（关闭登录屏密码 + 取消服务自启）…")

    # 一键部署：仅做官方客户端不具备的部分（服务 + 登录屏密码）
    def on_deploy(self):
        if not self._need_admin():
            return
        if not self.mgr.exe_path:
            QMessageBox.warning(self, "未找到 RustDesk",
                                f"未找到 {BIN_LABEL}，请先安装系统安装版。")
            return
        if self.mgr.portable_hint:
            QMessageBox.warning(self, "不支持该安装类型",
                                "检测到的是非系统标准安装（无法作为系统服务稳定运行），"
                                "请改用系统安装版后再部署。")
            return
        if self.deploy_worker and self.deploy_worker.isRunning():
            return

        def _deploy():
            results = []
            st = self.mgr.get_service_status()
            if not st["installed"]:
                ok, m = self.mgr.install_service()
                results.append(("✓ " if ok else "✗ ") + "安装服务: " + m)
                if not ok:
                    return False, "\n".join(results)
            ok, m = self.mgr.set_service_auto()
            results.append(("✓ " if ok else "✗ ") + "设为自动启动: " + m)
            if not ok:
                return False, "\n".join(results)
            if st["state"] != "RUNNING":
                ok, m = self.mgr.start_service()
                results.append(("✓ " if ok else "✗ ") + "启动服务: " + m)
                if not ok:
                    return False, "\n".join(results)
            ok, m = self.mgr.set_logon_screen_password(True)
            results.append(("✓ " if ok else "✗ ") + "登录屏密码: " + m)
            if not ok:
                return False, "\n".join(results)
            return True, "\n".join(results)

        self.log_msg("▶ 一键部署无人值守（服务自启+启动 → 登录屏密码）…")
        self.deploy_worker = DeployWorker(_deploy)
        self.deploy_worker.finished.connect(self._on_deploy_done)
        self.deploy_worker.start()

    def _on_deploy_done(self, ok, msg):
        self.log_msg(("✓ 部署成功\n" if ok else "✗ 部署未完成\n") + msg + "\n" + "-" * 40)
        self.refresh_all()

    # 其它
    def on_relaunch_admin(self):
        relaunch_as_admin()


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
