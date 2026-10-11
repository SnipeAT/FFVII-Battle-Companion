"""
Windows scheduling helpers: thread/process priority and EcoQoS (power throttling).

EcoQoS ("Efficiency mode" in Task Manager) asks the Windows scheduler to run a
thread on efficiency cores at reduced clock speed. Combined with below-normal
priority it keeps the companion off the performance cores the game uses.

All functions are no-ops that return False on non-Windows platforms or when the
running Windows version does not support the requested setting.
"""
import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger("ffvii-qos")

IS_WINDOWS = sys.platform == "win32"

THREAD_PRIORITY_BELOW_NORMAL = -1
THREAD_PRIORITY_NORMAL = 0
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

# THREAD_INFORMATION_CLASS / PROCESS_INFORMATION_CLASS values
_THREAD_POWER_THROTTLING = 3
_PROCESS_POWER_THROTTLING = 4

_POWER_THROTTLING_CURRENT_VERSION = 1
_POWER_THROTTLING_EXECUTION_SPEED = 0x1
_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4


class _POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


_k32 = None


def _kernel32():
    """Private kernel32 handle with explicit prototypes (does not touch ctypes.windll globals)."""
    global _k32
    if _k32 is None and IS_WINDOWS:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentThread.restype = wintypes.HANDLE
        k.GetCurrentThread.argtypes = []
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetCurrentProcess.argtypes = []
        k.SetThreadPriority.restype = wintypes.BOOL
        k.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        k.SetPriorityClass.restype = wintypes.BOOL
        k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.SetThreadInformation.restype = wintypes.BOOL
        k.SetThreadInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.SetProcessInformation.restype = wintypes.BOOL
        k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        _k32 = k
    return _k32


def set_current_thread_qos(efficient: bool, below_normal: bool = True) -> bool:
    """
    Sets the calling thread's priority and EcoQoS state.
    efficient=True schedules the thread on efficiency cores at low clocks;
    efficient=False explicitly opts the thread out (full-speed cores) even when
    the process as a whole runs in efficiency mode.
    Returns True when the power-throttling state was applied.
    """
    k32 = _kernel32()
    if k32 is None:
        return False
    try:
        handle = k32.GetCurrentThread()  # pseudo-handle, no CloseHandle needed
        k32.SetThreadPriority(handle, THREAD_PRIORITY_BELOW_NORMAL if below_normal else THREAD_PRIORITY_NORMAL)
        state = _POWER_THROTTLING_STATE(
            _POWER_THROTTLING_CURRENT_VERSION,
            _POWER_THROTTLING_EXECUTION_SPEED,
            _POWER_THROTTLING_EXECUTION_SPEED if efficient else 0,
        )
        return bool(k32.SetThreadInformation(handle, _THREAD_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)))
    except (AttributeError, OSError) as exc:  # pre-Windows 10 1709 kernels lack SetThreadInformation
        logger.debug(f"Thread QoS unavailable: {exc}")
        return False


def apply_process_efficiency_mode() -> bool:
    """
    Puts the whole process in Windows "Efficiency mode": below-normal priority class
    plus EcoQoS for every thread (including ONNX Runtime / OpenCV / WebView2 host threads),
    and ignores timer-resolution requests so the process cannot raise the system timer rate.
    Child processes (the WebView2 runtime) inherit the below-normal priority class.
    Returns True when the power-throttling state was applied.
    """
    k32 = _kernel32()
    if k32 is None:
        return False
    try:
        process = k32.GetCurrentProcess()
        k32.SetPriorityClass(process, BELOW_NORMAL_PRIORITY_CLASS)
        mask = _POWER_THROTTLING_EXECUTION_SPEED | _POWER_THROTTLING_IGNORE_TIMER_RESOLUTION
        state = _POWER_THROTTLING_STATE(_POWER_THROTTLING_CURRENT_VERSION, mask, mask)
        ok = bool(k32.SetProcessInformation(process, _PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)))
        if not ok:
            # Older builds reject IGNORE_TIMER_RESOLUTION; retry with execution speed only.
            state = _POWER_THROTTLING_STATE(_POWER_THROTTLING_CURRENT_VERSION,
                                            _POWER_THROTTLING_EXECUTION_SPEED, _POWER_THROTTLING_EXECUTION_SPEED)
            ok = bool(k32.SetProcessInformation(process, _PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)))
        return ok
    except (AttributeError, OSError) as exc:
        logger.debug(f"Process efficiency mode unavailable: {exc}")
        return False
