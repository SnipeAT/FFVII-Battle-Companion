import json
import unittest
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from ffvii_companion.server.main import app
from ffvii_companion.engine.calibration import calibration_manager

# The server answers only IP addresses, localhost and the PC's name (TestClient's default host,
# "testserver", is not one of them)
BASE_URL = "http://127.0.0.1"
WS_URL = "ws://127.0.0.1/ws"  # websocket_connect ignores base_url for relative URLs


class TestRestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, base_url=BASE_URL)

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": True})

    def test_dashboard_served(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("TACTICAL COMPANION", resp.text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)

    def test_dashboard_files_are_revalidated(self):
        # Without Cache-Control, browsers reuse a heuristically cached app.js after an update
        # and run it against the newer server; an unchanged file costs a 304
        for path in ("/", "/static/app.js", "/static/style.css"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            self.assertEqual(resp.headers.get("cache-control"), "no-cache", path)
            etag = resp.headers.get("etag")
            self.assertTrue(etag, path)
            again = self.client.get(path, headers={"If-None-Match": etag})
            self.assertEqual(again.status_code, 304, path)
            self.assertEqual(again.headers.get("cache-control"), "no-cache", path)

    def test_api_version_is_the_package_version(self):
        # The API used to carry its own copy of the version number
        from ffvii_companion import __version__
        self.assertEqual(self.client.get("/openapi.json").json()["info"]["version"], __version__)

    def test_dashboard_version_follows_the_files(self):
        import tempfile
        from pathlib import Path
        from ffvii_companion.server.main import DASHBOARD_VERSION, STATIC_DIR, dashboard_version
        self.assertEqual(DASHBOARD_VERSION, dashboard_version(STATIC_DIR))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "app.js").write_text("let a = 1;", encoding="utf-8")
            first = dashboard_version(folder)
            self.assertEqual(dashboard_version(folder), first)
            (folder / "app.js").write_text("let a = 2;", encoding="utf-8")
            changed = dashboard_version(folder)
            self.assertNotEqual(changed, first)
            (folder / "app.js").rename(folder / "main.js")
            self.assertNotEqual(dashboard_version(folder), changed)

    def test_no_cross_origin_access(self):
        resp = self.client.get("/api/capture/status", headers={"Origin": "https://example.com"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in resp.headers.keys()})

    def test_cross_origin_state_changes_are_rejected(self):
        foreign = self.client.post("/api/capture/stop", headers={"Origin": "https://example.com"})
        self.assertEqual(foreign.status_code, 403)
        same_origin = self.client.post("/api/capture/stop", headers={"Origin": BASE_URL})
        self.assertEqual(same_origin.status_code, 200)
        no_origin = self.client.post("/api/capture/stop")
        self.assertEqual(no_origin.status_code, 200)

    def test_cross_origin_requests_from_another_port_are_rejected(self):
        # CLAUDE-20261012-A09: a mutation comparing only host names passed every test
        for origin in ("http://127.0.0.1:8080", "http://localhost", "null"):
            resp = self.client.post("/api/capture/stop", headers={"Origin": origin})
            self.assertEqual(resp.status_code, 403, origin)

    def test_dns_rebinding_host_names_are_rejected(self):
        # CLAUDE-20261012-A01: a page that points its own name at 127.0.0.1 sends that name as
        # Host and as Origin, which match each other
        rebound = "http://rebind.attacker.example:59314"
        client = TestClient(app, base_url=rebound)
        self.assertEqual(client.post("/api/capture/stop", headers={"Origin": rebound}).status_code, 403)
        # it could also read every response, the battle and the profile included
        self.assertEqual(client.get("/api/profile").status_code, 403)
        self.assertEqual(client.get("/").status_code, 403)
        # GEMINI-20261014-04: the page names the fix, so nobody reaches for "*" to fix connections
        self.assertIn("dashboard_host_names", client.get("/").text)
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("ws://rebind.attacker.example:59314/ws", headers={"Origin": rebound}) as ws:
                ws.receive_json()

    def test_dashboard_hosts_are_allowed(self):
        import socket
        for base in ("http://127.0.0.1:59314", "http://localhost:59314", "http://[::1]:59314",
                     "http://192.168.1.20:59314", "http://companion.localhost",
                     f"http://{socket.gethostname()}:59314", f"http://{socket.gethostname()}.local:59314"):
            client = TestClient(app, base_url=base)
            self.assertEqual(client.get("/api/health").status_code, 200, base)
            self.assertEqual(client.post("/api/capture/stop", headers={"Origin": base}).status_code, 200, base)

    def test_dashboard_host_names_setting(self):
        from ffvii_companion.server.main import is_allowed_host
        self.assertFalse(is_allowed_host("companion.lan:59314", []))
        self.assertTrue(is_allowed_host("Companion.LAN.:59314", ["companion.lan"]))
        self.assertTrue(is_allowed_host("anything.example", ["*"]))
        self.assertFalse(is_allowed_host("", []))
        self.assertFalse(is_allowed_host("[not-an-ip", []))
        config = calibration_manager.get_config()
        try:
            config["dashboard_host_names"] = ["companion.lan"]
            client = TestClient(app, base_url="http://companion.lan:59314")
            self.assertEqual(client.get("/api/health").status_code, 200)
            # "*" turns the protection off: the log says so at every start
            config["dashboard_host_names"] = ["*"]
            with self.assertLogs("ffvii-server", "WARNING") as logs:
                with TestClient(app, base_url=BASE_URL):
                    pass
            self.assertIn("DNS rebinding protection is off", "\n".join(logs.output))
        finally:
            config["dashboard_host_names"] = []

    def test_failed_saves_answer_503_and_change_nothing(self):
        # CLAUDE-20261012-A04: a failed save answered 500 but kept the new value in memory
        from unittest import mock
        from ffvii_companion.engine.player_profile import player_profile
        level = player_profile.get()["party_level"]
        idle_fps = calibration_manager.get_config()["idle_fps"]
        failing = mock.patch("os.replace", side_effect=PermissionError("held by another program"))
        with failing:
            for resp in (
                self.client.post("/api/profile", json={"party_level": level % 98 + 1}),
                self.client.post("/api/calibration/config", json={"idle_fps": 4.0}),
                self.client.post("/api/calibration/preset/full_game_window"),
            ):
                self.assertEqual(resp.status_code, 503)
                self.assertNotIn("held by another program", resp.text)
        self.assertEqual(self.client.get("/api/profile").json()["party_level"], level)
        self.assertEqual(calibration_manager.get_config()["idle_fps"], idle_fps)

    def test_dashboard_host_names_cannot_be_changed_through_the_api(self):
        resp = self.client.post("/api/calibration/config", json={"dashboard_host_names": ["*"]})
        self.assertEqual(resp.status_code, 422)
        # the old name is not a setting any more: dropped like any unknown key
        self.client.post("/api/calibration/config", json={"allowed_hosts": ["*"]})
        self.assertEqual(calibration_manager.get_config()["dashboard_host_names"], [])
        self.assertNotIn("allowed_hosts", calibration_manager.get_config())

    def test_cross_origin_websocket_is_rejected(self):
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(WS_URL, headers={"Origin": "https://example.com"}) as ws:
                ws.receive_json()
        with self.client.websocket_connect(WS_URL, headers={"Origin": BASE_URL}) as ws:
            receive_connect_messages(ws)

    def test_every_enemy_id_is_reachable(self):
        # GEMINI-20261007-08: an id with a slash ("ying/yang") broke the enemy routes
        from ffvii_companion.compendium import compendium
        for enemy in compendium.get_all():
            self.assertEqual(self.client.get(f"/api/enemies/{enemy['id']}/partners").status_code, 200, enemy["id"])

    def test_dashboard_fetch_urls_encode_dynamic_segments(self):
        # CodeQL #1: every interpolated segment in a dashboard fetch(`...`) URL must use encodeURIComponent
        import re
        from ffvii_companion.server.main import STATIC_DIR
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        urls = re.findall(r"fetch\(`([^`]+)`", app_js)
        self.assertGreaterEqual(len(urls), 4)
        for url in urls:
            self.assertTrue(url.startswith("/api/"), url)
            for expr in re.findall(r"\$\{([^}]+)\}", url):
                self.assertRegex(expr, r"^encodeURIComponent\(.+\)$", url)

    def test_dashboard_html_templates_escape_enemy_fields(self):
        # CodeQL #2: enemy, variant, partner and search fields interpolated into HTML templates
        # must pass through escapeHtml, and escapeHtml(0) must return "0", not ""
        from ffvii_companion.server.main import STATIC_DIR
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('if (str === null || str === undefined) return "";', app_js)
        for unescaped in (
            "LVL ${enemy.level}</div>", ">x${enemy.count}<", "${enemy.hp.", "${enemy.mp.",
            "${(enemy.exp", "${(enemy.ap", "${(enemy.gil",
            "${v.index + 1}", "${v.count}", "${v.candidates.length}",
            "${c.level}", "${c.hp.", "${c.exp.", "${c.ap.", "${c.gil.",
            'data-id="${p.id}"', 'data-id="${e.id}"', "${e.level}", "${e.hp}",
            "${perf.grab_ms}",
        ):
            self.assertNotIn(unescaped, app_js)

    def test_api_search_endpoint(self):
        resp = self.client.get("/api/search?q=soldier")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(len(data), 0)
        self.assertTrue(any("SOLDIER" in e["name"] for e in data))
        self.assertEqual(self.client.get("/api/search?q=" + "x" * 100).status_code, 422)

    def test_large_responses_are_compressed(self):
        resp = self.client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(resp.headers.get("content-encoding"), "gzip")
        plain = self.client.get("/static/app.js", headers={"Accept-Encoding": "identity"})
        self.assertIsNone(plain.headers.get("content-encoding"))
        self.assertEqual(plain.text, resp.text)  # httpx transparently decompresses

    def test_api_steal_calc_endpoint(self):
        resp = self.client.post("/api/steal-calc", json={
            "stealer_level": 50, "target_level": 25, "base_chance": 8, "sneak_glove": False
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertAlmostEqual(data["total_success_percentage"], 17.19, places=2)
        self.assertEqual(data["chance_value"], 10)
        self.assertEqual(data["item_success_percentage"], data["total_success_percentage"])
        # A later steal slot is only tried when the earlier ones fail
        later = self.client.post("/api/steal-calc", json={
            "stealer_level": 50, "target_level": 25, "base_chance": 8, "earlier_base_chances": [32]}).json()
        self.assertEqual(later["total_success_percentage"], data["total_success_percentage"])
        self.assertLess(later["item_success_percentage"], data["item_success_percentage"])
        bad_slot = self.client.post("/api/steal-calc", json={
            "stealer_level": 50, "target_level": 25, "base_chance": 8, "earlier_base_chances": [300]})
        self.assertEqual(bad_slot.status_code, 422)
        bad = self.client.post("/api/steal-calc", json={"stealer_level": 0, "target_level": 25, "base_chance": 8})
        self.assertEqual(bad.status_code, 422)

    def test_simulation_encounter_flow(self):
        resp = self.client.post("/api/simulate/encounter", json={
            "enemy_names": ["SOLDIER:3rd", "Moth Slasher"],
            "battle_title": "Shinra HQ Test Encounter"
        })
        self.assertEqual(resp.status_code, 200)

        battle_data = resp.json()["battle"]
        self.assertTrue(battle_data["in_battle"])
        names = [e["name"] for e in battle_data["enemies"]]
        self.assertEqual(sorted(names), ["Moth Slasher", "SOLDIER:3rd"])

        resp_clear = self.client.post("/api/simulate/clear")
        self.assertEqual(resp_clear.status_code, 200)
        self.assertFalse(resp_clear.json()["battle"]["in_battle"])
        self.assertEqual(self.client.post("/api/simulate/encounter", json={"enemy_names": ["x" * 101]}).status_code, 422)

    def test_simulator_presets_resolve(self):
        presets = self.client.get("/api/simulate/presets").json()
        self.assertGreater(len(presets), 0)
        from ffvii_companion.compendium import compendium
        for preset in presets:
            for name in preset["enemies"]:
                self.assertIsNotNone(compendium.match_enemy(name), name)

    def test_enemy_ai_endpoint(self):
        record, = self.client.get("/api/enemies/guard-scorpion/ai").json()["records"]
        self.assertEqual(record["enemy_ids"], [22])
        self.assertEqual(record["reacts"], ["any action"])
        self.assertIn("Main", [s["trigger"] for s in record["scripts"]])
        self.assertEqual(self.client.get("/api/enemies/does-not-exist/ai").status_code, 404)
        self.assertEqual(self.client.get("/api/enemies/manhole(lid)/ai").status_code, 404)  # no AI scripts

    def test_every_ai_box_is_served(self):
        from ffvii_companion.compendium import compendium
        for enemy in compendium.get_all():
            if enemy["has_ai"]:
                self.assertEqual(self.client.get(f"/api/enemies/{enemy['id']}/ai").status_code, 200, enemy["id"])

    def test_modal_boxes_are_sized_by_the_viewport(self):
        # A <dialog> sizes to its content: a percentage width on the box inside resolved against
        # that, so closing the AI box's Full script shrank it and squeezed the Attacks table
        import re
        from ffvii_companion.server.main import STATIC_DIR
        css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertRegex(css, r"\n\.modal-window \{[^}]*\n  width: 90vw;")
        for selector, block in re.findall(r"\n([^{}\n]*\.modal-window[^{}\n]*)\{([^}]*)\}", css):
            self.assertNotRegex(block, r"(?<!-)width:\s*[\d.]+%", selector)
        for style in re.findall(r'class="modal-window[^"]*" style="([^"]*)"', html):
            self.assertNotRegex(style, r"(?<!-)width:\s*[\d.]+%", style)

    def test_partners_endpoint(self):
        resp = self.client.get("/api/enemies/prowler/partners")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)
        self.assertEqual(self.client.get("/api/enemies/does-not-exist/partners").json(), [])

    def test_partners_endpoint_during_a_battle(self):
        # GEMINI-20261009-02: the battle narrows only the partners of its own enemies
        from ffvii_companion.server.main import capture_service
        names = lambda path: [p["name"] for p in self.client.get(path).json()]
        guard_hound = names("/api/enemies/guard-hound/partners")
        try:
            self.client.post("/api/simulate/encounter", json={"enemy_names": ["Devil Ride"]})
            self.assertEqual(names("/api/enemies/devil-ride/partners"), ["Kalm Fang"])
            self.assertEqual(names("/api/enemies/guard-hound/partners"), guard_hound)
            # Counts left in the capture loop belong to its own battle, not to a simulated one
            with capture_service._state_lock:
                capture_service.observed_counts = {"Prowler": 1}
            self.assertEqual(names("/api/enemies/devil-ride/partners"), ["Kalm Fang"])
        finally:
            with capture_service._state_lock:
                capture_service.observed_counts = {}
            self.client.post("/api/simulate/clear")

    def test_simulated_duplicates_are_counted(self):
        # GEMINI-20261009-03: a repeated name is one enemy with a count, as the capture loop reports it
        battle = self.client.post("/api/simulate/encounter", json={
            "enemy_names": ["Sample:H0512", "Sample:H0512-opt", "sample:h0512-opt"]}).json()["battle"]
        self.client.post("/api/simulate/clear")
        self.assertEqual([(e["name"], e["count"]) for e in battle["enemies"]],
                         [("Sample:h0512", 1), ("Sample:h0512-opt", 2)])
        earth = [a for a in battle["summary"]["avoid"] if a["element"] == "Earth"]
        self.assertEqual(earth[0]["nullified_by"], ["Sample:H0512-opt"])


class TestCaptureAndCalibrationApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, base_url=BASE_URL)

    def test_capture_status_endpoint(self):
        data = self.client.get("/api/capture/status").json()
        self.assertIn("status", data)
        self.assertIn("config", data)
        for key in ("combat_phase", "current_fps", "ocr_frames", "gated_frames"):
            self.assertIn(key, data["status"])

    def test_capture_start_stop_flow(self):
        resp_start = self.client.post("/api/capture/start")
        self.assertEqual(resp_start.status_code, 200)
        self.assertTrue(resp_start.json()["running"])
        self.assertTrue(self.client.get("/api/capture/status").json()["status"]["running"])
        resp_stop = self.client.post("/api/capture/stop")
        self.assertEqual(resp_stop.status_code, 200)
        self.assertFalse(resp_stop.json()["running"])

    def test_calibration_config_is_isolated_from_user_file(self):
        import os
        from pathlib import Path
        test_home = Path(os.environ["FFVII_COMPANION_HOME"]).resolve()
        self.assertTrue(calibration_manager.file_path.resolve().is_relative_to(test_home))

    def test_calibration_config_update_and_validation(self):
        cfg = self.client.get("/api/calibration/config").json()["config"]
        for key in ("idle_fps", "active_fps", "settled_fps", "ocr_threads", "text_gate", "eco_mode"):
            self.assertIn(key, cfg)
        original = {k: cfg[k] for k in ("idle_fps", "active_fps", "settled_fps")}

        ok = self.client.post("/api/calibration/config", json={
            "idle_fps": 0.4, "active_fps": 1.8, "settled_fps": 0.3, "malicious_key": "injected"
        })
        self.assertEqual(ok.status_code, 200)
        updated = ok.json()["config"]
        self.assertEqual((updated["idle_fps"], updated["active_fps"], updated["settled_fps"]), (0.4, 1.8, 0.3))
        self.assertNotIn("malicious_key", updated)

        for bad in ({"idle_fps": "fast"}, {"active_fps": 50}, {"roi": {"top_pct": 0.9, "left_pct": 0,
                                                                         "width_pct": 1, "height_pct": 0.5}},
                    {"mode": "everything"}, {"text_gate": "yes"}):
            self.assertEqual(self.client.post("/api/calibration/config", json=bad).status_code, 422, bad)

        self.client.post("/api/calibration/config", json=original)

    def test_calibration_presets(self):
        presets = self.client.get("/api/calibration/config").json()["presets"]
        for key in presets:
            resp = self.client.post(f"/api/calibration/preset/{key}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["config"]["target_menu_preset"], key)
        self.assertEqual(self.client.post("/api/calibration/preset/nope").status_code, 400)

    def test_calibration_snapshot_endpoint(self):
        # GEMINI-20261008-02: a snapshot runs a full OCR pass, so it is POST-only: SameOriginGuard
        # does not check GETs, and any web page could otherwise fire them at the companion
        self.assertEqual(self.client.get("/api/calibration/snapshot").status_code, 405)
        evil = self.client.post("/api/calibration/snapshot", headers={"Origin": "http://evil.example"})
        self.assertEqual(evil.status_code, 403)
        resp = self.client.post("/api/calibration/snapshot")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        for key in ("dimensions", "recognized_lines", "matched_enemies", "performance"):
            self.assertIn(key, data)

    def test_performance_endpoints(self):
        data = self.client.get("/api/performance").json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("performance", data)
        logs = self.client.get("/api/performance/logs?lines=10").json()
        self.assertIsInstance(logs["logs"], list)

    def test_error_responses_do_not_expose_internal_exception_details(self):
        # CodeQL #3: exception strings from reading performance.log or capturing a snapshot
        # must stay in server logs, never in HTTP responses
        from unittest import mock
        from ffvii_companion.engine.capture_service import capture_service
        from ffvii_companion.engine.profiler import perf_tracker
        perf_tracker.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        perf_tracker.log_file_path.write_text("sample log\n", encoding="utf-8")
        with mock.patch("ffvii_companion.engine.profiler.open",
                        side_effect=PermissionError("secret/path/performance.log denied")):
            logs_resp = self.client.get("/api/performance/logs?lines=10")
        self.assertEqual(logs_resp.status_code, 200)
        self.assertEqual(logs_resp.json()["logs"], ["Error reading performance log."])
        self.assertNotIn("secret/path", logs_resp.text)

        with mock.patch.object(capture_service, "capture_snapshot",
                               side_effect=RuntimeError("internal stack detail C:\\secret")):
            snap_resp = self.client.post("/api/calibration/snapshot")
        self.assertEqual(snap_resp.status_code, 500)
        self.assertEqual(snap_resp.json()["detail"], "Error capturing diagnostic snapshot")
        self.assertNotIn("internal stack detail", snap_resp.text)

    def test_desktop_status_without_window(self):
        self.assertEqual(self.client.get("/api/desktop/status").json(), {"is_desktop": False, "on_top": False})

    def test_window_rescan_reuses_one_finder(self):
        # A new finder per request opened a desktop handle each time; rescans must still scan
        import ffvii_companion.server.main as server_main
        calls = []
        finder = server_main._calibration_window_finder
        finder.find_ffvii_window = lambda force_scan=False: calls.append(force_scan)
        try:
            for _ in range(2):
                self.assertEqual(self.client.get("/api/calibration/window").json(), {"found": False, "window": None})
        finally:
            del finder.find_ffvii_window  # back to the class method
        self.assertEqual(calls, [True, True])


class TestProfileSkillsAndReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, base_url=BASE_URL)

    def setUp(self):
        from ffvii_companion.engine.player_profile import player_profile
        self.saved_profile = player_profile.get()

    def tearDown(self):
        from ffvii_companion.engine.player_profile import player_profile
        player_profile.update(self.saved_profile)
        self.client.post("/api/simulate/clear")

    def test_profile_update_and_validation(self):
        ok = self.client.post("/api/profile", json={"party_level": 42, "sneak_glove": True,
                                                    "learned_enemy_skills": ["Laser", "Big Guard"],
                                                    "unknown_key": 1})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {"party_level": 42, "sneak_glove": True,
                                     "learned_enemy_skills": ["Big Guard", "Laser"]})  # materia order
        self.assertEqual(self.client.get("/api/profile").json()["party_level"], 42)
        for bad in ({"party_level": 0}, {"party_level": "high"}, {"sneak_glove": "yes"},
                    {"learned_enemy_skills": ["Ultima"]}, {"learned_enemy_skills": "Laser"}):
            self.assertEqual(self.client.post("/api/profile", json=bad).status_code, 422, bad)

    def test_profile_change_rebroadcasts_steal_odds(self):
        self.client.post("/api/profile", json={"party_level": 10, "sneak_glove": False})
        self.client.post("/api/simulate/encounter", json={"enemy_names": ["Custom Sweeper"]})
        with self.client.websocket_connect(WS_URL) as ws:
            msgs = receive_connect_messages(ws)
            low = msgs["battle"]["enemies"][0]["steal_odds"]
            self.client.post("/api/profile", json={"sneak_glove": True})
            self.assertEqual(ws.receive_json()["type"], "PROFILE_UPDATE")
            battle = ws.receive_json()
        self.assertEqual(battle["type"], "BATTLE_UPDATE")
        self.assertEqual(battle["data"]["party"], {"party_level": 10, "sneak_glove": True})
        high = battle["data"]["enemies"][0]["steal_odds"]
        from ffvii_companion.compendium import EnemyCompendium, compendium
        sweeper = compendium.get_by_name("Custom Sweeper")
        self.assertEqual(high, EnemyCompendium.calculate_steal_rate(
            10, sweeper["level"], sweeper["steals"][0]["base_chance"], True)["total_success_percentage"])
        self.assertEqual(battle["data"]["enemies"][0]["steals"][0]["odds"], high)
        self.assertGreater(high, low)

    def test_battle_shows_the_variant_of_the_formation(self):
        # Data audit finding 1: Rude with two Attack Squads is the scene 204 record (Lv 42, 9000 HP)
        data = self.client.post("/api/simulate/encounter",
                                json={"enemy_names": ["Turks:Rude", "Attack Squad"]}).json()["battle"]
        rude = next(e for e in data["enemies"] if e["name"] == "Turks:rude")
        self.assertEqual((rude["display_name"], rude["level"], rude["hp"]), ("Turks:Rude", 42, 9000))
        self.assertTrue(rude["variant"]["resolved"])
        self.assertIsNone(rude.get("variants"))  # the full list stays in the compendium entry
        self.assertEqual(data["summary"]["formation"], {"Attack Squad": 2, "Turks:Rude": 1})

    def test_battle_carries_summary(self):
        data = self.client.post("/api/simulate/encounter", json={"enemy_names": ["Death Claw"]}).json()["battle"]
        summary = data["summary"]
        self.assertEqual(summary["enemy_skills"], [{"name": "Laser", "sources": ["Death Claw"], "manipulate": True}])
        self.assertIn("ap", summary["totals"])
        cleared = self.client.post("/api/simulate/clear").json()["battle"]
        self.assertIsNone(cleared["summary"])

    def test_enemy_skills_endpoint(self):
        from ffvii_companion.compendium import ENEMY_SKILLS
        skills = self.client.get("/api/enemy-skills").json()
        self.assertEqual([s["name"] for s in skills], list(ENEMY_SKILLS))
        laser = next(s for s in skills if s["name"] == "Laser")
        self.assertIn({"id": "death-claw", "name": "Death Claw", "manipulate": True}, laser["sources"])

    def test_misdetection_report_endpoint(self):
        from unittest import mock
        from ffvii_companion.engine.capture_service import capture_service
        from ffvii_companion.paths import get_user_data_dir
        from tests.helpers import load_fixture_bgr
        with capture_service._state_lock:
            saved = capture_service.last_ocr_frame
            capture_service.last_ocr_frame = load_fixture_bgr("devil_ride_b.png")
        try:
            # No live grab in tests: only the frame of the last OCR pass is saved
            with mock.patch.object(capture_service, "_calculate_capture_bbox", side_effect=RuntimeError("no screen")):
                resp = self.client.post("/api/report/misdetection",
                                        json={"expected": "Devil Ride x2", "note": "test"})
                with capture_service._state_lock:
                    capture_service.last_ocr_frame = None
                missing = self.client.post("/api/report/misdetection", json={})
        finally:
            with capture_service._state_lock:
                capture_service.last_ocr_frame = saved
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["files"], ["last_ocr.png", "manifest.json"])
        self.assertEqual(data["expected"], {"Devil Ride": 2})
        self.assertFalse(data["needs_review"])
        folder = get_user_data_dir() / data["folder"]
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["frames"]["last_ocr.png"]["enemies"], {"Devil Ride": 2})
        self.assertTrue((folder / "last_ocr.png").exists())
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(self.client.post("/api/report/misdetection", json={"note": "x" * 2000}).status_code, 422)


def receive_connect_messages(ws) -> dict:
    """
    The messages every client receives on connect: the version of the dashboard files, the
    battle state, the player profile, then the capture status.
    """
    version = ws.receive_json()
    battle = ws.receive_json()
    profile = ws.receive_json()
    capture = ws.receive_json()
    assert version["type"] == "DASHBOARD_VERSION", version
    assert battle["type"] == "BATTLE_UPDATE", battle
    assert profile["type"] == "PROFILE_UPDATE", profile
    assert capture["type"] == "CAPTURE_STATUS", capture
    return {"version": version["data"]["version"], "battle": battle["data"], "profile": profile["data"],
            "capture": capture["data"]}


class TestWebSocket(unittest.TestCase):
    def test_connect_sends_version_battle_profile_and_capture_status(self):
        from ffvii_companion.server.main import DASHBOARD_VERSION
        with TestClient(app, base_url=BASE_URL).websocket_connect(WS_URL) as ws:
            msgs = receive_connect_messages(ws)
        # A dashboard tab left open across an update reloads when this differs from what it first saw
        self.assertEqual(msgs["version"], DASHBOARD_VERSION)
        self.assertIn("party", msgs["battle"])
        self.assertEqual(set(msgs["profile"]), {"party_level", "sneak_glove", "learned_enemy_skills"})
        self.assertEqual(set(msgs["capture"]), {"running", "combat_phase", "current_fps"})

    def test_capture_start_and_stop_reach_every_dashboard(self):
        # Regression: the CAPTURE button changed only the dashboard it was clicked on; the desktop
        # window, a browser tab and a phone each kept showing their own on/off state
        from unittest import mock
        from ffvii_companion.engine.capture_service import capture_service
        set_running = lambda running: lambda: setattr(capture_service, "_running", running)
        # No real capture thread: it would grab the screen and could push battle messages of its own.
        # One client context: both sockets share one event loop, as under uvicorn
        with mock.patch.object(capture_service, "start", side_effect=set_running(True)), \
                mock.patch.object(capture_service, "stop", side_effect=set_running(False)), \
                TestClient(app, base_url=BASE_URL) as client, \
                client.websocket_connect(WS_URL) as desktop, client.websocket_connect(WS_URL) as phone:
            self.assertFalse(receive_connect_messages(desktop)["capture"]["running"])
            self.assertFalse(receive_connect_messages(phone)["capture"]["running"])

            started = client.post("/api/capture/start").json()
            self.assertTrue(started["running"])
            self.assertIn("combat_phase", started, "The clicking dashboard shows the phase right away")
            for ws in (desktop, phone):
                msg = ws.receive_json()
                self.assertEqual(msg["type"], "CAPTURE_STATUS")
                self.assertTrue(msg["data"]["running"])

            self.assertFalse(client.post("/api/capture/stop").json()["running"])
            for ws in (desktop, phone):
                msg = ws.receive_json()
                self.assertEqual(msg["type"], "CAPTURE_STATUS")
                self.assertFalse(msg["data"]["running"])

    def test_phase_change_is_pushed_to_clients(self):
        # Regression: hunting -> settled changes no enemy data, and the dashboard kept showing HUNTING
        from ffvii_companion.engine.capture_service import capture_service
        client = TestClient(app, base_url=BASE_URL)
        with client.websocket_connect(WS_URL) as ws:
            receive_connect_messages(ws)
            with capture_service._state_lock:
                saved = (capture_service.combat_phase, capture_service.current_fps)
                capture_service.combat_phase = "settled"
                capture_service.current_fps = 0.35
            try:
                capture_service.on_phase_changed()
                msg = ws.receive_json()
            finally:
                with capture_service._state_lock:
                    capture_service.combat_phase, capture_service.current_fps = saved
        self.assertEqual(msg, {"type": "CAPTURE_STATUS", "data": {
            "running": capture_service.is_running(), "combat_phase": "settled", "current_fps": 0.35}})

    def test_websocket_manual_select_add_and_clear(self):
        client = TestClient(app, base_url=BASE_URL)
        with client.websocket_connect(WS_URL) as ws:
            receive_connect_messages(ws)

            ws.send_json({"action": "MANUAL_SELECT", "enemy_id": "sweeper"})
            msg1 = ws.receive_json()
            self.assertEqual([e["id"] for e in msg1["data"]["enemies"]], ["sweeper"])
            self.assertIsNotNone(msg1["data"]["summary"], "Manual lookups carry the battle summary")

            # ADD_ENEMY must append, not replace
            ws.send_json({"action": "ADD_ENEMY", "enemy_id": "mp"})
            msg2 = ws.receive_json()
            self.assertEqual(sorted(e["id"] for e in msg2["data"]["enemies"]), ["mp", "sweeper"])

            # Malformed payloads are ignored without closing the socket: the next command is answered
            ws.send_text("not json")
            ws.send_text(json.dumps(["list"]))
            ws.send_json({"action": "CLEAR_BATTLE"})
            self.assertFalse(ws.receive_json()["data"]["in_battle"])

    def test_clear_battle_resets_the_capture_loop(self):
        # Regression: CLEAR reset only the server's battle; the capture loop kept its collected
        # enemies and brought them back with the next new name it read
        from ffvii_companion.compendium import compendium
        from ffvii_companion.engine.capture_service import capture_service
        # One client context, so both sockets share one event loop as under uvicorn: without it each
        # socket gets its own loop and the broadcast from the other loop can leave receive_json hanging
        with TestClient(app, base_url=BASE_URL) as client, client.websocket_connect(WS_URL) as ws, \
                client.websocket_connect(WS_URL) as phone:
            receive_connect_messages(ws)
            receive_connect_messages(phone)
            with capture_service._state_lock:
                capture_service.current_enemies = [dict(compendium.get_by_name("Prowler"), count=1)]
                capture_service.observed_counts = {"Prowler": 1}
                capture_service.current_battle_title = "Battle: Prowler"
                capture_service.combat_phase = "settled"
                capture_service.current_fps = 0.35
            ws.send_json({"action": "CLEAR_BATTLE"})
            for client_ws in (ws, phone):
                msg = client_ws.receive_json()
                self.assertEqual(msg["type"], "BATTLE_UPDATE")
                self.assertFalse(msg["data"]["in_battle"])
                self.assertEqual(msg["data"]["enemies"], [])
                # The capture button of every dashboard leaves SETTLED
                self.assertEqual((msg["data"]["combat_phase"], msg["data"]["current_fps"]), ("idle", 0.5))
        self.assertEqual(capture_service.current_enemies, [])
        self.assertEqual(capture_service.observed_counts, {})
        self.assertIsNone(capture_service.current_battle_title)
        self.assertEqual(capture_service.combat_phase, "idle")


if __name__ == "__main__":
    unittest.main()


class TestBroadcastDropsSlowClients(unittest.TestCase):
    class FakeSocket:
        def __init__(self, fail: bool):
            self.fail, self.sent, self.closed_with = fail, [], None

        async def send_text(self, text):
            if self.fail:
                raise RuntimeError("send timed out")
            self.sent.append(text)

        async def close(self, code=1000):
            self.closed_with = code

    def test_a_dropped_client_is_closed_so_it_reconnects(self):
        # CLAUDE-20261012-A02: a client dropped after one failed send stayed connected and
        # showed LIVE SYNC without getting another update
        import asyncio
        from ffvii_companion.server.main import ConnectionManager

        async def run():
            manager = ConnectionManager()
            slow, fine = self.FakeSocket(fail=True), self.FakeSocket(fail=False)
            manager.active_connections.update({slow, fine})
            await manager.broadcast({"type": "BATTLE_UPDATE"})
            await asyncio.sleep(0)  # let the close task run
            await asyncio.gather(*manager._closing)
            return manager, slow, fine

        manager, slow, fine = asyncio.run(run())
        self.assertEqual(manager.active_connections, {fine})
        self.assertEqual(slow.closed_with, 1011)
        self.assertIsNone(fine.closed_with)
        self.assertEqual(len(fine.sent), 1)
