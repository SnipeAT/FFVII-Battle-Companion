import sys
import os

# Enable Windows Per-Monitor DPI Aware V2 (PMv2) before any window initialization.
# This ensures native, crisp rendering on 4K displays while automatically scaling
# the title bar, minimize, and close buttons to standard size on 1440p / 1080p displays.
if sys.platform == "win32":
    try:
        import ctypes
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1 fallback)
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

# Keep mathematical and tensor runtimes single-threaded so they never spin up idle
# thread pools on high-core CPUs (e.g. 13900K). OCR threading is configured explicitly
# through the ocr_threads setting.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import cv2
cv2.setNumThreads(1)

import time
import socket
import logging
import logging.handlers
import argparse
import threading
import multiprocessing
import urllib.request

import uvicorn
import webview

from ffvii_companion.paths import get_user_data_dir
from ffvii_companion.engine import win_qos
from ffvii_companion.server.main import app, register_desktop_window_getter
from ffvii_companion.engine.capture_service import capture_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("ffvii-desktop")


def setup_file_logging() -> None:
    """
    Mirrors log output to logs/companion.log in the data folder (1 MB, 2 backups). Windowed launches
    (pythonw / the packaged EXE) have no console, so this is the only place errors
    from the capture loop or server end up.
    """
    try:
        log_dir = get_user_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "companion.log", maxBytes=1024 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
    except Exception as e:
        logger.warning(f"File logging unavailable: {e}")


def show_startup_error(message: str) -> None:
    """
    Logs a fatal startup error and shows it in a message box: windowed launches (pythonw, the
    packaged EXE) have no console, so the companion would otherwise exit without a word.
    """
    logger.error(message)
    if sys.platform == "win32":
        try:
            import ctypes
            log_file = get_user_data_dir() / "logs" / "companion.log"
            ctypes.windll.user32.MessageBoxW(
                None, f"{message}\n\nDetails: {log_file}", "FFVII Tactical Battle Companion", 0x10)  # MB_ICONERROR
        except Exception:
            pass

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        # 0.0.0.0 / :: are bind-only addresses; connecting to them fails on Winsock
        probe = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
        return s.connect_ex((probe, port)) == 0

def find_available_port(start_port: int = 59314, host: str = "127.0.0.1") -> int:
    port = start_port
    for _ in range(50):
        if not is_port_in_use(port, host):
            return port
        port += 1
    return start_port

def wait_for_server(url: str, timeout: float = 6.0) -> bool:
    """Polls url until it answers HTTP 200 or the timeout expires."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.05)
    return False

_dpi_wndproc_refs = []

def setup_dpi_scaling_handler(window, base_width: int, base_height: int, min_width: int = 0, min_height: int = 0):
    """
    Subclasses the native Win32 window to handle WM_DPICHANGED dynamically and
    eliminates restrictive minimum size constraints.
    Ensures the window smoothly resizes and scales when dragged between monitors
    with different DPIs (e.g. 4K at 150% and 1440p at 100%), while allowing the
    user to tailor the window to any custom dimensions.
    """
    if sys.platform != "win32":
        return

    def on_shown():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32

            if not hasattr(window, "native") or not window.native:
                return

            hwnd = int(window.native.Handle.ToInt64())
            if not hwnd:
                return

            # Explicitly clear WinForms MinimumSize restriction
            try:
                from System.Drawing import Size
                window.native.MinimumSize = Size(min_width, min_height)
            except Exception:
                pass

            GWLP_WNDPROC = -4
            WM_DPICHANGED = 0x02E0
            WM_GETMINMAXINFO = 0x0024
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010

            class POINT(ctypes.Structure):
                _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

            class MINMAXINFO(ctypes.Structure):
                _fields_ = [
                    ('ptReserved', POINT),
                    ('ptMaxSize', POINT),
                    ('ptMaxPosition', POINT),
                    ('ptMinTrackSize', POINT),
                    ('ptMaxTrackSize', POINT)
                ]

            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            CallWindowProc = user32.CallWindowProcW
            CallWindowProc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            CallWindowProc.restype = LRESULT

            GetWindowLongPtr = user32.GetWindowLongPtrW
            GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
            GetWindowLongPtr.restype = ctypes.c_void_p

            SetWindowLongPtr = user32.SetWindowLongPtrW
            SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            SetWindowLongPtr.restype = ctypes.c_void_p

            # Read the original window procedure before installing ours, so messages
            # arriving right after installation can always be forwarded.
            old_proc = GetWindowLongPtr(hwnd, GWLP_WNDPROC)

            def dpi_wndproc(h, msg, wp, lp):
                if msg == WM_DPICHANGED:
                    rect = ctypes.cast(lp, ctypes.POINTER(wintypes.RECT)).contents
                    w = rect.right - rect.left
                    h_val = rect.bottom - rect.top
                    user32.SetWindowPos(h, 0, rect.left, rect.top, w, h_val, SWP_NOZORDER | SWP_NOACTIVATE)
                    if hasattr(window, "native") and window.native:
                        try:
                            from System.Drawing import Size
                            window.native.MinimumSize = Size(min_width, min_height)
                        except Exception:
                            pass
                    return 0
                elif msg == WM_GETMINMAXINFO:
                    CallWindowProc(old_proc, h, msg, wp, lp)
                    if lp:
                        try:
                            mmi = ctypes.cast(lp, ctypes.POINTER(MINMAXINFO)).contents
                            mmi.ptMinTrackSize.x = min_width
                            mmi.ptMinTrackSize.y = min_height
                        except Exception:
                            pass
                    return 0
                return CallWindowProc(old_proc, h, msg, wp, lp)

            cb = WNDPROC(dpi_wndproc)
            _dpi_wndproc_refs.append(cb)
            SetWindowLongPtr(hwnd, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))

            # Ensure initial window size matches the monitor it is currently positioned on
            current_dpi = user32.GetDpiForWindow(hwnd)
            scale = current_dpi / 96.0
            expected_w = int(base_width * scale)
            expected_h = int(base_height * scale)

            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            actual_w = rect.right - rect.left
            actual_h = rect.bottom - rect.top

            if actual_w != expected_w or actual_h != expected_h:
                user32.SetWindowPos(hwnd, 0, rect.left, rect.top, expected_w, expected_h, SWP_NOZORDER | SWP_NOACTIVATE)
        except Exception as e:
            logger.warning(f"Failed to attach DPI monitor listener: {e}")

    window.events.shown += on_shown

def main():
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="FFVII Tactical Battle Companion - Native Desktop App")
    parser.add_argument("--port", type=int, default=59314, help="HTTP/WebSocket port (default: 59314)")
    parser.add_argument("--host", default="127.0.0.1", help="Network host interface (default: 127.0.0.1 for local only, or 0.0.0.0 for LAN)")
    parser.add_argument("--width", type=int, default=1320, help="Initial window width (default: 1320)")
    parser.add_argument("--height", type=int, default=860, help="Initial window height (default: 860)")
    parser.add_argument("--min-width", type=int, default=0, help="Minimum window width (default: 0 for unrestricted)")
    parser.add_argument("--min-height", type=int, default=0, help="Minimum window height (default: 0 for unrestricted)")
    parser.add_argument("--on-top", action="store_true", help="Start with window pinned always on top")
    parser.add_argument("--no-capture", action="store_true", help="Do not automatically start capture engine on launch")
    parser.add_argument("--normal-priority", action="store_true", help="Do not run the companion in Windows Efficiency mode")
    parser.add_argument("--debug", action="store_true", help="Enable developer inspection tools in desktop window")
    args = parser.parse_args()

    setup_file_logging()

    # Efficiency mode: below-normal priority + EcoQoS for every thread of the companion
    # (the WebView2 UI processes inherit the priority class). The capture loop still
    # opts out of EcoQoS while hunting for a new enemy, so identification stays fast.
    if not args.normal_priority:
        if win_qos.apply_process_efficiency_mode():
            logger.info("Windows Efficiency mode enabled (below-normal priority, EcoQoS).")

    port = args.port
    if is_port_in_use(port, args.host):
        if wait_for_server(f"http://127.0.0.1:{port}/api/health", timeout=0.5):
            # A second instance would run a second capture loop on the same game window
            logger.warning(f"A companion is already running on port {port}; exiting.")
            sys.exit(0)
        logger.warning(f"Port {port} is occupied. Selecting next available port...")
        port = find_available_port(start_port=port, host=args.host)
        logger.info(f"Selected port: {port}")

    # Start in-process Uvicorn server on daemon thread
    server_config = uvicorn.Config(
        app,
        host=args.host,
        port=port,
        log_level="warning",
        access_log=False
    )
    server = uvicorn.Server(server_config)

    def run_server_with_efficiency():
        # Below-normal priority + EcoQoS for the web server thread
        win_qos.set_current_thread_qos(efficient=True, below_normal=True)
        server.run()

    server_thread = threading.Thread(
        target=run_server_with_efficiency,
        name="UvicornServerThread",
        daemon=True
    )
    server_thread.start()

    # Wait until Uvicorn server is accepting requests
    health_url = f"http://127.0.0.1:{port}/api/health"
    logger.info(f"Starting companion server on http://{args.host}:{port} ...")
    if not wait_for_server(health_url, timeout=6.0):
        show_startup_error(f"The companion server did not start on port {port} within 6 seconds.")
        sys.exit(1)

    logger.info("Companion server running successfully.")

    # Automatically start capture service
    if not args.no_capture:
        try:
            logger.info("Auto-starting FFVII OCR screen capture loop...")
            capture_service.start()
        except Exception as e:
            logger.error(f"Failed to auto-start capture engine: {e}")

    # Create native window WITHOUT js_api (eliminates pywebview COM deadlock)
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    window_url = f"http://{display_host}:{port}"

    window = webview.create_window(
        title="FFVII Tactical Battle Companion",
        url=window_url,
        width=args.width,
        height=args.height,
        min_size=(args.min_width, args.min_height),
        resizable=True,
        on_top=args.on_top,
        background_color="#0b0f19",
        text_select=True
        # Note: js_api omitted intentionally; desktop integration uses REST endpoints
    )

    # Attach dynamic Per-Monitor V2 DPI scaling handler
    setup_dpi_scaling_handler(window, args.width, args.height, args.min_width, args.min_height)

    # Register window reference for REST endpoints (/api/desktop/toggle-pin)
    register_desktop_window_getter(lambda: window)

    # Run native window loop (blocking on main thread)
    window_failed = False
    try:
        webview.start(debug=args.debug)
    except Exception as e:
        window_failed = True
        show_startup_error(f"The companion window could not be opened: {e}")
    finally:
        logger.info("Shutting down capture loop and server..." if window_failed
                    else "Window closed. Shutting down capture loop and server...")
        try:
            if capture_service.is_running():
                capture_service.stop()
        except Exception:
            pass
        server.should_exit = True
        server_thread.join(timeout=2.0)
        logger.info("Shutdown complete.")
    if window_failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
