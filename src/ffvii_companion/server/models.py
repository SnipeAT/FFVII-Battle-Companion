from typing import Annotated, Any, Dict, List, Optional
from pydantic import BaseModel, Field
import time

class StealItem(BaseModel):
    item: str
    rate: str
    is_key_steal: bool = False
    base_chance: int = 32  # Raw FFVII steal value of the item slot (e.g. 8 = rare, 32 = common, 63 = very common)
    note: Optional[str] = None  # Why a key steal is worth it
    odds: Optional[float] = None  # Chance (%) a Steal gets this item at the profile's party level (set when broadcast)

class DropItem(BaseModel):
    item: str
    rate: str

class EnemyStats(BaseModel):
    strength: int = 0
    defense: int = 0
    magic: int = 0
    magic_defense: int = 0
    dexterity: int = 0
    luck: int = 0
    evade: int = 0

class EnemySkill(BaseModel):
    name: str
    manipulate: bool = False  # Also in the enemy's Manipulate list

class EnemyVariant(BaseModel):
    """Which scene.bin record of the enemy a battle shows (compendium.EnemyCompendium.resolve_variants)."""
    index: int
    count: int
    enemy_ids: List[int] = Field(default_factory=list)
    scenes: List[int] = Field(default_factory=list)
    resolved: bool = True
    candidates: List[Dict[str, Any]] = Field(default_factory=list)

class Enemy(BaseModel):
    id: str
    name: str  # Matching key, also used by formations.json
    display_name: Optional[str] = None  # As the game writes it ("Jenova·LIFE")
    level: int
    hp: int
    mp: int
    exp: int = 0
    ap: int = 0
    gil: int = 0
    stats: Optional[EnemyStats] = None
    manipulate: List[str] = Field(default_factory=list)
    enemy_skills: List[EnemySkill] = Field(default_factory=list)
    zones: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    resistances: List[str] = Field(default_factory=list)
    absorbs: List[str] = Field(default_factory=list)
    elemental_immunities: List[str] = Field(default_factory=list)
    full_cure: List[str] = Field(default_factory=list)  # Elements that restore all of its HP
    healing_damages: bool = False  # Restorative absorbed: healing magic and items damage it
    status_immunities: List[str] = Field(default_factory=list)
    steals: List[StealItem] = Field(default_factory=list)  # Steal slots in the order the game tries them
    drops: List[DropItem] = Field(default_factory=list)
    morph: Optional[str] = None
    notes: Optional[str] = None
    count: int = 1
    instance_letter: Optional[str] = None
    steal_odds: Optional[float] = None  # Chance (%) a Steal gets any item at the party level (set when broadcast)
    variants: Optional[List[Dict[str, Any]]] = None  # Distinct scene.bin records (compendium entries only)
    variant: Optional[EnemyVariant] = None  # The record a battle shows (set when broadcast)

class BattleState(BaseModel):
    in_battle: bool = False
    battle_title: Optional[str] = None
    enemies: List[Enemy] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)
    source: str = "idle" # "ocr", "manual", "simulation"
    # Added when the state is sent (server.main.battle_data): EnemyCompendium.battle_summary()
    # and the profile values the steal odds were computed with
    summary: Optional[Dict[str, Any]] = None
    party: Optional[Dict[str, Any]] = None

class StealCalcRequest(BaseModel):
    stealer_level: int = Field(15, ge=1, le=99)
    target_level: int = Field(14, ge=1, le=99)
    base_chance: int = Field(8, ge=0, le=255)
    sneak_glove: bool = False
    # Steal values of the enemy's earlier steal slots, which the game tries first
    earlier_base_chances: List[Annotated[int, Field(ge=0, le=255)]] = Field(default_factory=list, max_length=3)

class StealCalcResponse(BaseModel):
    stealer_level: int
    target_level: int
    sneak_glove: bool
    base_chance_val: int
    level_difference: int
    level_factor: int
    chance_value: int
    total_success_percentage: float
    formula_note: str
    item_success_percentage: float  # Chance this slot's item is stolen once the earlier slots failed

class MisdetectionReportRequest(BaseModel):
    expected: Optional[str] = Field(None, max_length=500)  # What is really on screen, e.g. "Devil Ride x2"
    note: Optional[str] = Field(None, max_length=1000)

class DetectSimulateRequest(BaseModel):
    enemy_names: List[str] = Field(..., max_length=16)
    battle_title: Optional[str] = Field("Simulated Encounter", max_length=200)
