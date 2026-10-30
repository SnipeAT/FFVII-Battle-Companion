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

    def test_build_uses_its_own_tree_and_the_build_root_override(self):
        # A second copy of the project built with this project's venv collected this project's
        # modules (editable install), and every build shared one build folder
        import contextlib
        import io
        import tempfile
        from build_tools import build_exe
        with tempfile.TemporaryDirectory() as tmp:
            build_root = Path(tmp) / "build"
            with mock.patch.dict(os.environ, {build_exe.BUILD_ROOT_ENV: str(build_root)}), \
                    mock.patch.object(build_exe.subprocess, "run", return_value=mock.Mock(returncode=1)) as run, \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                build_exe.build()
        cmd, env = run.call_args.args[0], run.call_args.kwargs["env"]
        self.assertEqual(cmd[cmd.index("--distpath") + 1], str(build_root.resolve() / "dist"))
        tree_src = Path(build_exe.__file__).resolve().parent.parent / "src"
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(tree_src))

    def test_build_refuses_a_build_root_with_other_files(self):
        # The build deletes its build root, so a mistyped FFVII_COMPANION_BUILD_ROOT must not wipe
        # a folder with other contents
        import contextlib
        import io
        import tempfile
        from build_tools import build_exe
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("keep")
            with mock.patch.dict(os.environ, {build_exe.BUILD_ROOT_ENV: tmp}), \
                    mock.patch.object(build_exe.subprocess, "run") as run, \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                build_exe.build()
            self.assertTrue((Path(tmp) / "notes.txt").exists())
        run.assert_not_called()


class TestReleasePackage(unittest.TestCase):
    def test_release_leaves_out_the_builders_data(self):
        # dist/FFVII_Companion/ also holds the settings, profile, logs and reports of whoever built
        # it; a release zip must contain only the build (the bundle's own _internal/.../data stays)
        import tempfile
        from build_tools.package_release import release_files
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            for rel in ("FFVII_Companion.exe", "_internal/python314.dll", "_internal/ffvii_companion/data/enemies.json",
                        "data/profile.json", "logs/companion.log", "reports/20261011-120000/manifest.json"):
                (app / rel).parent.mkdir(parents=True, exist_ok=True)
                (app / rel).write_text("x")
            self.assertEqual(sorted(rel.as_posix() for _, rel in release_files(app)),
                             ["FFVII_Companion.exe", "_internal/ffvii_companion/data/enemies.json",
                              "_internal/python314.dll"])

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
        self.assertIn("==== Microsoft WebView2 SDK (in pywebview) ====", text)
        # Its BSD license asks binary redistributions to reproduce the notice itself, not a link
        self.assertIn("Copyright (C) Microsoft Corporation. All rights reserved.", text)
        # The OCR models and post-processing in the app's own package are Apache-2.0, not MIT
        self.assertIn("==== PP-OCRv4 models and post-processing from RapidOCR ====", text)
        self.assertIn("Copyright (c) 2021 RapidOCR Authors. All rights reserved.", text)

    @staticmethod
    def _relative_links(page):
        """(target, linked file, #anchor) of every relative Markdown link of a page, outside code blocks."""
        import re
        text = re.sub(r"```.*?```", "", page.read_text(encoding="utf-8"), flags=re.DOTALL)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("http://", "https://")):
                continue
            path, _, anchor = target.partition("#")
            yield target, (page.parent / path).resolve() if path else page, anchor

    @staticmethod
    def _anchors(page):
        # GitHub's heading ids: lower case, punctuation dropped, spaces to hyphens
        import re
        headings = re.findall(r"^#+ (.+)$", page.read_text(encoding="utf-8"), re.MULTILINE)
        return {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}

    def test_readme_and_docs_links_work_in_the_release_zip(self):
        # The zip ships README.md and CHANGELOG.md with docs/ next to them: every relative link of
        # those pages must reach one of them (src/ is not in the zip), and every #anchor a heading
        # of its page
        root = Path(__file__).resolve().parent.parent
        in_zip = {root / "README.md", root / "CHANGELOG.md"}
        pages = [*sorted(in_zip), *sorted((root / "docs").glob("*.md"))]
        self.assertGreater(len(pages), 2)
        for page in pages:
            for target, linked, anchor in self._relative_links(page):
                with self.subTest(page=page.name, link=target):
                    self.assertTrue(linked.is_file())
                    self.assertTrue(linked in in_zip or (root / "docs") in linked.parents)
                    if anchor:
                        self.assertIn(anchor, self._anchors(linked))

    def test_repository_pages_links_work(self):
        # CONTRIBUTING.md and SECURITY.md are read on GitHub only (not in the zip): their relative
        # links must reach a file of the repository, and every #anchor a heading of its page
        root = Path(__file__).resolve().parent.parent
        for page in (root / "CONTRIBUTING.md", root / "SECURITY.md"):
            for target, linked, anchor in self._relative_links(page):
                with self.subTest(page=page.name, link=target):
                    self.assertTrue(linked.is_file())
                    self.assertIn(root, linked.parents)
                    if anchor and linked.suffix == ".md":
                        self.assertIn(anchor, self._anchors(linked))

    def test_architecture_codemap_lists_every_module(self):
        # docs/ARCHITECTURE.md's codemap is the only module map: every module of the package must be
        # in it (a folder listed without children, like ppocr/, stands for its contents), and every
        # path it lists under the package must exist
        import re
        root = Path(__file__).resolve().parent.parent
        text = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        tree = re.search(r"^## Codemap\n.*?```\n(.*?)```", text, re.DOTALL | re.MULTILINE).group(1)
        listed, stack = set(), []  # stack: (column, name) of the entries above the current one
        for line in tree.splitlines():
            entry = re.search(r"[├└]── (\S+)", line)
            if not entry:
                continue
            while stack and stack[-1][0] >= entry.start(1):
                stack.pop()
            stack.append((entry.start(1), entry.group(1)))
            listed.add("".join(name for _, name in stack))  # folder names end with "/"

        package = "src/ffvii_companion/"
        in_package = {p[len(package):] for p in listed if p.startswith(package) and p != package}
        self.assertIn("engine/capture_service.py", in_package)
        leaf_folders = {p for p in in_package if p.endswith("/")
                        and not any(q != p and q.startswith(p) for q in in_package)}
        modules = sorted(p.relative_to(root / package).as_posix() for p in (root / package).rglob("*.py")
                         if p.name != "__init__.py" and "__pycache__" not in p.parts)
        for module in modules:
            with self.subTest(module=module):
                self.assertTrue(module in in_package or any(module.startswith(f) for f in leaf_folders))
        for path in sorted(in_package):
            with self.subTest(listed=path):
                self.assertTrue((root / package / path).exists())

    @unittest.skipUnless((Path(__file__).resolve().parent.parent / ".git").exists(), "needs a Git checkout")
    def test_release_includes_the_committed_docs(self):
        from build_tools.package_release import doc_files
        files = doc_files()
        self.assertIn("docs/CONFIGURATION.md", files)
        self.assertIn("docs/images/dashboard.png", files)

    @unittest.skipUnless((Path(__file__).resolve().parent.parent / ".git").exists(), "needs a Git checkout")
    def test_release_includes_the_license_guide_and_lan_launcher(self):
        # Dropping one of them from the zip passed every test (GEMINI-20261014-01)
        from build_tools.package_release import project_files
        files = dict((name, path) for path, name in project_files())
        for name in ("LICENSE.txt", "README.md", "CHANGELOG.md", "docs/CONFIGURATION.md", "Launch_LAN_Mode.bat"):
            with self.subTest(name=name):
                self.assertIn(name, files)
                self.assertTrue(files[name].is_file())

    def test_build_writes_the_exe_config(self):
        # Files extracted from a downloaded zip that was not unblocked carry Zone.Identifier, and
        # .NET then refused to load pywebview's Python.Runtime.dll: the EXE exited without a window
        import tempfile
        from build_tools.build_exe import write_exe_config
        with tempfile.TemporaryDirectory() as tmp:
            write_exe_config(Path(tmp))
            config = (Path(tmp) / "FFVII_Companion.exe.config").read_text(encoding="utf-8")
        self.assertIn('<loadFromRemoteSources enabled="true"/>', config)

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

    def test_companion_process_is_not_the_game(self):
        # The packaged FFVII_Companion.exe matches the ffvii prefix: with the game closed, the
        # finder enumerated the companion's own windows on every scan instead of backing off, and
        # only the "COMPANION" title exclusion kept it from capturing itself (code review 2026-10-30)
        import psutil
        from ffvii_companion.engine import window_finder as module

        class Proc:
            info = {"pid": os.getpid(), "name": "FFVII_Companion.exe"}

        user32 = mock.Mock()
        user32.FindWindowW.return_value = 0
        finder = FFVIIWindowFinder()
        with mock.patch.object(module, "USER32", user32), \
                mock.patch.object(psutil, "process_iter", return_value=[Proc()]):
            self.assertIsNone(finder.find_ffvii_window(force_scan=True))
        user32.EnumWindows.assert_not_called()
        self.assertGreater(finder.scan_interval, 1.0, "backs off: no game process")


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

    def test_other_hosts(self):
        # CLAUDE-20261012-A06: --host ::1 raised in is_port_in_use, and the health check always
        # polled 127.0.0.1, so a server bound to one LAN address never counted as started
        import socket
        from ffvii_companion.desktop import http_url, is_port_in_use
        self.assertEqual(http_url("0.0.0.0", 59314, "/api/health"), "http://127.0.0.1:59314/api/health")
        self.assertEqual(http_url("::", 59314), "http://[::1]:59314")
        self.assertEqual(http_url("::1", 59314), "http://[::1]:59314")
        self.assertEqual(http_url("192.168.1.5", 59315), "http://192.168.1.5:59315")
        self.assertFalse(is_port_in_use(59314, "not a host name"))
        if socket.has_ipv6:
            with socket.socket(socket.AF_INET6) as srv:
                try:
                    srv.bind(("::1", 0))
                except OSError:
                    self.skipTest("no IPv6 loopback")
                srv.listen(5)
                port = srv.getsockname()[1]
                self.assertTrue(is_port_in_use(port, "::1"))
                self.assertTrue(is_port_in_use(port, "::"))

    def test_health_check_bypasses_proxies(self):
        # urllib sent the health check of 127.0.0.1 to the proxy of the Windows settings (their
        # "<local>" exception does not cover it), so with a system proxy the start failed after 6 s
        # (code review 2026-10-30). A proxy from the environment takes the same path.
        import http.server
        import socket
        from ffvii_companion.desktop import wait_for_server

        class Health(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_proxy = f"http://127.0.0.1:{probe.getsockname()[1]}"
        server = http.server.HTTPServer(("127.0.0.1", 0), Health)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/api/health"
            with mock.patch.dict(os.environ, {"HTTP_PROXY": dead_proxy, "NO_PROXY": ""}):
                self.assertTrue(wait_for_server(url, timeout=2.0))
        finally:
            server.shutdown()
            server.server_close()

    def test_second_launch_exits_when_a_companion_answers(self):
        # GEMINI-20261007-06: a second launch took the next free port and ran a second capture
        # loop on the same game window (failing at the next port also keeps a broken main() from
        # starting a server and a window here)
        from ffvii_companion import desktop
        with mock.patch.object(sys, "argv", ["FFVII_Companion", "--no-capture", "--normal-priority"]), \
                mock.patch.object(desktop, "setup_file_logging"), \
                mock.patch.object(desktop, "is_port_in_use", return_value=True), \
                mock.patch.object(desktop, "wait_for_server", return_value=True) as health, \
                mock.patch.object(desktop, "find_available_port", side_effect=AssertionError("took another port")):
            with self.assertRaises(SystemExit) as stop:
                desktop.main()
        self.assertEqual(stop.exception.code, 0)
        health.assert_called_once_with("http://127.0.0.1:59314/api/health", timeout=0.5)

    def test_no_free_port_is_reported(self):
        # With every port of the range taken, find_available_port returned the first one, taken
        # too, and the start failed after 6 s with a message about the server not starting
        from ffvii_companion import desktop
        with mock.patch.object(desktop, "is_port_in_use", return_value=True):
            self.assertIsNone(desktop.find_available_port(59314))
        with mock.patch.object(sys, "argv", ["FFVII_Companion", "--no-capture", "--normal-priority"]), \
                mock.patch.object(desktop, "setup_file_logging"), \
                mock.patch.object(desktop, "is_port_in_use", return_value=True), \
                mock.patch.object(desktop, "wait_for_server", return_value=False), \
                mock.patch.object(desktop, "show_startup_error") as error, \
                mock.patch.object(desktop.uvicorn, "Server", side_effect=AssertionError("started a server")):
            with self.assertRaises(SystemExit) as stop:
                desktop.main()
        self.assertEqual(stop.exception.code, 1)
        self.assertIn("Ports 59314 to 59363 are all in use", error.call_args[0][0])

    def test_ports_past_65535_are_never_used(self):
        # Windows wraps a port past 65535 around (65536 connects to port 0), so is_port_in_use
        # called it free: the search, or a --port that high, ended in a server that cannot bind
        from ffvii_companion import desktop
        taken_up_to_the_last = mock.patch.object(desktop, "is_port_in_use", side_effect=lambda port, host: port <= 65535)
        with taken_up_to_the_last:
            self.assertIsNone(desktop.find_available_port(65530))
        with mock.patch.object(sys, "argv", ["FFVII_Companion", "--port", "70000", "--no-capture", "--normal-priority"]), \
                mock.patch.object(desktop, "setup_file_logging"), \
                mock.patch.object(desktop, "is_port_in_use", side_effect=AssertionError("probed the port")), \
                mock.patch.object(desktop, "show_startup_error") as error, \
                mock.patch.object(desktop.uvicorn, "Server", side_effect=AssertionError("started a server")):
            with self.assertRaises(SystemExit) as stop:
                desktop.main()
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("--port 70000 is not a port number", error.call_args[0][0])

    def test_normal_priority_reaches_the_server_thread(self):
        # The server thread set itself to below-normal priority and EcoQoS even with
        # --normal-priority (code review 2026-10-30)
        import time
        from ffvii_companion import desktop
        for argv, expected in ((["--normal-priority"], []), ([], [mock.call(efficient=True, below_normal=True)])):
            with self.subTest(argv=argv):
                with mock.patch.object(sys, "argv", ["FFVII_Companion", "--no-capture", *argv]), \
                        mock.patch.object(desktop, "setup_file_logging"), \
                        mock.patch.object(desktop.win_qos, "apply_process_efficiency_mode", return_value=False), \
                        mock.patch.object(desktop.win_qos, "set_current_thread_qos") as thread_qos, \
                        mock.patch.object(desktop, "is_port_in_use", return_value=False), \
                        mock.patch.object(desktop, "wait_for_server", return_value=True), \
                        mock.patch.object(desktop.uvicorn, "Server") as server, \
                        mock.patch.object(desktop.webview, "create_window", side_effect=AssertionError("window")):
                    with self.assertRaises(AssertionError):
                        desktop.main()
                    deadline = time.monotonic() + 5
                    while not server.return_value.run.called and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(server.return_value.run.called)
                    self.assertEqual(thread_qos.call_args_list, expected)

    def test_desktop_window_opens_the_dashboard_marked_as_desktop(self):
        # PIN pins the window on the PC: the dashboard shows it only when opened with ?desktop=1,
        # which only the desktop window does (phones and tablets on the LAN showed it too)
        from ffvii_companion import desktop
        with mock.patch.object(sys, "argv", ["FFVII_Companion", "--no-capture", "--normal-priority"]), \
                mock.patch.object(desktop, "setup_file_logging"), \
                mock.patch.object(desktop, "is_port_in_use", return_value=False), \
                mock.patch.object(desktop, "wait_for_server", return_value=True), \
                mock.patch.object(desktop.uvicorn, "Server"), \
                mock.patch.object(desktop.webview, "create_window", side_effect=AssertionError("window")) as window:
            with self.assertRaises(AssertionError):
                desktop.main()
        self.assertEqual(window.call_args.kwargs["url"], "http://127.0.0.1:59314/?desktop=1")

@unittest.skipUnless(os.name == "nt", "Windows batch file")
class TestLanLauncher(unittest.TestCase):
    def run_launcher(self, *args):
        # A copy in an empty folder finds neither the EXE nor the virtual environment: it prints
        # the address, reports that, and its pause reads end of input
        import shutil
        import subprocess
        import tempfile
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(root / "Launch_LAN_Mode.bat", tmp)
            result = subprocess.run(["cmd", "/c", str(Path(tmp) / "Launch_LAN_Mode.bat"), *args],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertIn("Could not find compiled executable", result.stdout)
        return result.stdout

    def test_prints_the_port_the_companion_uses(self):
        # It printed 59314 when a --port flag passed on to the companion asked for another
        # (GEMINI-20261014-02)
        self.assertIn("(0.0.0.0:59314)", self.run_launcher())
        for args in (["--port", "59420"], ["--on-top", "--port=59420"]):
            with self.subTest(args=args):
                output = self.run_launcher(*args)
                self.assertIn("(0.0.0.0:59420)", output)
                self.assertNotIn("59314", output)


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
