"""
Preset encounters (PRESET_BATTLES, also served at /api/simulate/presets) and a command-line client
that drives a running companion through /api/simulate/*, for trying the dashboard without the game:

    python -m ffvii_companion.server.simulator          # one preset battle
    python -m ffvii_companion.server.simulator tour     # cycle through the presets
    python -m ffvii_companion.server.simulator clear    # clear the battle

Uses only the standard library (urllib).
"""
import json
import time
import urllib.request
from typing import List, Dict, Any, Optional

DEFAULT_SERVER_URL = "http://127.0.0.1:59314"


def _post_json(url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
    """POSTs JSON with the standard library (no third-party HTTP client needed)."""
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

PRESET_BATTLES = [
    {
        "title": "Sector 1 Reactor Core",
        "description": "First Boss Encounter",
        "enemies": ["Guard Scorpion"]
    },
    {
        "title": "Shinra HQ - 67F / 68F Patrol",
        "description": "High Value Steals: Hardedge & Carbon Bangle",
        "enemies": ["SOLDIER:3rd", "Moth Slasher"]
    },
    {
        "title": "Train Graveyard - Rare Encounter",
        "description": "Critical Aeris Weapon Steal & Ghost Hand",
        "enemies": ["Eligor", "Ghost"]
    },
    {
        "title": "Shinra HQ - 68F Laboratory",
        "description": "Boss Encounter with Minions",
        "enemies": ["Sample:H0512", "Sample:H0512-opt", "Sample:H0512-opt"]
    },
    {
        "title": "Midgar Wasteland",
        "description": "Barret Weapon Steal & Matra Magic Enemy Skill",
        "enemies": ["Custom Sweeper", "Kalm Fang"]
    },
    {
        "title": "Chocobo Marsh",
        "description": "Iconic Swamp Serpent - Beta Enemy Skill",
        "enemies": ["Midgar Zolom"]
    },
    {
        "title": "Cave of the Gi",
        "description": "Undead Boss - Phoenix Down / X-Potion One-Hit Kill",
        "enemies": ["Gi Nattak"]
    },
    {
        "title": "Shinra HQ - Roof Showdown",
        "description": "Cloud 1v1 Boss & Barrier Support Pet",
        "enemies": ["Rufus", "Dark Nation"]
    }
]

class BattleSimulator:
    def __init__(self, server_url: str = DEFAULT_SERVER_URL):
        self.server_url = server_url.rstrip("/")

    def trigger_preset(self, index: int) -> Dict[str, Any]:
        if 0 <= index < len(PRESET_BATTLES):
            preset = PRESET_BATTLES[index]
            return self.trigger_battle(preset["enemies"], preset["title"])
        raise ValueError(f"Invalid preset index: {index}")

    def trigger_battle(self, enemy_names: List[str], battle_title: Optional[str] = "Simulated Encounter") -> Dict[str, Any]:
        url = f"{self.server_url}/api/simulate/encounter"
        payload = {
            "enemy_names": enemy_names,
            "battle_title": battle_title
        }
        return _post_json(url, payload)

    def clear_battle(self) -> Dict[str, Any]:
        url = f"{self.server_url}/api/simulate/clear"
        return _post_json(url)

    def run_auto_tour(self, interval_seconds: int = 5):
        print(f"Starting automatic battle simulation tour ({len(PRESET_BATTLES)} encounters)...")
        for i, preset in enumerate(PRESET_BATTLES):
            print(f"Triggering [{i+1}/{len(PRESET_BATTLES)}]: {preset['title']} ({', '.join(preset['enemies'])})")
            self.trigger_preset(i)
            time.sleep(interval_seconds)
        print("Simulation tour complete. Clearing battle.")
        self.clear_battle()

if __name__ == "__main__":
    import sys
    sim = BattleSimulator()
    if len(sys.argv) > 1 and sys.argv[1] == "tour":
        sim.run_auto_tour(4)
    elif len(sys.argv) > 1 and sys.argv[1] == "clear":
        sim.clear_battle()
        print("Battle cleared.")
    else:
        # Trigger preset 1 (SOLDIER:3rd + Moth Slasher)
        res = sim.trigger_preset(1)
        print("Triggered simulation:", res)
