"""
The FastAPI app: REST API, the /ws WebSocket hub and the dashboard files.

- Wires the capture loop's callbacks (handle_live_battle_detected / _cleared,
  handle_capture_phase_changed) to broadcasts. They run on the capture thread (or the worker
  thread of a CLEAR), so they go through ConnectionManager.threadsafe_broadcast() onto the
  server's event loop.
- `current_battle` (guarded by battle_lock) is what the dashboards see: set by the capture loop,
  manual lookups, added partners and the simulator. Every battle message goes through
  battle_data(): the scene.bin variant of each enemy for this battle, steal odds at the profile's
  party level, the battle summary.
- On connect, a WebSocket client receives DASHBOARD_VERSION, BATTLE_UPDATE, PROFILE_UPDATE and
  CAPTURE_STATUS, in that order; it sends MANUAL_SELECT, ADD_ENEMY and CLEAR_BATTLE.
- SameOriginGuard rejects requests to host names that are not allowed (DNS rebinding) and
  cross-origin state-changing requests and WebSockets; there is no CORS. GET endpoints must not
  change state.
- DashboardFiles serves server/static/ with Cache-Control: no-cache.

docs/ARCHITECTURE.md describes the messages and the security boundary.
"""
import hashlib
import ipaddress
import json
import socket
import time
import logging
import threading
from typing import List, Set, Optional, Dict, Any
from pathlib import Path
from contextlib import asynccontextmanager

from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, PlainTextResponse
from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import Headers
from starlette.websockets import WebSocketClose

import asyncio
from ffvii_companion import __version__
from ffvii_companion.compendium import compendium
from ffvii_companion.server.models import (
    AiInfo, Enemy, BattleState, StealCalcRequest, StealCalcResponse, DetectSimulateRequest, MisdetectionReportRequest
)
from ffvii_companion.engine.capture_service import capture_service
from ffvii_companion.engine.calibration import calibration_manager, PRESETS, ConfigValidationError
from ffvii_companion.engine.detection_report import ReportError
from ffvii_companion.engine.player_profile import player_profile, ProfileValidationError
from ffvii_companion.engine.window_finder import FFVIIWindowFinder
from ffvii_companion.engine.profiler import perf_tracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ffvii-server")

@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.loop = asyncio.get_running_loop()
    if "*" in calibration_manager.get_config()["dashboard_host_names"]:
        logger.warning('dashboard_host_names holds "*": any host name is answered, the DNS rebinding protection is off')
    yield
    if capture_service.is_running():
        capture_service.stop()

app = FastAPI(title="FFVII Tactical Battle Companion", version=__version__, lifespan=lifespan)


def _host_name(host_header: str) -> str:
    """The host name of a Host header ("Name:59314", "[::1]:59314"), lowercased, without the port
    or a trailing dot; empty when it cannot be parsed."""
    try:
        return (urlsplit("//" + host_header).hostname or "").rstrip(".")
    except ValueError:
        return ""


def is_allowed_host(host_header: str, host_names: List[str]) -> bool:
    """
    Whether a request sent to this Host may be answered. A DNS-rebinding web page reaches the
    companion under its own host name (one it points at 127.0.0.1 after the page loaded), so the
    browser sends that name as Host and as Origin, which then match: only the Host shows it. The
    dashboard is always opened under an IP address, localhost (or a *.localhost name, which
    browsers resolve to the PC themselves) or the PC's name (also with ".local", as tablets find
    it on the LAN); dashboard_host_names (settings file) adds names, "*" allows any. It decides
    which addresses the dashboard may be opened under, not which devices may connect.
    """
    if "*" in host_names:
        return True
    name = _host_name(host_header)
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name == "localhost" or name.endswith(".localhost") or name in _PC_NAMES:
        return True
    return name in host_names


_PC_NAMES = {socket.gethostname().lower(), socket.gethostname().lower() + ".local"}

# What a browser shows when the dashboard is opened under a name that is not allowed. A device
# that cannot reach the companion at all never gets here (README, "Troubleshooting").
HOST_NAME_REJECTED = ("Host name not allowed. Open the dashboard by this PC's IP address, or add "
                      "this name to dashboard_host_names in the settings file (see the companion's "
                      "Configuration page).")


class SameOriginGuard:
    """
    Rejects requests that web pages of other sites could send:
    - every request (and WebSocket) sent to a host name that is not allowed (is_allowed_host):
      a DNS-rebinding page would otherwise be same-origin with the companion and could read
      every response, not only change state;
    - unsafe HTTP methods (POST, ...) and WebSocket connections whose Origin header does not
      match the Host they were sent to. Browsers do not apply CORS to simple POSTs or
      WebSockets, so without this check any web page open on the PC (or LAN) could stop
      capture, switch presets or drive the /ws socket.
    Requests without an Origin header (CLI simulator, scripts, tests) and all same-origin
    dashboard requests to an allowed host pass through unchanged.
    """
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            origin = headers.get("origin")
            unsafe = scope["type"] == "websocket" or scope.get("method", "GET") not in self.SAFE_METHODS
            reason = message = None
            if not is_allowed_host(host, calibration_manager.get_config()["dashboard_host_names"]):
                reason = (f"{scope['type']} request to host name {host[:100]!r}: not an IP address, "
                          "localhost, this PC's name or a name in dashboard_host_names")
                message = HOST_NAME_REJECTED
            elif origin and unsafe and urlsplit(origin).netloc.lower() != host.lower():
                reason = f"cross-origin {scope['type']} request from {origin[:100]!r}"
                message = "Cross-origin request rejected"
            if reason:
                logger.warning(f"Rejected {reason}")
                if scope["type"] == "websocket":
                    await WebSocketClose(code=1008)(scope, receive, send)
                else:
                    await PlainTextResponse(message, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# No CORS middleware: the dashboard is always served by this same server (desktop window,
# browser, or a phone or tablet on the LAN), so cross-origin access is never needed. Leaving it off
# prevents unrelated web pages from reading screen OCR results; SameOriginGuard blocks
# them from changing state, and DNS-rebinding pages from reaching the server at all.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(SameOriginGuard)

from ffvii_companion.paths import PACKAGE_DIR

STATIC_DIR = PACKAGE_DIR / "server" / "static"


def dashboard_version(directory: Path) -> str:
    """
    Short hash of the dashboard files (names and contents). Sent to every WebSocket client on
    connect: a dashboard tab left open across an update reconnects to the new server with the
    old script still loaded, and reloads when the version differs from the one it first saw.
    """
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        data = path.read_bytes()
        digest.update(f"{path.relative_to(directory).as_posix()}\0{len(data)}\0".encode("utf-8"))
        digest.update(data)
    return digest.hexdigest()[:12]


DASHBOARD_VERSION = dashboard_version(STATIC_DIR)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._closing: Set[asyncio.Task] = set()  # close tasks of dropped clients (kept referenced)

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        self.loop = asyncio.get_running_loop()

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast_text(self, text: str):
        if not self.active_connections:
            return
        connections = list(self.active_connections)

        async def _send_safe(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=1.5)
                return True
            except Exception as e:
                logger.warning(f"Error broadcasting to WebSocket client: {e}")
                return False

        results = await asyncio.gather(*[_send_safe(ws) for ws in connections], return_exceptions=True)
        for ws, res in zip(connections, results):
            if res is not True and ws in self.active_connections:
                self.active_connections.discard(ws)
                # Close it too: a dropped socket left open stays connected (the browser answers
                # pings), so the dashboard would show LIVE SYNC without ever getting another
                # update. The close frame makes its onclose reconnect and resync.
                task = asyncio.create_task(self._close_dropped(ws))
                self._closing.add(task)
                task.add_done_callback(self._closing.discard)

    @staticmethod
    async def _close_dropped(ws: WebSocket):
        try:
            await asyncio.wait_for(ws.close(code=1011), timeout=1.5)
        except Exception:
            pass  # already gone; a client that never reads gets the frame once it reads again

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        await self.broadcast_text(json.dumps(message))

    def threadsafe_broadcast(self, message: dict):
        """Allows background capture thread to safely schedule a WebSocket broadcast."""
        if not self.active_connections:
            return
        if self.loop and self.loop.is_running():
            text = json.dumps(message)
            asyncio.run_coroutine_threadsafe(self.broadcast_text(text), self.loop)

manager = ConnectionManager()
battle_lock = threading.Lock()
current_battle = BattleState()


def battle_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Completes a battle state dict for the dashboard: every enemy shows the scene.bin variant of
    this battle (EnemyCompendium.resolve_variants, which copies the dicts, so compendium entries
    are never modified) with its steal odds at the profile's party level, and the encounter gets
    the battle summary plus the profile values the odds were computed with.
    """
    profile = player_profile.get()
    level, glove = profile["party_level"], profile["sneak_glove"]
    enemies = []
    for e in compendium.resolve_variants(data.get("enemies") or [], party_level=level):
        item_odds = compendium.party_steal_item_odds(e, level, glove)
        e["steals"] = [{**s, "odds": odds} for s, odds in zip(e.get("steals") or [], item_odds)]
        e["steal_odds"] = compendium.party_steal_odds(e, level, glove)
        enemies.append(e)
    out = dict(data)
    out["enemies"] = enemies
    out["summary"] = compendium.battle_summary(enemies) if data.get("in_battle") else None
    out["party"] = {"party_level": level, "sneak_glove": glove}
    return out


def battle_message(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "BATTLE_UPDATE", "data": battle_data(data)}


def current_battle_snapshot() -> Dict[str, Any]:
    with battle_lock:
        return current_battle.model_dump()


# Connect background capture service callbacks
def handle_live_battle_detected(enemies: list, battle_title: str):
    global current_battle
    now = time.time()
    with battle_lock:
        matched_enemy_objs = [Enemy(**e) for e in enemies]
        current_battle = BattleState(
            in_battle=True,
            battle_title=battle_title,
            enemies=matched_enemy_objs,
            timestamp=now,
            source="ocr"
        )
    # Direct dict serialization avoids Pydantic model_dump overhead on hot path
    with capture_service._state_lock:
        phase = capture_service.combat_phase
        fps = capture_service.current_fps
    manager.threadsafe_broadcast(battle_message({
        "in_battle": True,
        "battle_title": battle_title,
        "enemies": enemies,
        "timestamp": now,
        "source": "ocr",
        "combat_phase": phase,
        "current_fps": fps
    }))

def handle_live_battle_cleared():
    global current_battle
    now = time.time()
    with battle_lock:
        current_battle = BattleState(
            in_battle=False,
            battle_title="No Active Battle",
            enemies=[],
            timestamp=now,
            source="idle"
        )
    with capture_service._state_lock:
        phase = capture_service.combat_phase
        fps = capture_service.current_fps
    manager.threadsafe_broadcast(battle_message({
        "in_battle": False,
        "battle_title": "No Active Battle",
        "enemies": [],
        "timestamp": now,
        "source": "idle",
        "combat_phase": phase,
        "current_fps": fps
    }))

def capture_status_message() -> Dict[str, Any]:
    """
    What every dashboard's CAPTURE button shows: whether capture runs, and its sampling phase and
    rate. Sent on connect, when any dashboard starts or stops capture, and on a phase change.
    """
    with capture_service._state_lock:
        data = {"running": capture_service.is_running(), "combat_phase": capture_service.combat_phase,
                "current_fps": capture_service.current_fps}
    return {"type": "CAPTURE_STATUS", "data": data}

def handle_capture_phase_changed():
    """Pushes a sampling phase change that carries no encounter update (e.g. hunting -> settled)."""
    manager.threadsafe_broadcast(capture_status_message())

capture_service.on_battle_detected = handle_live_battle_detected
capture_service.on_battle_cleared = handle_live_battle_cleared
capture_service.on_phase_changed = handle_capture_phase_changed

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Immediately send the dashboard version (a tab with an older script reloads), the
        # current state, the player profile and the capture status upon connection
        await websocket.send_json({"type": "DASHBOARD_VERSION", "data": {"version": DASHBOARD_VERSION}})
        await websocket.send_json(battle_message(current_battle_snapshot()))
        await websocket.send_json({"type": "PROFILE_UPDATE", "data": player_profile.get()})
        await websocket.send_json(capture_status_message())
        while True:
            # Listen for dashboard commands (manual lookup, add a partner, clear the battle)
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    continue
                action = payload.get("action")
                if action == "MANUAL_SELECT":
                    enemy_id = payload.get("enemy_id")
                    enemy_data = compendium.get_by_id(enemy_id) if isinstance(enemy_id, str) else None
                    if enemy_data:
                        enemy_obj = Enemy(**enemy_data)
                        with battle_lock:
                            current_battle.in_battle = True
                            current_battle.battle_title = f"Manual Lookup: {enemy_obj.display_name or enemy_obj.name}"
                            current_battle.enemies = [enemy_obj]
                            current_battle.source = "manual"
                            snap = current_battle.model_dump()
                        await manager.broadcast(battle_message(snap))
                elif action == "ADD_ENEMY":
                    enemy_id = payload.get("enemy_id")
                    enemy_data = compendium.get_by_id(enemy_id) if isinstance(enemy_id, str) else None
                    if enemy_data:
                        enemy_obj = Enemy(**enemy_data)
                        with battle_lock:
                            if not any(e.id == enemy_obj.id for e in current_battle.enemies):
                                current_battle.enemies.append(enemy_obj)
                                current_battle.in_battle = True
                                current_battle.battle_title = (
                                    f"Battle: {', '.join(e.display_name or e.name for e in current_battle.enemies)}")
                            snap = current_battle.model_dump()
                        await manager.broadcast(battle_message(snap))
                elif action == "CLEAR_BATTLE":
                    # The capture loop forgets its collected enemies too, so the next name it reads
                    # starts a new encounter instead of bringing the cleared ones back. Its
                    # on_battle_cleared (handle_live_battle_cleared) clears current_battle and
                    # broadcasts it with the idle phase. In a worker thread: clear_battle waits
                    # for a sampling step in progress.
                    await asyncio.to_thread(capture_service.clear_battle)
            except (ValueError, TypeError) as ex:
                logger.warning(f"Ignoring malformed ws message: {ex}")
    except WebSocketDisconnect:
        pass
    except Exception as ex:
        logger.warning(f"WebSocket connection closed with error: {ex}")
    finally:
        manager.disconnect(websocket)

@app.get("/api/health")
def health():
    """Lightweight liveness probe used by the desktop launcher."""
    return {"ok": True}

@app.get("/api/enemies/{enemy_id}/partners", response_model=List[Enemy])
def get_enemy_partners(enemy_id: str):
    """Enemies that share a formation with this one; while it is in the current battle, only those
    of the formations matching the battle (get_partners ignores a battle without the enemy)."""
    observed = {}
    with battle_lock:
        live = current_battle.source == "ocr"
        if current_battle.in_battle and current_battle.enemies:
            for e in current_battle.enemies:
                observed[e.name] = getattr(e, "count", 1) or 1
    # The capture loop's counts describe its own battle, not a simulated or manual one
    if live:
        with capture_service._state_lock:
            for k, v in capture_service.observed_counts.items():
                observed[k] = max(v, observed.get(k, 1))

    partners = compendium.get_partners(enemy_id, current_observed=observed if observed else None)
    return [Enemy(**p) for p in partners]

@app.get("/api/enemies/{enemy_id}/ai", response_model=AiInfo, response_model_exclude_none=True)
def get_enemy_ai(enemy_id: str):
    """The card's AI box: the enemy's hand-written AI summary and, per scene.bin record, its attacks
    and decompiled AI scripts (EnemyCompendium.get_ai). 404 when the enemy has no AI scripts."""
    info = compendium.get_ai(enemy_id)
    if info is None:
        raise HTTPException(status_code=404, detail="No AI scripts for this enemy")
    return info

# Both spellings are searchable: the compendium name and the game's ("Jenovalife", "Jenova·LIFE")
_cached_searchable_enemies = [({e["name"].lower(), e.get("display_name", e["name"]).lower()}, e["id"].lower(), e)
                              for e in compendium.get_all()]
_search_cache: dict = {}

@app.get("/api/search")
def search_enemies(q: str = Query(..., min_length=1, max_length=64)):
    query = q.strip().lower()
    if query in _search_cache:
        return _search_cache[query]

    exact_matches = []
    prefix_matches = []
    substr_matches = []
    seen_ids = set()

    for names, eid, enemy in _cached_searchable_enemies:
        if query in names or eid == query:
            exact_matches.append(enemy)
            seen_ids.add(enemy["id"])
        elif any(n.startswith(query) for n in names) or eid.startswith(query):
            if enemy["id"] not in seen_ids:
                prefix_matches.append(enemy)
                seen_ids.add(enemy["id"])
        elif any(query in n for n in names) or query in eid:
            if enemy["id"] not in seen_ids:
                substr_matches.append(enemy)
                seen_ids.add(enemy["id"])

    ranked_results = exact_matches + prefix_matches + substr_matches

    # If few results, test fuzzy match
    if len(ranked_results) < 3:
        fuzzy_match = compendium.match_enemy(q)
        if fuzzy_match and fuzzy_match["id"] not in seen_ids:
            ranked_results.append(fuzzy_match)

    final_results = ranked_results[:15]
    if len(_search_cache) >= 256:
        # Bounded cache eviction
        for _ in range(64):
            try:
                _search_cache.pop(next(iter(_search_cache)))
            except (StopIteration, KeyError):
                break
    _search_cache[query] = final_results
    return final_results

@app.post("/api/steal-calc", response_model=StealCalcResponse)
def calculate_steal(req: StealCalcRequest):
    result = compendium.calculate_steal_rate(
        stealer_level=req.stealer_level,
        target_level=req.target_level,
        base_chance=req.base_chance,
        sneak_glove=req.sneak_glove
    )
    slots = list(req.earlier_base_chances) + [req.base_chance]
    item = compendium.steal_item_chance(req.stealer_level, req.target_level, slots, len(slots) - 1, req.sneak_glove)
    return StealCalcResponse(**result, item_success_percentage=item)

# --- Player profile (party level, Sneak Glove, learned Enemy Skills) ---

@app.get("/api/profile")
def get_profile():
    return player_profile.get()

@app.post("/api/profile")
async def update_profile(changes: Dict[str, Any]):
    """Partial update. Every dashboard receives the new profile and the battle with re-computed steal odds."""
    try:
        profile = player_profile.update(changes)
    except ProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"The profile could not be saved: {exc}")
    await manager.broadcast({"type": "PROFILE_UPDATE", "data": profile})
    await manager.broadcast(battle_message(current_battle_snapshot()))
    return profile

_enemy_skill_sources = compendium.enemy_skill_sources()

@app.get("/api/enemy-skills")
def get_enemy_skills():
    """The 24 Enemy Skills in materia order with the enemies that have them in their attack list."""
    return _enemy_skill_sources

# --- Misdetection reports ---

@app.post("/api/report/misdetection")
def report_misdetection(req: MisdetectionReportRequest):
    """Saves the capture-region frames with OCR results (engine/detection_report.py) for a regression test."""
    try:
        result = capture_service.save_detection_report(req.expected, req.note)
    except ReportError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # The folder name only: LAN clients do not need the PC's directory layout
    return {"status": "saved", "folder": f"reports/{result['folder']}", "files": result["files"],
            "expected": result["expected"], "unresolved": result["unresolved"],
            "needs_review": result["needs_review"]}

@app.post("/api/simulate/encounter")
async def simulate_encounter(req: DetectSimulateRequest):
    global current_battle
    # A repeated name is one enemy with a count, as the capture loop reports it (the dashboard
    # keys its cards by id and shows the count as a badge)
    matched_enemies: List[Enemy] = []
    by_id: Dict[str, Enemy] = {}
    for name in req.enemy_names:
        found = compendium.match_enemy(name)
        if not found:
            logger.warning(f"Simulation name not recognized: {name}")
        elif found["id"] in by_id:
            by_id[found["id"]].count += 1
        else:
            by_id[found["id"]] = Enemy(**found)
            matched_enemies.append(by_id[found["id"]])

    with battle_lock:
        current_battle = BattleState(
            in_battle=len(matched_enemies) > 0,
            battle_title=req.battle_title or "Simulated Battle",
            enemies=matched_enemies,
            source="simulation"
        )
        snap = current_battle.model_dump()
    message = battle_message(snap)
    await manager.broadcast(message)
    return {"status": "ok", "battle": message["data"]}

@app.post("/api/simulate/clear")
async def simulate_clear():
    global current_battle
    with battle_lock:
        current_battle = BattleState(
            in_battle=False,
            battle_title="No Active Battle",
            enemies=[],
            source="idle"
        )
        snap = current_battle.model_dump()
    message = battle_message(snap)
    await manager.broadcast(message)
    return {"status": "ok", "battle": message["data"]}

@app.get("/api/simulate/presets")
def get_presets():
    from ffvii_companion.server.simulator import PRESET_BATTLES
    return PRESET_BATTLES

# --- Live screen capture and calibration endpoints ---

@app.get("/api/capture/status")
def get_capture_status():
    status = capture_service.get_status()
    cfg = calibration_manager.get_config()
    return {
        "status": status,
        "config": cfg
    }

def broadcast_capture_status() -> Dict[str, Any]:
    """Pushes the capture status to every dashboard (start/stop come from any of them) and returns it."""
    message = capture_status_message()
    manager.threadsafe_broadcast(message)
    return message["data"]

@app.post("/api/capture/start")
def start_capture():
    if not capture_service.is_running():
        capture_service.start()
    return {"status": "started", **broadcast_capture_status()}

@app.post("/api/capture/stop")
def stop_capture():
    if capture_service.is_running():
        capture_service.stop()
    return {"status": "stopped", **broadcast_capture_status()}

@app.get("/api/calibration/config")
def get_calibration():
    return {
        "config": calibration_manager.get_config(),
        "presets": PRESETS
    }

@app.post("/api/calibration/config")
def update_calibration(settings: Dict[str, Any]):
    try:
        updated = calibration_manager.update_config(settings)
    except ConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"The settings could not be saved: {exc}")
    return {"status": "updated", "config": updated}

@app.post("/api/calibration/preset/{preset_key}")
def apply_preset_endpoint(preset_key: str):
    try:
        res = calibration_manager.apply_preset(preset_key)
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"The settings could not be saved: {exc}")
    if not res:
        raise HTTPException(status_code=400, detail="Invalid preset key")
    return {"status": "preset_applied", "config": res}

# One finder for the calibration dialog's rescans: a new finder per request opened another
# desktop handle (never closed) each time. Separate from the capture loop's finder, whose
# cached window and scan throttling belong to the capture thread.
_calibration_window_finder = FFVIIWindowFinder()
_calibration_window_lock = threading.Lock()

@app.get("/api/calibration/window")
def detect_game_window():
    with _calibration_window_lock:
        win = _calibration_window_finder.find_ffvii_window(force_scan=True)
    return {
        "found": win is not None,
        "window": win
    }

# POST, not GET: a snapshot runs a full uncached OCR pass, and SameOriginGuard only inspects
# unsafe methods, so a GET could be fired at the companion by any web page
@app.post("/api/calibration/snapshot")
def capture_diagnostic_snapshot():
    try:
        snap = capture_service.capture_snapshot()
        return {
            "status": "success",
            "bbox": snap["bbox"],
            "dimensions": snap["dimensions"],
            "recognized_lines": snap["recognized_lines"],
            "matched_enemies": [e["name"] for e in snap["matched_enemies"]],
            "game_window": snap["game_window"],
            "performance": snap.get("performance", {})
        }
    except Exception as e:
        logger.error(f"Error capturing diagnostic snapshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/performance")
def get_performance_metrics():
    return {
        "status": "ok",
        "performance": perf_tracker.get_summary()
    }

@app.get("/api/performance/logs")
def get_performance_logs(lines: int = Query(50, ge=1, le=500)):
    return {
        "status": "ok",
        "logs": perf_tracker.get_recent_logs(max_lines=lines)
    }

# Native desktop integration endpoints
_desktop_window_getter = None

def register_desktop_window_getter(getter):
    global _desktop_window_getter
    _desktop_window_getter = getter

@app.get("/api/desktop/status")
def get_desktop_status():
    if _desktop_window_getter:
        try:
            win = _desktop_window_getter()
            if win:
                return {"is_desktop": True, "on_top": bool(win.on_top)}
        except Exception:
            pass
    return {"is_desktop": False, "on_top": False}

@app.post("/api/desktop/toggle-pin")
def toggle_desktop_pin():
    if _desktop_window_getter:
        try:
            win = _desktop_window_getter()
            if win:
                win.on_top = not win.on_top
                return {"is_desktop": True, "on_top": bool(win.on_top)}
        except Exception:
            pass
    return {"is_desktop": False, "on_top": False}

class DashboardFiles(StaticFiles):
    """
    The dashboard's files, sent with Cache-Control: no-cache so browsers revalidate them on every
    load (an unchanged file costs a 304 through its ETag). Without it they cache heuristically and
    can keep running an app.js from before an update against the newer server.
    """
    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Serve static dashboard UI
dashboard_files = DashboardFiles(directory=str(STATIC_DIR))
app.mount("/static", dashboard_files, name="static")

@app.get("/")
async def serve_index(request: Request):
    # Through the same files app as /static: same cache header, 304 when unchanged
    return await dashboard_files.get_response("index.html", request.scope)
