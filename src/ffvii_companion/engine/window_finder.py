"""
Finds the FFVII window through Win32 (ctypes): a cached window handle first, then the window title,
then the windows of ff7* / ffvii* processes; full scans are throttled. Returns the window's screen
rectangle for the capture region. Without Win32 (other platforms) it finds nothing.
"""
import sys
import time
import threading
import ctypes
from ctypes import wintypes
from typing import Optional, Dict, Any, List, Set
import psutil

USER32 = ctypes.windll.user32 if sys.platform == "win32" else None
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM) if sys.platform == "win32" else None

class FFVIIWindowFinder:
    FFVII_PROCESS_NAMES = [
        "ff7.exe",
        "ff7_en.exe",
        "ff7_mo.exe",
        "ff7_bc.exe",
        "ff7_de.exe",
        "ff7_es.exe",
        "ff7_fr.exe",
        "ff7_it.exe",
        "ff7_launcher.exe",
        "ffvii.exe"
    ]

    # Any executable starting with these prefixes is also treated as the game
    # (covers renamed re-release / mod-manager executables).
    FFVII_PROCESS_PREFIXES = ("ff7", "ffvii")

    TARGET_TITLES = [
        "FINAL FANTASY VII",
        "Final Fantasy VII",
        "FFVII",
        "FF7"
    ]

    EXCLUDE_KEYWORDS = [
        "EXPLORER",
        "COMPANION",
        "FIREFOX",
        "CHROME",
        "EDGE",
        "ANTIGRAVITY",
        "CODE",
        "POWERSHELL",
        "TERMINAL",
        "DISCORD"
    ]

    def __init__(self):
        self._cached_hwnd: Optional[int] = None
        self._cached_pid: Optional[int] = None
        self._cached_title: Optional[str] = None
        self._last_scan_time: float = 0.0
        self._scan_interval: float = 1.0
        self._attached_threads: Set[int] = set()
        self._ensure_desktop_attached()

    @property
    def scan_interval(self) -> float:
        """Seconds between full scans while no window is cached: 1.0, backing off by 0.3 per
        scan that finds nothing, up to 2.5."""
        return self._scan_interval

    @classmethod
    def is_ffvii_process_name(cls, process_name: str) -> bool:
        """True for known FFVII executables (ff7_en.exe, ff7.exe, ...) or any ff7*/ffvii* executable."""
        pname = process_name.lower()
        return any(target in pname for target in cls.FFVII_PROCESS_NAMES) or pname.startswith(cls.FFVII_PROCESS_PREFIXES)

    def _ensure_desktop_attached(self):
        tid = threading.get_ident()
        if tid in self._attached_threads:
            return
        if USER32:
            try:
                h_desk = USER32.OpenDesktopW("Default", 0, False, 0x01FF)
                if h_desk:
                    USER32.SetThreadDesktop(h_desk)
                    self._attached_threads.add(tid)
            except Exception:
                pass

    def find_ffvii_window(self, force_scan: bool = False) -> Optional[Dict[str, Any]]:
        """
        Locates the running FFVII game window using process inspection and window enumeration.
        Uses cached HWND and FindWindowW for microsecond-fast subsequent checks.
        Without a cached window, full scans are throttled to one per _scan_interval;
        force_scan=True (an explicit user request) scans regardless.
        Returns dict with hwnd, title, left, top, width, height, or None if not found.
        """
        if not USER32:
            return None

        self._ensure_desktop_attached()
        user32 = USER32

        # Fast path 1: verify if previously cached window handle is still alive and visible
        if self._cached_hwnd:
            if user32.IsWindow(self._cached_hwnd) and user32.IsWindowVisible(self._cached_hwnd) and not user32.IsIconic(self._cached_hwnd):
                rect = wintypes.RECT()
                user32.GetWindowRect(self._cached_hwnd, ctypes.byref(rect))
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 320 and height > 240:
                    self._scan_interval = 1.0
                    return {
                        "hwnd": self._cached_hwnd,
                        "title": self._cached_title or "FINAL FANTASY VII",
                        "pid": self._cached_pid or 0,
                        "left": rect.left,
                        "top": rect.top,
                        "width": width,
                        "height": height,
                        "source": "cached_window"
                    }
            # Cache invalidated (game minimized or closed)
            self._cached_hwnd = None
            self._cached_pid = None

        now = time.time()
        if not force_scan and (now - self._last_scan_time) < self._scan_interval:
            return None
        self._last_scan_time = now

        # Fast path 2: Direct FindWindowW lookup on canonical game window titles (microsecond cost)
        rect = wintypes.RECT()
        lpdw_pid = wintypes.DWORD()
        for target_title in self.TARGET_TITLES:
            h = user32.FindWindowW(None, target_title)
            if h and user32.IsWindowVisible(h) and not user32.IsIconic(h):
                user32.GetWindowRect(h, ctypes.byref(rect))
                w = rect.right - rect.left
                ht = rect.bottom - rect.top
                if w > 320 and ht > 240:
                    user32.GetWindowThreadProcessId(h, ctypes.byref(lpdw_pid))
                    pid = lpdw_pid.value
                    self._cached_hwnd = h
                    self._cached_pid = pid
                    self._cached_title = target_title
                    self._scan_interval = 1.0
                    return {
                        "hwnd": h,
                        "title": target_title,
                        "pid": pid,
                        "left": rect.left,
                        "top": rect.top,
                        "width": w,
                        "height": ht,
                        "source": "find_window"
                    }

        # 3. Search for FFVII process IDs if direct window title did not match
        ffvii_pids = set()
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                pname = (proc.info['name'] or '').lower()
                if self.is_ffvii_process_name(pname):
                    ffvii_pids.add(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # If no FFVII process is running at all, skip full window enumeration and back off
        if not ffvii_pids:
            self._scan_interval = min(2.5, self._scan_interval + 0.3)
            return None

        # FFVII process found: enumerate visible windows, checking PID before title string extraction
        matches: List[Dict[str, Any]] = []

        def enum_windows_callback(hwnd, lParam):
            if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                win_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
                pid = win_pid.value

                if pid not in ffvii_pids:
                    return True

                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value
                    title_upper = title.upper()

                    # Exclude non-game utilities
                    if any(exc in title_upper for exc in self.EXCLUDE_KEYWORDS):
                        return True

                    r = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(r))
                    width = r.right - r.left
                    height = r.bottom - r.top

                    if width > 320 and height > 240:
                        matches.append({
                            "hwnd": hwnd,
                            "title": title,
                            "pid": pid,
                            "left": r.left,
                            "top": r.top,
                            "width": width,
                            "height": height,
                            "source": "process_match"
                        })
            return True

        user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)

        if matches:
            matches.sort(key=lambda m: m["width"] * m["height"], reverse=True)
            best = matches[0]
            self._cached_hwnd = best["hwnd"]
            self._cached_pid = best["pid"]
            self._cached_title = best["title"]
            self._scan_interval = 1.0
            return best

        self._scan_interval = min(2.5, self._scan_interval + 0.3)
        return None

if __name__ == "__main__":
    finder = FFVIIWindowFinder()
    win = finder.find_ffvii_window()
    if win:
        print(f"Found FFVII window: '{win['title']}' [PID {win.get('pid')}] ({win['left']}, {win['top']}) {win['width']}x{win['height']}")
    else:
        print("FFVII game window is not currently open.")
