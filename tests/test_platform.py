"""Screen capture, window discovery, Windows QoS helpers and paths (no hardware-specific assumptions)."""
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

from ffvii_companion.engine.capture import ScreenCaptureEngine
from ffvii_companion.engine.window_finder import FFVIIWindowFinder
from ffvii_companion import paths
from ffvii_companion.engine import win_qos


class TestUserDataDir(unittest.TestCase):
    def test_runs_from_source_write_to_dev_home(self):
        # The project root used to be the writable folder: dev runs created logs/ there and saved
        # settings over the tracked defaults
        project = paths.project_root()
        self.assertTrue((project / "pyproject.toml").exists(), project)
        with mock.patch.dict(os.environ):
            os.environ.pop(paths.USER_DIR_ENV, None)
            self.assertEqual(paths.get_user_data_dir(), project / paths.DEV_HOME_DIRNAME)

    def test_packaged_app_writes_next_to_the_executable(self):
        exe = Path(os.environ[paths.USER_DIR_ENV]) / "FFVII_Companion" / "FFVII_Companion.exe"
        with mock.patch.dict(os.environ), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(exe)):
            os.environ.pop(paths.USER_DIR_ENV, None)
            self.assertEqual(paths.get_user_data_dir(), exe.resolve().parent)

    def test_environment_override_wins(self):
        self.assertEqual(paths.get_user_data_dir(), Path(os.environ[paths.USER_DIR_ENV]).resolve())


class TestBuildInstall(unittest.TestCase):
    def test_install_bundle_keeps_user_folders(self):
        # GEMINI-20261007-10: data/, logs/ and reports/ next to the EXE survive a rebuild
        import tempfile
        from pathlib import Path
        from build_tools.build_exe import install_bundle
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            (src / "_internal").mkdir(parents=True)
            (src / "app.exe").write_text("new")
            (src / "_internal" / "lib.dll").write_text("new")
            for folder in ("data", "logs", "reports/20261007-190000"):
                (dst / folder).mkdir(parents=True)
            (dst / "reports/20261007-190000/manifest.json").write_text("{}")
            (dst / "data" / "profile.json").write_text("{}")
            (dst / "stale.dll").write_text("old")
            install_bundle(src, dst)
            self.assertTrue((dst / "reports/20261007-190000/manifest.json").exists())
            self.assertTrue((dst / "data" / "profile.json").exists())
            self.assertTrue((dst / "app.exe").exists())
            self.assertFalse((dst / "stale.dll").exists())

    @unittest.skipUnless(os.name == "nt", "Windows read-only folder attribute")
    def test_install_bundle_removes_read_only_stale_folders(self):
        # OneDrive marks the folders in dist/ read-only, and Windows refuses to remove a read-only
        # folder: the first build after the move to src/ stopped on the old _internal/server/static
        import stat
        import tempfile
        from build_tools.build_exe import install_bundle
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            (src / "_internal" / "ffvii_companion").mkdir(parents=True)
            (src / "_internal" / "ffvii_companion" / "index.html").write_text("new")
            old_static = dst / "_internal" / "server" / "static"
            old_static.mkdir(parents=True)
            (old_static / "index.html").write_text("old")
            for folder in (old_static, old_static.parent, dst / "_internal"):
                os.chmod(folder, stat.S_IREAD)
            install_bundle(src, dst)
            self.assertFalse((dst / "_internal" / "server").exists())
            self.assertTrue((dst / "_internal" / "ffvii_companion" / "index.html").exists())


class TestReleasePackage(unittest.TestCase):
    def test_release_leaves_out_the_builders_data(self):
        # dist/FFVII_Companion/ also holds the settings, profile, logs and reports of whoever built
        # it; a release zip must contain only the build (the bundle's own _internal/.../data stays)
        import tempfile
        from build_tools.package_release import release_files
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            for rel in ("FFVII_Companion.exe", "_internal/python311.dll", "_internal/ffvii_companion/data/enemies.json",
                        "data/profile.json", "logs/companion.log", "reports/20261011-120000/manifest.json"):
                (app / rel).parent.mkdir(parents=True, exist_ok=True)
                (app / rel).write_text("x")
            self.assertEqual(sorted(rel.as_posix() for _, rel in release_files(app)),
                             ["FFVII_Companion.exe", "_internal/ffvii_companion/data/enemies.json",
                              "_internal/python311.dll"])

    def test_notices_list_the_distributions_of_the_bundled_modules(self):
        # Import names map to their distributions (cv2 -> opencv-python); the standard library and
        # the app itself (under LICENSE.txt) have no entry; PyInstaller's bootloader is in every EXE
        from build_tools.package_release import bundled_distributions, third_party_notices
        dists = bundled_distributions({"numpy", "cv2", "onnxruntime", "webview", "ffvii_companion", "json"})
        self.assertEqual([d.metadata["Name"].lower() for d in dists],
                         ["numpy", "onnxruntime", "opencv-python", "pyinstaller", "pywebview"])
        text = third_party_notices("0.0.0", dists)
        self.assertIn("==== Python ", text)
        self.assertIn("--- ThirdPartyNotices.txt ---", text)  # onnxruntime's, outside its .dist-info

    @unittest.skipUnless(os.name == "nt", "Win32 message box")
    def test_startup_error_is_shown_not_only_logged(self):
        # Windowed launches have no console: a failed window start used to exit silently
        from ffvii_companion import desktop
        with mock.patch("ctypes.windll.user32.MessageBoxW") as box, self.assertLogs("ffvii-desktop", "ERROR"):
            desktop.show_startup_error("The companion window could not be opened: boom")
        self.assertIn("could not be opened: boom", box.call_args.args[1])
        self.assertIn("companion.log", box.call_args.args[1])


class TestScreenCapture(unittest.TestCase):
    def test_monitor_discovery(self):
        cap = ScreenCaptureEngine()
        self.assertGreater(len(cap.monitors), 1)  # [0] is the virtual screen spanning all monitors
        self.assertTrue(1 <= cap.monitor_index < len(cap.monitors))

    def test_out_of_bounds_monitor_index_falls_back(self):
        engine = ScreenCaptureEngine(monitor_index=999)
        self.assertLess(engine.monitor_index, len(engine.monitors))
        self.assertGreaterEqual(engine.monitor_index, 0)

    def test_grab_matches_requested_region(self):
        cap = ScreenCaptureEngine()
        mon = cap.monitors[cap.monitor_index]
        width, height = min(320, mon["width"]), min(200, mon["height"])
        frame = cap.grab({"top": mon["top"], "left": mon["left"], "width": width, "height": height})
        self.assertEqual(frame.shape, (height, width, 4))  # BGRA, as the capture loop receives it


class TestWindowFinder(unittest.TestCase):
    def test_window_finder_safe_run(self):
        win = FFVIIWindowFinder().find_ffvii_window()
        self.assertTrue(win is None or isinstance(win, dict))
        if win:
            for key in ("hwnd", "title", "left", "top", "width", "height"):
                self.assertIn(key, win)

    def test_process_name_matching(self):
        for name in ("ff7_en.exe", "FF7.exe", "ff7_de.exe", "ffvii.exe", "ff7_esr.exe"):
            self.assertTrue(FFVIIWindowFinder.is_ffvii_process_name(name), name)
        for name in ("7th Heaven.exe", "chrome.exe", "python.exe", "steam.exe"):
            self.assertFalse(FFVIIWindowFinder.is_ffvii_process_name(name), name)


@unittest.skipUnless(sys.platform == "win32", "Windows scheduling APIs")
class TestPortProbe(unittest.TestCase):
    def test_wildcard_host_detects_listener(self):
        # GEMINI-20261007-05: connecting to 0.0.0.0 always fails on Winsock
        import socket
        from ffvii_companion.desktop import is_port_in_use
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(5)
            port = srv.getsockname()[1]
            self.assertTrue(is_port_in_use(port, "0.0.0.0"))
            self.assertTrue(is_port_in_use(port, "127.0.0.1"))

class TestWinQoS(unittest.TestCase):
    def test_thread_qos_in_worker_thread(self):
        results = []
        t = threading.Thread(target=lambda: results.extend([
            win_qos.set_current_thread_qos(efficient=True),
            win_qos.set_current_thread_qos(efficient=False),
        ]))
        t.start()
        t.join()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(isinstance(r, bool) for r in results))


if __name__ == "__main__":
    unittest.main()
