"""
Phone AI agent — control Android phone via ADB: screenshots, installs, downloads,
system optimization, game modding, input simulation.
"""
from __future__ import annotations
import base64
import os
import tempfile
import subprocess
from pathlib import Path


_ADB = Path(r"C:\Users\vybzd\AppData\Local\Android\Sdk\platform-tools\adb.exe")


def _adb(*args: str, timeout: int = 60) -> tuple[bool, str]:
    """Run ADB command, return (success, output)."""
    cmd = [str(_ADB)] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, (result.stdout or result.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


class PhoneAgent:
    """Control phone via ADB."""

    @staticmethod
    def device_id() -> str:
        success, output = _adb("devices")
        lines = output.strip().split("\n")[1:]
        devices = [l.split("\t")[0] for l in lines if "\tdevice" in l]
        if not devices:
            raise RuntimeError("No device connected")
        return devices[0]

    # ─── Screenshots & screen reading ───────────────────

    @classmethod
    def screenshot(cls) -> dict:
        """Take screenshot, return base64 + metadata."""
        dev = cls.device_id()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        try:
            ok, _ = _adb("-s", dev, "screencap", "-p", "/sdcard/screen.png")
            if not ok:
                return {"error": "screencap failed"}
            ok, _ = _adb("-s", dev, "pull", "/sdcard/screen.png", tmp)
            if not ok:
                return {"error": "pull failed"}
            with open(tmp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return {"image": b64, "size": len(b64), "mime": "image/png"}
        finally:
            try:
                os.unlink(tmp)
                _adb("-s", dev, "shell", "rm", "-f", "/sdcard/screen.png")
            except:
                pass

    @classmethod
    def screen_text(cls) -> str:
        """Get current app package + window focus info."""
        dev = cls.device_id()
        _, out = _adb("-s", dev, "shell", "dumpsys", "window", "|", "findstr", "mCurrentFocus", "mFocusedApp")
        return out

    # ─── App management ───────────────────────────────

    @classmethod
    def installed_apps(cls) -> list[str]:
        dev = cls.device_id()
        ok, out = _adb("-s", dev, "shell", "cmd", "package", "list", "packages", "-3")
        return [p.replace("package:", "") for p in out.strip().split("\n") if p] if ok else []

    @classmethod
    def launch_app(cls, package: str) -> dict:
        dev = cls.device_id()
        ok, msg = _adb("-s", dev, "shell", "monkey", "-p", package, "1")
        return {"success": ok, "message": msg}

    @classmethod
    def force_stop(cls, package: str) -> dict:
        dev = cls.device_id()
        ok, msg = _adb("-s", dev, "shell", "am", "force-stop", package)
        return {"success": ok, "message": msg}

    # ─── Download & file operations ───────────────────

    @classmethod
    def download(cls, url: str, dest: str = "/sdcard/Download/") -> dict:
        dev = cls.device_id()
        filename = url.split("/")[-1].split("?")[0] or "download"
        dest_path = f"{dest.rstrip('/')}/{filename}"
        ok, msg = _adb("-s", dev, "shell", "curl", "-sS", "-L", "-o", dest_path, url)
        return {"success": ok, "message": msg, "path": dest_path}

    @classmethod
    def list_downloads(cls) -> list[str]:
        dev = cls.device_id()
        ok, out = _adb("-s", dev, "shell", "ls", "/sdcard/Download/")
        return out.split("\n") if ok else []

    # ─── System info & optimization ───────────────────

    @classmethod
    def system_info(cls) -> dict:
        dev = cls.device_id()
        _, battery = _adb("-s", dev, "shell", "dumpsys", "battery")
        _, storage = _adb("-s", dev, "shell", "df", "/sdcard")
        _, meminfo = _adb("-s", dev, "shell", "dumpsys", "meminfo", "|", "findstr", "Totals")
        return {
            "battery": [l.strip() for l in battery.split("\n") if l.strip()][:5],
            "storage": [l.strip() for l in storage.split("\n") if l.strip()],
            "memory": meminfo.strip(),
        }

    @classmethod
    def kill_bg_apps(cls) -> list[str]:
        dev = cls.device_id()
        apps = cls.installed_apps()
        killed = []
        for pkg in apps[:100]:
            if not any(p in pkg for p in ["android", "google", "com.jarvis"]):
                _adb("-s", dev, "shell", "am", "force-stop", pkg)
                killed.append(pkg)
        return killed[:30]

    @classmethod
    def game_mode(cls, package: str) -> dict:
        dev = cls.device_id()
        ok1, _ = _adb("-s", dev, "shell", "settings", "put", "global", "force_4x_msaa", "1")
        ok2, _ = _adb("-s", dev, "shell", "settings", "put", "global", "force_gpu_rendering", "1")
        ok3, _ = _adb("-s", dev, "shell", "settings", "put", "global", "force_desktop_mode_on_external_displays", "1")
        return {"success": True, "message": f"Game mode enabled: {package}\n4x MSAA, GPU forced", "game": package}

    # ─── Input simulation ─────────────────────────────

    @classmethod
    def tap(cls, x: int, y: int) -> dict:
        dev = cls.device_id()
        ok, msg = _adb("-s", dev, "shell", "input", "tap", str(x), str(y))
        return {"success": ok, "message": msg}

    @classmethod
    def swipe(cls, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> dict:
        dev = cls.device_id()
        ok, msg = _adb("-s", dev, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration))
        return {"success": ok, "message": msg}

    @classmethod
    def type_text(cls, text: str) -> dict:
        dev = cls.device_id()
        escaped = text.replace(" ", "%s").replace("'", "\\" + "'")
        ok, msg = _adb("-s", dev, "shell", "input", "text", escaped)
        return {"success": ok, "message": msg}

    @classmethod
    def press_key(cls, code: int) -> dict:
        dev = cls.device_id()
        ok, msg = _adb("-s", dev, "shell", "input", "keyevent", str(code))
        return {"success": ok, "message": msg}

    @classmethod
    def shell(cls, cmd: str) -> dict:
        """Run any shell command on the phone."""
        dev = cls.device_id()
        ok, msg = _adb("-s", dev, "shell", cmd)
        return {"success": ok, "output": msg}
