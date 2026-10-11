// FFVII Tactical Companion Client Script

let ws = null;
let currentEnemyForSteal = null;
let currentStealSlot = 0;
const currentEnemiesMap = new Map();
let simulatorPresets = [];

// DOM Elements
const wsStatusDot = document.getElementById("wsStatusDot");
const wsStatusText = document.getElementById("wsStatusText");
const battleTitleBanner = document.getElementById("battleTitleBanner");
const idleState = document.getElementById("idleState");
const enemiesGrid = document.getElementById("enemiesGrid");
const activeEnemiesCount = document.getElementById("activeEnemiesCount");
const lastUpdatedTime = document.getElementById("lastUpdatedTime");

const enemySearchInput = document.getElementById("enemySearchInput");
const searchResultsDropdown = document.getElementById("searchResultsDropdown");
const clearBattleBtn = document.getElementById("clearBattleBtn");
const toggleSimBtn = document.getElementById("toggleSimBtn");
const closeSimBtn = document.getElementById("closeSimBtn");
const simDrawer = document.getElementById("simDrawer");
const presetButtons = document.getElementById("presetButtons");

// Formation Partners Bar
const partnersBar = document.getElementById("partnersBar");
const partnersChips = document.getElementById("partnersChips");
const autoAddPartnersCheckbox = document.getElementById("autoAddPartnersCheckbox");

// Capture & Calibration Elements
const toggleCaptureBtn = document.getElementById("toggleCaptureBtn");
const captureDot = document.getElementById("captureDot");
const captureBtnText = document.getElementById("captureBtnText");
const openCalibBtn = document.getElementById("openCalibBtn");
const calibModal = document.getElementById("calibModal");
const closeCalibModalBtn = document.getElementById("closeCalibModalBtn");
const saveCalibBtn = document.getElementById("saveCalibBtn");
const rescanWindowBtn = document.getElementById("rescanWindowBtn");
const windowStatusText = document.getElementById("windowStatusText");
const presetSelectGrid = document.getElementById("presetSelectGrid");
const testSnapshotBtn = document.getElementById("testSnapshotBtn");
const diagnosticOutput = document.getElementById("diagnosticOutput");
const diagDim = document.getElementById("diagDim");
const diagPerf = document.getElementById("diagPerf");
const diagLines = document.getElementById("diagLines");
const diagEnemies = document.getElementById("diagEnemies");

// Performance Profiler Elements
const refreshPerfBtn = document.getElementById("refreshPerfBtn");
const perfStatGrab = document.getElementById("perfStatGrab");
const perfStatOcr = document.getElementById("perfStatOcr");
const perfStatMatch = document.getElementById("perfStatMatch");
const perfStatTotal = document.getElementById("perfStatTotal");
const perfLifetimeStats = document.getElementById("perfLifetimeStats");
const togglePerfLogsBtn = document.getElementById("togglePerfLogsBtn");
const perfLogsContainer = document.getElementById("perfLogsContainer");

let isCaptureActive = false;

// Steal Modal Elements
const stealModal = document.getElementById("stealModal");
const closeStealModalBtn = document.getElementById("closeStealModalBtn");
const stealModalTitle = document.getElementById("stealModalTitle");
const stealerLevelInput = document.getElementById("stealerLevelInput");
const targetLevelInput = document.getElementById("targetLevelInput");
const sneakGloveCheckbox = document.getElementById("sneakGloveCheckbox");
const recalcBtn = document.getElementById("recalcBtn");
const calcResultPercent = document.getElementById("calcResultPercent");
const calcResultFormula = document.getElementById("calcResultFormula");

// Battle summary, Enemy Skill checklist and misdetection report
const battleSummary = document.getElementById("battleSummary");
const openSkillsBtn = document.getElementById("openSkillsBtn");
const skillsCountText = document.getElementById("skillsCountText");
const skillsModal = document.getElementById("skillsModal");
const skillsModalCount = document.getElementById("skillsModalCount");
const skillsList = document.getElementById("skillsList");
const closeSkillsModalBtn = document.getElementById("closeSkillsModalBtn");
const openReportBtn = document.getElementById("openReportBtn");
const reportModal = document.getElementById("reportModal");
const closeReportModalBtn = document.getElementById("closeReportModalBtn");
const reportShownText = document.getElementById("reportShownText");
const reportExpectedInput = document.getElementById("reportExpectedInput");
const reportNoteInput = document.getElementById("reportNoteInput");
const reportResult = document.getElementById("reportResult");
const saveReportBtn = document.getElementById("saveReportBtn");

// Player profile (party level, Sneak Glove, learned Enemy Skills), kept on the server and
// pushed to every dashboard as PROFILE_UPDATE
let playerProfile = { party_level: 15, sneak_glove: false, learned_enemy_skills: [] };
let lastBattleSummary = null;
const ENEMY_SKILL_TOTAL = 24;

let reconnectTimeout = null;
let reconnectAttempts = 0;

// Version of the dashboard files the server serves (server: dashboard_version), as first seen
// on connect. A tab left open across a companion update reconnects to the new server with this
// old script still loaded: it reloads so the new files run.
let dashboardVersion = null;

// WebSocket Setup with Exponential Backoff
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  updateWsStatus("CONNECTING...", "connecting");
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    updateWsStatus("LIVE SYNC", "connected");
    reconnectAttempts = 0;
  };

  ws.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === "DASHBOARD_VERSION") {
        if (dashboardVersion === null) {
          dashboardVersion = message.data.version;
        } else if (message.data.version !== dashboardVersion) {
          window.location.reload();
        }
      } else if (message.type === "BATTLE_UPDATE") {
        renderBattleState(message.data);
        applyCapturePhase(message.data.combat_phase, message.data.current_fps);
      } else if (message.type === "CAPTURE_STATUS") {
        // Sent on connect, when any dashboard starts or stops capture, and when the sampling
        // phase changes without an encounter update (e.g. hunting -> settled)
        isCaptureActive = message.data.running;
        applyCapturePhase(message.data.combat_phase, message.data.current_fps);
      } else if (message.type === "PROFILE_UPDATE") {
        applyProfile(message.data);
      }
    } catch (err) {
      console.error("Error parsing WS payload:", err);
    }
  };

  ws.onclose = () => {
    updateWsStatus("DISCONNECTED (RETRYING...)", "disconnected");
    if (!reconnectTimeout) {
      reconnectAttempts++;
      const baseDelay = Math.min(5000, 1000 + reconnectAttempts * 1000);
      const jitter = Math.floor(Math.random() * 500);
      const delay = baseDelay + jitter;
      reconnectTimeout = setTimeout(() => {
        reconnectTimeout = null;
        initWebSocket();
      }, delay);
    }
  };

  ws.onerror = (err) => {
    console.error("WS error:", err);
  };
}

function updateWsStatus(text, statusClass) {
  wsStatusText.textContent = text;
  wsStatusDot.className = `status-dot ${statusClass}`;
}

let lastRenderedStateKey = "";
let lastTimeUpdatedMs = 0;
const renderedCardElements = new Map(); // enemyId -> { el: HTMLElement, count: number }
let isPageVisible = !document.hidden;

document.addEventListener("visibilitychange", () => {
  isPageVisible = !document.hidden;
  if (isPageVisible) {
    checkCaptureStatus();
  }
});

// Render Active Battle State with Modern View Transitions API and Keyed DOM Diffing
function renderBattleState(state) {
  // The party values are part of the key: a profile change re-sends the battle with new steal odds
  const party = state.party ? `${state.party.party_level}:${state.party.sneak_glove}` : "";
  const currentStateKey = state.in_battle && state.enemies && state.enemies.length > 0
    ? `${state.battle_title}|${state.enemies.map(e => `${e.id}:${e.count || 1}:${variantKey(e)}`).join(",")}|${party}`
    : "idle";

  if (currentStateKey === lastRenderedStateKey) {
    const nowMs = Date.now();
    if (nowMs - lastTimeUpdatedMs > 2000) {
      lastTimeUpdatedMs = nowMs;
      if (lastUpdatedTime) lastUpdatedTime.textContent = `LAST SYNC: ${new Date(nowMs).toLocaleTimeString()}`;
    }
    return;
  }
  lastRenderedStateKey = currentStateKey;

  const updateDOM = () => {
    const nowMs = Date.now();
    lastTimeUpdatedMs = nowMs;
    if (lastUpdatedTime) lastUpdatedTime.textContent = `LAST SYNC: ${new Date(nowMs).toLocaleTimeString()}`;

    if (!state.in_battle || !state.enemies || state.enemies.length === 0) {
      battleTitleBanner.textContent = "STATUS: AWAITING ENCOUNTER";
      idleState.classList.remove("hidden");
      enemiesGrid.classList.add("hidden");
      currentEnemiesMap.clear();
      renderedCardElements.clear();
      enemiesGrid.replaceChildren();
      activeEnemiesCount.textContent = "ENEMIES: 0";
      if (partnersBar) partnersBar.classList.add("hidden");
      // The server narrows partners with the capture loop's collected counts, which the cache key
      // does not hold: a cleared or finished battle starts with fresh answers
      partnersCache.clear();
      if (partnersAbortController) {
        partnersAbortController.abort();
        partnersAbortController = null;
      }
      renderBattleSummary(null);
      return;
    }

    // Active battle present
    battleTitleBanner.textContent = `COMBAT ACTIVE: ${(state.battle_title || "UNKNOWN").toUpperCase()} [${state.source.toUpperCase()}]`;
    idleState.classList.add("hidden");
    enemiesGrid.classList.remove("hidden");
    activeEnemiesCount.textContent = `ENEMIES: ${state.enemies.length}`;

    currentEnemiesMap.clear();
    const activeIds = new Set();
    const newCardsFragment = document.createDocumentFragment();

    state.enemies.forEach((enemy) => {
      currentEnemiesMap.set(enemy.id, enemy);
      activeIds.add(enemy.id);

      const existing = renderedCardElements.get(enemy.id);
      if (existing && existing.variant !== variantKey(enemy)) {
        // Another scene.bin variant or fewer candidates (the formation was narrowed down): rebuild the card in place
        const card = createEnemyCard(enemy, state.party);
        existing.el.replaceWith(card);
        renderedCardElements.set(enemy.id, { el: card, count: enemy.count, variant: variantKey(enemy) });
      } else if (existing) {
        if (existing.count !== enemy.count) {
          existing.count = enemy.count;
          const countBadge = existing.el.querySelector(".enemy-count-badge");
          const titleH2 = existing.el.querySelector(".card-title-group h2");
          if (enemy.count && enemy.count > 1) {
            if (countBadge) {
              countBadge.textContent = `x${enemy.count}`;
            } else if (titleH2) {
              const badge = document.createElement("span");
              badge.className = "enemy-count-badge";
              badge.textContent = `x${enemy.count}`;
              titleH2.appendChild(document.createTextNode(" "));
              titleH2.appendChild(badge);
            }
          } else if (countBadge) {
            countBadge.remove();
          }
        }
        updateStealOdds(existing.el, enemy, state.party);
      } else {
        const card = createEnemyCard(enemy, state.party);
        renderedCardElements.set(enemy.id, { el: card, count: enemy.count, variant: variantKey(enemy) });
        newCardsFragment.appendChild(card);
      }
    });

    // Remove departed enemies from DOM
    for (const [id, entry] of renderedCardElements.entries()) {
      if (!activeIds.has(id)) {
        entry.el.remove();
        renderedCardElements.delete(id);
      }
    }

    // Append newly added enemy cards
    if (newCardsFragment.childNodes.length > 0) {
      enemiesGrid.appendChild(newCardsFragment);
    }

    renderBattleSummary(state.summary || null);

    // Query formation partners for the primary enemy
    updatePartnersBar(state.enemies);
  };

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (document.startViewTransition && !prefersReducedMotion && isPageVisible) {
    document.startViewTransition(updateDOM);
  } else {
    updateDOM();
  }
}

// Formation partners bar with in-memory caching and AbortController
const partnersCache = new Map();
let partnersAbortController = null;

if (partnersChips) {
  partnersChips.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-partner-chip");
    if (!btn) return;
    const enemyId = btn.getAttribute("data-id");
    if (enemyId && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "ADD_ENEMY", enemy_id: enemyId }));
    }
  });
}

function renderPartnersChips(partners, currentEnemies) {
  const existingIds = new Set(currentEnemies.map(e => e.id));
  const unadded = partners.filter(p => !existingIds.has(p.id));
  if (unadded.length === 0) {
    partnersBar.classList.add("hidden");
    partnersChips.replaceChildren();
    return;
  }

  partnersBar.classList.remove("hidden");
  partnersChips.innerHTML = unadded.map(p => `
    <button class="btn-partner-chip" data-id="${p.id}" title="Click to show ${escapeHtml(enemyLabel(p))} in this battle">
      + ${escapeHtml(enemyLabel(p))} (LVL ${p.level})
    </button>
  `).join("");
}

function updatePartnersBar(currentEnemies) {
  if (!partnersBar || !partnersChips || !currentEnemies || currentEnemies.length === 0) {
    if (partnersBar) partnersBar.classList.add("hidden");
    return;
  }

  const primaryEnemy = currentEnemies[0];
  const cacheKey = currentEnemies.map(e => `${e.id}:${e.count || 1}`).sort().join("|");

  if (partnersCache.has(cacheKey)) {
    renderPartnersChips(partnersCache.get(cacheKey), currentEnemies);
    return;
  }

  if (partnersAbortController) {
    partnersAbortController.abort();
    partnersAbortController = null;
  }
  partnersAbortController = new AbortController();

  fetch(`/api/enemies/${primaryEnemy.id}/partners`, { signal: partnersAbortController.signal })
    .then(res => res.json())
    .then(partners => {
      partnersCache.set(cacheKey, partners);
      renderPartnersChips(partners, currentEnemies);
    })
    .catch(err => {
      if (err.name !== "AbortError") {
        console.error("Error fetching partners:", err);
        partnersBar.classList.add("hidden");
      }
    });
}

// Name as the game writes it ("Jenova·LIFE"); name is the matching key
function enemyLabel(enemy) {
  return enemy.display_name || enemy.name;
}

// The scene.bin variant a card shows and the candidates it lists (server:
// EnemyCompendium.resolve_variants); a partner can narrow the candidates without changing the variant shown
function variantKey(enemy) {
  const v = enemy.variant;
  if (!v) return "";
  return `${v.index}/${v.resolved ? 1 : 0}/${(v.candidates || []).map(c => c.index).join(".")}`;
}

// Steal odds of one slot at the profile's party level (computed by the server, see
// server.main.battle_data); the game tries the slots in order, so a later slot needs the earlier ones to fail
function stealOddsText(enemy, slot, party) {
  const steal = (enemy.steals || [])[slot];
  if (!steal || steal.odds === null || steal.odds === undefined || !party) {
    return `Odds scale with your level vs. LVL ${enemy.level}`;
  }
  const glove = party.sneak_glove ? " + Sneak Glove" : "";
  const order = slot > 0 ? ", if the earlier slots fail" : "";
  return `Your odds: ${Number(steal.odds).toFixed(1)}% (party LVL ${party.party_level}${glove}${order})`;
}

function updateStealOdds(cardEl, enemy, party) {
  cardEl.querySelectorAll(".steal-party-odds[data-slot]").forEach(oddsEl => {
    oddsEl.textContent = stealOddsText(enemy, Number(oddsEl.getAttribute("data-slot")), party);
  });
}

function variantHtml(enemy) {
  const v = enemy.variant;
  if (!v || v.count < 2) return "";
  const scenes = (list) => `scene ${list.join(", ")}`;
  if (v.resolved) {
    return `<div class="card-variant">Variant ${v.index + 1} of ${v.count} (${escapeHtml(scenes(v.scenes))})</div>`;
  }
  const rows = v.candidates.map(c =>
    `<li${c.index === v.index ? ' class="shown"' : ""}>LVL ${c.level} &middot; ${c.hp.toLocaleString()} HP &middot; ` +
    `EXP ${c.exp.toLocaleString()} &middot; AP ${c.ap.toLocaleString()} &middot; GIL ${c.gil.toLocaleString()} ` +
    `<span class="summary-dim">(${escapeHtml(scenes(c.scenes))})</span></li>`).join("");
  return `<div class="card-variant card-variant-open">${v.candidates.length} variants possible in this battle; ` +
    `showing the one closest to your party level<ul class="variant-list">${rows}</ul></div>`;
}

function isSkillLearned(name) {
  return playerProfile.learned_enemy_skills.includes(name);
}

function enemySkillBadgesHtml(skills) {
  return skills.map(s => {
    const learned = isSkillLearned(s.name);
    return `<span class="badge badge-eskill ${learned ? "learned" : ""}" data-skill="${escapeHtml(s.name)}">` +
      `${escapeHtml(s.name)}${s.manipulate ? " (M)" : ""}` +
      `<span class="eskill-state">${learned ? "LEARNED" : "NEW"}</span></span>`;
  }).join("");
}

function createEnemyCard(enemy, party) {
  const card = document.createElement("div");
  card.className = "enemy-card";
  card.setAttribute("data-enemy-id", enemy.id);
  card.style.viewTransitionName = `card-${enemy.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;

  // Zones display
  const zonesText = (enemy.zones && enemy.zones.length > 0) ? enemy.zones.join(", ") : "Unknown Location";

  // Weaknesses badges
  let weaknessesHtml = "";
  if (enemy.weaknesses && enemy.weaknesses.length > 0) {
    weaknessesHtml = enemy.weaknesses.map(w => `<span class="badge badge-weakness">[WEAK] ${escapeHtml(w)}</span>`).join("");
  } else {
    weaknessesHtml = `<span class="empty-notice">No elemental weaknesses</span>`;
  }

  // Resistances & Halved
  let resistsHtml = "";
  if (enemy.resistances && enemy.resistances.length > 0) {
    resistsHtml = enemy.resistances.map(r => `<span class="badge badge-resist">[RESIST] ${escapeHtml(r)}</span>`).join("");
  }

  // Absorbs
  let absorbsHtml = "";
  if (enemy.absorbs && enemy.absorbs.length > 0) {
    absorbsHtml = enemy.absorbs.map(a => `<span class="badge badge-absorb">[ABSORB] ${escapeHtml(a)}</span>`).join("");
  }

  // Elemental Immunities
  let eleImmuneHtml = "";
  if (enemy.elemental_immunities && enemy.elemental_immunities.length > 0) {
    eleImmuneHtml = enemy.elemental_immunities.map(i => `<span class="badge badge-immune">[IMMUNE] ${escapeHtml(i)}</span>`).join("");
  }

  // Elements that restore all of its HP
  let fullCureHtml = "";
  if (enemy.full_cure && enemy.full_cure.length > 0) {
    fullCureHtml = enemy.full_cure.map(a => `<span class="badge badge-absorb">[FULL CURE] ${escapeHtml(a)}</span>`).join("");
  }

  // Restorative absorbed (undead): healing hurts it
  const healingHtml = enemy.healing_damages
    ? `<span class="badge badge-weakness" title="Restorative absorbed">[UNDEAD] Healing magic and items damage it</span>`
    : "";

  const defenseCombined = healingHtml + resistsHtml + absorbsHtml + fullCureHtml + eleImmuneHtml;

  // Status Immunities
  let statusHtml = "";
  if (enemy.status_immunities && enemy.status_immunities.length > 0) {
    statusHtml = enemy.status_immunities.map(s => `<span class="badge badge-status">${escapeHtml(s)}</span>`).join("");
  } else {
    statusHtml = `<span class="empty-notice">Susceptible to all status effects</span>`;
  }

  // Steal Item Block: every steal slot, in the order the game tries them
  let stealHtml = "";
  if (enemy.steals && enemy.steals.length > 0) {
    stealHtml = enemy.steals.map((steal, slot) => {
      const isKey = steal.is_key_steal;
      return `
      <div class="loot-card ${isKey ? 'steal-highlight' : ''}">
        <div class="loot-row">
          <span class="loot-item-name ${isKey ? 'key-item' : ''}">
            ${isKey ? '[KEY STEAL] ' : ''}${escapeHtml(steal.item)}
          </span>
          <span class="loot-rate-tag ${isKey ? 'rare' : ''}">${escapeHtml(steal.rate)}</span>
        </div>
        ${steal.note ? `<div class="steal-note">${escapeHtml(steal.note)}</div>` : ""}
        <div class="loot-row" style="margin-top: 4px;">
          <span class="steal-party-odds" data-slot="${slot}">${escapeHtml(stealOddsText(enemy, slot, party))}</span>
          <button class="btn-steal-calc" data-slot="${slot}">CALC ODDS</button>
        </div>
      </div>
    `;
    }).join("");
  } else {
    stealHtml = `<div class="loot-card"><span class="empty-notice">None (Cannot be stolen)</span></div>`;
  }

  // Defeat Drops Block
  let dropsHtml = "";
  if (enemy.drops && enemy.drops.length > 0) {
    dropsHtml = `
      <div class="loot-card">
        ${enemy.drops.map(d => `
          <div class="loot-row">
            <span class="loot-item-name">${escapeHtml(d.item)}</span>
            <span class="loot-rate-tag">${escapeHtml(d.rate)}</span>
          </div>
        `).join("")}
      </div>
    `;
  } else {
    dropsHtml = `<div class="loot-card"><span class="empty-notice">None (No item dropped on normal defeat)</span></div>`;
  }

  // Morph Opportunity Block
  let morphHtml = "";
  if (enemy.morph) {
    morphHtml = `
      <div class="loot-card morph-highlight">
        <div class="loot-row">
          <div class="morph-title-group">
            <span class="badge badge-morph-req">[MORPH REQUIRED]</span>
            <span class="loot-item-name morph-item-name">${escapeHtml(enemy.morph)}</span>
          </div>
          <span class="loot-rate-tag morph-rate">100% on Morph</span>
        </div>
        <div class="morph-instruction-note">
          Requires landing the final blow with the <strong>Morph</strong> command
        </div>
      </div>
    `;
  } else {
    morphHtml = `<div class="loot-card"><span class="empty-notice">None (Cannot be morphed)</span></div>`;
  }

  // Enemy Skills in the attack list (learned state follows the profile)
  let skillsHtml = "";
  if (enemy.enemy_skills && enemy.enemy_skills.length > 0) {
    skillsHtml = `
      <div class="section-block">
        <div class="section-label">Enemy Skills</div>
        <div class="badge-row">${enemySkillBadgesHtml(enemy.enemy_skills)}</div>
      </div>
    `;
  }

  // Manipulate command list
  let manipulateHtml = "";
  if (enemy.manipulate && enemy.manipulate.length > 0) {
    manipulateHtml = `
      <div class="section-block">
        <div class="section-label">Manipulate Commands</div>
        <div class="manipulate-list">${enemy.manipulate.map(escapeHtml).join(" &middot; ")}</div>
      </div>
    `;
  }

  // Combat stats
  let combatStatsHtml = "";
  if (enemy.stats) {
    const s = enemy.stats;
    const cells = [["STR", s.strength], ["DEF", s.defense], ["MAG", s.magic], ["MDEF", s.magic_defense],
                   ["DEX", s.dexterity], ["LCK", s.luck], ["EVA", s.evade]];
    combatStatsHtml = `
      <div class="section-block">
        <div class="section-label">Combat Stats</div>
        <div class="combat-stats">${cells.map(([label, value]) =>
          `<div class="combat-stat"><span>${label}</span><strong>${Number(value) || 0}</strong></div>`).join("")}</div>
      </div>
    `;
  }

  // Tactical Notes Block
  let notesHtml = "";
  if (enemy.notes) {
    notesHtml = `
      <div class="section-block">
        <div class="section-label">Tactical Intel</div>
        <div class="tactical-notes">${escapeHtml(enemy.notes)}</div>
      </div>
    `;
  }

  card.innerHTML = `
    <div class="card-header">
      <div class="card-title-group">
        <h2>${escapeHtml(enemyLabel(enemy))}${enemy.count && enemy.count > 1 ? ` <span class="enemy-count-badge">x${enemy.count}</span>` : ''}</h2>
        <div class="card-zones">${escapeHtml(zonesText)}</div>
        ${variantHtml(enemy)}
      </div>
      <div class="level-badge">LVL ${enemy.level}</div>
    </div>

    <div class="stats-strip">
      <div class="stat-item">
        <span class="stat-label">HP</span>
        <span class="stat-val hp">${enemy.hp.toLocaleString()}</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">MP</span>
        <span class="stat-val mp">${enemy.mp.toLocaleString()}</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">EXP</span>
        <span class="stat-val">${(enemy.exp || 0).toLocaleString()}</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">AP</span>
        <span class="stat-val">${(enemy.ap || 0).toLocaleString()}</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">GIL</span>
        <span class="stat-val">${(enemy.gil || 0).toLocaleString()}</span>
      </div>
    </div>

    <div class="card-body">
      <div class="section-block">
        <div class="section-label">Elemental Weaknesses</div>
        <div class="badge-row">${weaknessesHtml}</div>
      </div>

      <div class="section-block">
        <div class="section-label">Defensive Affinities</div>
        <div class="badge-row">${defenseCombined || '<span class="empty-notice">Normal (Standard damage)</span>'}</div>
      </div>

      <div class="section-block">
        <div class="section-label">Steal Opportunity</div>
        ${stealHtml}
      </div>

      <div class="section-block">
        <div class="section-label">Defeat Drops (On Kill)</div>
        ${dropsHtml}
      </div>

      <div class="section-block">
        <div class="section-label">Morph Reward</div>
        ${morphHtml}
      </div>

      <div class="section-block">
        <div class="section-label">Status Immunities</div>
        <div class="badge-row">${statusHtml}</div>
      </div>

      ${skillsHtml}
      ${manipulateHtml}
      ${combatStatsHtml}
      ${notesHtml}
    </div>
  `;

  return card;
}

// Delegated Steal Calculator Click Listener
enemiesGrid.addEventListener("click", (e) => {
  const calcBtn = e.target.closest(".btn-steal-calc");
  if (!calcBtn) return;
  const card = calcBtn.closest(".enemy-card");
  const enemyId = card?.getAttribute("data-enemy-id");
  if (enemyId) {
    const enemy = currentEnemiesMap.get(enemyId);
    if (enemy) openStealCalculator(enemy, Number(calcBtn.getAttribute("data-slot")) || 0);
  }
});

// Battle summary strip: encounter-wide tactics from EnemyCompendium.battle_summary
function summaryItem(label, valueHtml, modifier = "") {
  return `<div class="summary-item ${modifier}"><span class="summary-label">${label}</span>` +
    `<span class="summary-value">${valueHtml}</span></div>`;
}

function renderBattleSummary(summary) {
  lastBattleSummary = summary;
  if (!battleSummary) return;
  if (!summary) {
    battleSummary.classList.add("hidden");
    battleSummary.replaceChildren();
    return;
  }
  const items = [];
  const t = summary.totals || {};
  const totalsLabel = summary.formation_known ? "FORMATION TOTAL" : "SEEN SO FAR";
  items.push(summaryItem(totalsLabel,
    `EXP ${(t.exp || 0).toLocaleString()} &middot; AP ${(t.ap || 0).toLocaleString()} &middot; GIL ${(t.gil || 0).toLocaleString()}`));
  if (summary.formation_known && summary.formation) {
    const members = Object.entries(summary.formation).map(([name, count]) =>
      `${count > 1 ? `${count}x ` : ""}${escapeHtml(name)}`).join(", ");
    items.push(summaryItem("FORMATION", members));
  }
  if (summary.cannot_escape === true) {
    items.push(summaryItem("ESCAPE", "Not possible", "summary-danger"));
  } else if (summary.cannot_escape === false) {
    items.push(summaryItem("ESCAPE", "Possible"));
  }
  if (summary.weak_all.length > 0) {
    items.push(summaryItem("WEAK (ALL)", summary.weak_all.map(escapeHtml).join(", "), "summary-good"));
  }
  if (summary.weak_some.length > 0) {
    items.push(summaryItem("WEAK (SOME)", summary.weak_some.map(escapeHtml).join(", ")));
  }
  if (summary.avoid.length > 0) {
    const avoid = summary.avoid.map(a => {
      const parts = [];
      if (a.absorbed_by.length) parts.push(`absorbed by ${a.absorbed_by.map(escapeHtml).join(", ")}`);
      if (a.nullified_by.length) parts.push(`no effect on ${a.nullified_by.map(escapeHtml).join(", ")}`);
      if (a.full_cure_by && a.full_cure_by.length) parts.push(`fully heals ${a.full_cure_by.map(escapeHtml).join(", ")}`);
      return `${escapeHtml(a.element)} (${parts.join("; ")})`;
    }).join(" &middot; ");
    items.push(summaryItem("AVOID", avoid, "summary-warn"));
  }
  if (summary.healing_damages && summary.healing_damages.length > 0) {
    items.push(summaryItem("UNDEAD", `Healing magic and items damage ${summary.healing_damages.map(escapeHtml).join(", ")}`,
      "summary-good"));
  }
  items.push(summaryItem("STATUS (ALL)", summary.status_all.length > 0
    ? summary.status_all.map(escapeHtml).join(", ")
    : "No ailment works on every enemy"));
  for (const skill of summary.enemy_skills) {
    const learned = isSkillLearned(skill.name);
    const via = skill.manipulate ? ", Manipulate possible" : "";
    items.push(summaryItem(learned ? "E.SKILL (LEARNED)" : "E.SKILL (NEW)",
      `${escapeHtml(skill.name)} <span class="summary-dim">(${skill.sources.map(escapeHtml).join(", ")}${via})</span>`,
      learned ? "" : "summary-eskill"));
  }
  battleSummary.innerHTML = items.join("");
  battleSummary.classList.remove("hidden");
}

// Profile: refreshes everything that depends on it without rebuilding the cards
function applyProfile(profile) {
  if (!profile) return;
  playerProfile = {
    party_level: profile.party_level,
    sneak_glove: !!profile.sneak_glove,
    learned_enemy_skills: Array.isArray(profile.learned_enemy_skills) ? profile.learned_enemy_skills : []
  };
  const learnedCount = playerProfile.learned_enemy_skills.length;
  if (skillsCountText) skillsCountText.textContent = `${learnedCount}/${ENEMY_SKILL_TOTAL}`;
  if (skillsModalCount) skillsModalCount.textContent = `${learnedCount}/${ENEMY_SKILL_TOTAL}`;

  enemiesGrid.querySelectorAll(".badge-eskill[data-skill]").forEach(badge => {
    const learned = isSkillLearned(badge.getAttribute("data-skill"));
    badge.classList.toggle("learned", learned);
    const stateEl = badge.querySelector(".eskill-state");
    if (stateEl) stateEl.textContent = learned ? "LEARNED" : "NEW";
  });
  if (lastBattleSummary) renderBattleSummary(lastBattleSummary);
  if (skillsList) {
    skillsList.querySelectorAll("input[data-skill]").forEach(box => {
      box.checked = isSkillLearned(box.getAttribute("data-skill"));
    });
  }
  // Steal calculator inputs (left alone while the user is typing in them)
  if (stealerLevelInput && document.activeElement !== stealerLevelInput) {
    stealerLevelInput.value = playerProfile.party_level;
  }
  if (sneakGloveCheckbox) sneakGloveCheckbox.checked = playerProfile.sneak_glove;
  if (stealModal && stealModal.open) performStealCalc();
}

function saveProfile(changes) {
  return fetch("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes)
  })
    .then(res => res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`)))
    .then(applyProfile)
    .catch(err => console.error("Error saving profile:", err));
}

// Enemy Skill checklist
let enemySkillSources = null;

function renderSkillsList() {
  if (!skillsList || !enemySkillSources) return;
  skillsList.innerHTML = enemySkillSources.map(skill => {
    const sources = skill.sources.map(s => `${escapeHtml(s.name)}${s.manipulate ? " (M)" : ""}`).join(", ");
    return `<li class="skill-row">
      <label>
        <input type="checkbox" data-skill="${escapeHtml(skill.name)}" ${isSkillLearned(skill.name) ? "checked" : ""} />
        <span class="skill-name">${escapeHtml(skill.name)}</span>
      </label>
      <span class="skill-sources">${sources || "No source in the compendium"}</span>
    </li>`;
  }).join("");
}

openSkillsBtn.addEventListener("click", () => {
  if (skillsModal && typeof skillsModal.showModal === "function") skillsModal.showModal();
  if (enemySkillSources) {
    renderSkillsList();
    return;
  }
  skillsList.innerHTML = `<li class="empty-notice">Loading Enemy Skills...</li>`;
  fetch("/api/enemy-skills")
    .then(res => res.json())
    .then(data => {
      enemySkillSources = data;
      renderSkillsList();
    })
    .catch(err => {
      skillsList.innerHTML = `<li class="empty-notice">Could not load Enemy Skills: ${escapeHtml(String(err))}</li>`;
    });
});

skillsList.addEventListener("change", (e) => {
  const box = e.target.closest("input[data-skill]");
  if (!box) return;
  const learned = new Set(playerProfile.learned_enemy_skills);
  const name = box.getAttribute("data-skill");
  if (box.checked) learned.add(name); else learned.delete(name);
  saveProfile({ learned_enemy_skills: Array.from(learned) });
});

closeSkillsModalBtn.addEventListener("click", () => skillsModal.close());

// Misdetection report
openReportBtn.addEventListener("click", () => {
  const shown = Array.from(currentEnemiesMap.values())
    .map(e => `${enemyLabel(e)}${e.count > 1 ? ` x${e.count}` : ""}`).join(", ");
  reportShownText.textContent = `Dashboard currently shows: ${shown || "no enemies"}`;
  reportResult.classList.add("hidden");
  reportResult.textContent = "";
  if (reportModal && typeof reportModal.showModal === "function") reportModal.showModal();
  reportExpectedInput.focus();
});

closeReportModalBtn.addEventListener("click", () => reportModal.close());

saveReportBtn.addEventListener("click", () => {
  saveReportBtn.disabled = true;
  reportResult.classList.remove("hidden", "report-error");
  reportResult.textContent = "Capturing and recognizing the frames...";
  fetch("/api/report/misdetection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected: reportExpectedInput.value.trim() || null,
      note: reportNoteInput.value.trim() || null
    })
  })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      if (!ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
      const expected = Object.entries(data.expected || {}).map(([n, c]) => `${n}${c > 1 ? ` x${c}` : ""}`).join(", ");
      const lines = [`Saved to ${data.folder} (${data.files.join(", ")}).`];
      if (expected) lines.push(`Expected: ${expected}.`);
      if (data.unresolved && data.unresolved.length) lines.push(`Not recognized as enemy names: ${data.unresolved.join(", ")}.`);
      if (data.needs_review) lines.push("No expected enemies given: the manifest records what was detected; check it before using the frames as a test.");
      reportResult.textContent = lines.join(" ");
      reportExpectedInput.value = "";
      reportNoteInput.value = "";
    })
    .catch(err => {
      reportResult.classList.add("report-error");
      reportResult.textContent = `Report not saved: ${err.message || err}`;
    })
    .finally(() => {
      saveReportBtn.disabled = false;
    });
});

// Utility: HTML Escaping
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Manual Search Logic with AbortController and Keyboard Navigation
let searchDebounceTimer = null;
let searchAbortController = null;
let selectedSearchIndex = -1;

function closeSearchDropdown() {
  searchResultsDropdown.classList.remove("visible");
  enemySearchInput.setAttribute("aria-expanded", "false");
  enemySearchInput.removeAttribute("aria-activedescendant");
  selectedSearchIndex = -1;
}

const searchCache = new Map();

enemySearchInput.addEventListener("input", (e) => {
  clearTimeout(searchDebounceTimer);
  if (searchAbortController) {
    searchAbortController.abort();
    searchAbortController = null;
  }

  const q = e.target.value.trim();
  if (q.length === 0) {
    closeSearchDropdown();
    searchResultsDropdown.innerHTML = "";
    return;
  }

  const cacheKey = q.toLowerCase();
  if (searchCache.has(cacheKey)) {
    renderSearchResults(searchCache.get(cacheKey));
    return;
  }

  searchDebounceTimer = setTimeout(() => {
    searchAbortController = new AbortController();
    fetch(`/api/search?q=${encodeURIComponent(q)}`, { signal: searchAbortController.signal })
      .then(res => res.json())
      .then(results => {
        searchCache.set(cacheKey, results);
        renderSearchResults(results);
      })
      .catch(err => {
        if (err.name !== "AbortError") {
          console.error("Search error:", err);
        }
      });
  }, 200);
});

function renderSearchResults(enemies) {
  selectedSearchIndex = -1;
  if (!enemies || enemies.length === 0) {
    searchResultsDropdown.innerHTML = `<div class="search-item" style="color: var(--text-dim);" role="option">No enemy found</div>`;
    searchResultsDropdown.classList.add("visible");
    enemySearchInput.setAttribute("aria-expanded", "true");
    return;
  }

  searchResultsDropdown.innerHTML = enemies.map((e, idx) => `
    <div class="search-item" data-id="${e.id}" data-idx="${idx}" role="option" id="search-opt-${e.id}">
      <span><strong>${escapeHtml(enemyLabel(e))}</strong></span>
      <span class="search-item-lvl">LVL ${e.level} | ${e.hp} HP</span>
    </div>
  `).join("");
  searchResultsDropdown.classList.add("visible");
  enemySearchInput.setAttribute("aria-expanded", "true");
}

function updateSearchSelection(newIndex) {
  const items = searchResultsDropdown.querySelectorAll(".search-item[data-id]");
  if (items.length === 0) return;

  if (newIndex < 0) newIndex = items.length - 1;
  if (newIndex >= items.length) newIndex = 0;

  selectedSearchIndex = newIndex;
  items.forEach((item, idx) => {
    if (idx === selectedSearchIndex) {
      item.classList.add("selected");
      item.scrollIntoView({ block: "nearest" });
      enemySearchInput.setAttribute("aria-activedescendant", item.id);
    } else {
      item.classList.remove("selected");
    }
  });
}

enemySearchInput.addEventListener("keydown", (e) => {
  const items = searchResultsDropdown.querySelectorAll(".search-item[data-id]");
  const isVisible = searchResultsDropdown.classList.contains("visible");

  if (e.key === "ArrowDown" && isVisible && items.length > 0) {
    e.preventDefault();
    updateSearchSelection(selectedSearchIndex + 1);
  } else if (e.key === "ArrowUp" && isVisible && items.length > 0) {
    e.preventDefault();
    updateSearchSelection(selectedSearchIndex - 1);
  } else if (e.key === "Enter" && isVisible && items.length > 0) {
    e.preventDefault();
    const targetItem = selectedSearchIndex >= 0 ? items[selectedSearchIndex] : items[0];
    if (targetItem) {
      const enemyId = targetItem.getAttribute("data-id");
      if (enemyId) {
        selectManualEnemy(enemyId);
        closeSearchDropdown();
        enemySearchInput.value = "";
      }
    }
  } else if (e.key === "Escape") {
    closeSearchDropdown();
  }
});

searchResultsDropdown.addEventListener("click", (e) => {
  const item = e.target.closest(".search-item");
  if (!item) return;
  const enemyId = item.getAttribute("data-id");
  if (enemyId) {
    selectManualEnemy(enemyId);
    closeSearchDropdown();
    enemySearchInput.value = "";
  }
});

// Close dropdown on outer click
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-container")) {
    closeSearchDropdown();
  }
});

function selectManualEnemy(enemyId) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      action: "MANUAL_SELECT",
      enemy_id: enemyId
    }));
  }
}

// Clear Battle
clearBattleBtn.addEventListener("click", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: "CLEAR_BATTLE" }));
  }
});

// Test Harness Drawer Controls
toggleSimBtn.addEventListener("click", () => {
  const isOpening = simDrawer.classList.contains("hidden");
  simDrawer.classList.toggle("hidden");
  toggleSimBtn.setAttribute("aria-expanded", isOpening ? "true" : "false");
  if (isOpening && presetButtons.children.length === 0) {
    loadSimulatorPresets();
  }
});

closeSimBtn.addEventListener("click", () => {
  simDrawer.classList.add("hidden");
  toggleSimBtn.setAttribute("aria-expanded", "false");
});

if (presetButtons) {
  presetButtons.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-preset");
    if (!btn) return;
    const idx = parseInt(btn.getAttribute("data-idx"), 10);
    const p = simulatorPresets[idx];
    if (p) {
      fetch("/api/simulate/encounter", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enemy_names: p.enemies,
          battle_title: p.title
        })
      });
    }
  });
}

function loadSimulatorPresets() {
  fetch("/api/simulate/presets")
    .then(res => res.json())
    .then(presets => {
      simulatorPresets = presets;
      presetButtons.innerHTML = presets.map((p, idx) => `
        <button class="btn-preset" data-idx="${idx}">
          <strong>${escapeHtml(p.title)}</strong>
          <span>${escapeHtml(p.enemies.join(" + "))}</span>
        </button>
      `).join("");
    })
    .catch(err => console.error("Error loading presets:", err));
}

// Steal Calculator Logic
function openStealCalculator(enemy, slot) {
  if (!enemy.steals || !enemy.steals[slot]) return;
  currentEnemyForSteal = enemy;
  currentStealSlot = slot;
  stealModalTitle.textContent = `STEAL CALCULATOR: ${enemy.steals[slot].item.toUpperCase()}`;
  targetLevelInput.value = enemy.level;
  if (stealModal && typeof stealModal.showModal === "function") {
    stealModal.showModal();
  }
  performStealCalc();
}

closeStealModalBtn.addEventListener("click", () => {
  if (stealModal) stealModal.close();
});

// Party level and Sneak Glove are saved in the server-side profile (shared by every dashboard);
// typing is debounced so a two-digit level is saved once.
let partyLevelSaveTimer = null;

recalcBtn.addEventListener("click", performStealCalc);
stealerLevelInput.addEventListener("input", () => {
  performStealCalc();
  const level = parseInt(stealerLevelInput.value, 10);
  if (partyLevelSaveTimer) clearTimeout(partyLevelSaveTimer);
  if (!Number.isFinite(level) || level < 1 || level > 99) return;
  partyLevelSaveTimer = setTimeout(() => {
    partyLevelSaveTimer = null;
    if (level !== playerProfile.party_level) saveProfile({ party_level: level });
  }, 600);
});
sneakGloveCheckbox.addEventListener("change", () => {
  performStealCalc();
  saveProfile({ sneak_glove: sneakGloveCheckbox.checked });
});

function performStealCalc() {
  const steals = currentEnemyForSteal ? (currentEnemyForSteal.steals || []) : [];
  if (!steals[currentStealSlot]) return;
  const stealValue = Number(steals[currentStealSlot].base_chance);
  const payload = {
    stealer_level: Math.min(99, Math.max(1, parseInt(stealerLevelInput.value, 10) || 1)),
    target_level: currentEnemyForSteal.level,
    base_chance: Number.isFinite(stealValue) ? stealValue : 8,
    sneak_glove: sneakGloveCheckbox.checked,
    earlier_base_chances: steals.slice(0, currentStealSlot).map(s => Number(s.base_chance) || 0)
  };

  fetch("/api/steal-calc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(res => res.json())
    .then(data => {
      calcResultPercent.textContent = `${data.item_success_percentage}%`;
      calcResultFormula.textContent = currentStealSlot > 0
        ? `${data.formula_note}. Tried after ${currentStealSlot} earlier slot(s): ${data.item_success_percentage}% that all of them fail and this one succeeds.`
        : data.formula_note;
    })
    .catch(err => console.error("Steal calc error:", err));
}

// Live Screen Capture & Calibration Handlers
function checkCaptureStatus() {
  fetch("/api/capture/status")
    .then(res => res.json())
    .then(data => {
      isCaptureActive = data.status.running;
      applyCapturePhase(data.status.combat_phase || "idle", data.status.current_fps);
      if (data.config && autoAddPartnersCheckbox) {
        autoAddPartnersCheckbox.checked = !!data.config.auto_add_scene_partners;
      }
    })
    .catch(err => console.error("Error checking capture status:", err));
}

// Shows the sampling phase on the capture button and in the calibration dialog
function applyCapturePhase(phase, fps) {
  if (!phase) return;
  updateCaptureUi(isCaptureActive, phase, fps);
  const calibSamplingPhase = document.getElementById("calibSamplingPhase");
  if (calibSamplingPhase) {
    calibSamplingPhase.textContent = `${phase.toUpperCase()} (${Number(fps || 0.5).toFixed(2)} FPS)`;
  }
}

function updateCaptureUi(active, phase, fps) {
  isCaptureActive = active;
  if (active) {
    toggleCaptureBtn.classList.add("active");
    const phaseLabel = phase ? ` (${phase.toUpperCase()})` : "";
    captureBtnText.textContent = `CAPTURE: LIVE${phaseLabel}`;
  } else {
    toggleCaptureBtn.classList.remove("active");
    captureBtnText.textContent = "CAPTURE: OFF";
  }
}

// The server also pushes the new status to every dashboard as CAPTURE_STATUS
toggleCaptureBtn.addEventListener("click", () => {
  const endpoint = isCaptureActive ? "/api/capture/stop" : "/api/capture/start";
  fetch(endpoint, { method: "POST" })
    .then(res => res.json())
    .then(data => {
      isCaptureActive = data.running;
      applyCapturePhase(data.combat_phase, data.current_fps);
    })
    .catch(err => console.error("Error toggling capture:", err));
});

// Calibration Modal Controls
openCalibBtn.addEventListener("click", () => {
  if (calibModal && typeof calibModal.showModal === "function") {
    calibModal.showModal();
  }
  scanGameWindow();
});

closeCalibModalBtn.addEventListener("click", () => {
  if (calibModal) calibModal.close();
});

saveCalibBtn.addEventListener("click", () => {
  if (calibModal) calibModal.close();
});

// Light Dismiss on Backdrop Click for Native Dialogs
[calibModal, stealModal, skillsModal, reportModal].forEach(dialog => {
  if (!dialog) return;
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog || !e.target.closest(".modal-window")) {
      dialog.close();
    }
  });
});

// Global Keyboard Shortcuts
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    [stealModal, calibModal, skillsModal, reportModal].forEach(dialog => {
      if (dialog && dialog.open) dialog.close();
    });
    simDrawer.classList.add("hidden");
    toggleSimBtn.setAttribute("aria-expanded", "false");
    closeSearchDropdown();
  } else if (e.key === "/" && !e.target.closest("input, textarea")) {
    e.preventDefault();
    enemySearchInput.focus();
    enemySearchInput.select();
  }
});

rescanWindowBtn.addEventListener("click", scanGameWindow);

function scanGameWindow() {
  windowStatusText.textContent = "Scanning running processes...";
  const configRequest = fetch("/api/calibration/config").then(res => res.json());
  // The status without a game window depends on the capture mode: auto_window waits for the
  // window, the other modes capture the monitor (server: CaptureService._calculate_capture_bbox)
  Promise.all([fetch("/api/calibration/window").then(res => res.json()), configRequest.catch(() => null)])
    .then(([data, calib]) => {
      if (data.found && data.window) {
        const win = data.window;
        windowStatusText.textContent = `FFVII Found: "${win.title}" (${win.width}x${win.height} at [${win.left}, ${win.top}])`;
        windowStatusText.style.color = "var(--accent-mako)";
      } else {
        const mode = calib && calib.config ? calib.config.mode : "auto_window";
        windowStatusText.textContent = mode === "auto_window"
          ? "FFVII not detected (live capture starts when the game window is found)"
          : `FFVII not detected (mode "${mode}" captures the monitor instead)`;
        windowStatusText.style.color = "var(--accent-orange)";
      }
    })
    .catch(err => {
      windowStatusText.textContent = "Error scanning window: " + err;
    });

  // Also sync config on load
  configRequest
    .then(data => {
      const rawPreset = data.config.target_menu_preset;
      // Legacy alias written by older builds
      const preset = rawPreset === "bottom_left_targets" ? "center_target_box" : rawPreset;
      presetSelectGrid.querySelectorAll(".btn-preset-calib").forEach(b => {
        b.classList.remove("active");
        if (b.getAttribute("data-preset") === preset) {
          b.classList.add("active");
        }
      });
      renderSamplingRates(data.config);
    })
    .catch(err => console.error("Error fetching config:", err));

  // Load performance metrics
  loadPerformanceMetrics();
}

// Fill the adaptive sampling description with the configured per-phase rates
function renderSamplingRates(config) {
  if (!config) return;
  const fmt = (v) => `${Number(v).toFixed(2).replace(/0$/, "")} FPS`;
  const idleEl = document.getElementById("idleFpsText");
  const activeEl = document.getElementById("activeFpsText");
  const settledEl = document.getElementById("settledFpsText");
  if (idleEl && config.idle_fps !== undefined) idleEl.textContent = fmt(config.idle_fps);
  if (activeEl && config.active_fps !== undefined) activeEl.textContent = fmt(config.active_fps);
  if (settledEl && config.settled_fps !== undefined) settledEl.textContent = fmt(config.settled_fps);
}

function loadPerformanceMetrics() {
  fetch("/api/performance")
    .then(res => res.json())
    .then(data => {
      if (data && data.performance) {
        renderPerformanceStats(data.performance);
      }
    })
    .catch(err => console.error("Error loading performance stats:", err));
  checkCaptureStatus();
}

function renderPerformanceStats(p) {
  if (!p || !p.has_data) {
    if (perfStatGrab) perfStatGrab.textContent = "N/A";
    if (perfStatOcr) perfStatOcr.textContent = "N/A";
    if (perfStatMatch) perfStatMatch.textContent = "N/A";
    if (perfStatTotal) perfStatTotal.textContent = "N/A";
    if (perfLifetimeStats) perfLifetimeStats.textContent = "Lifetime: 0 frames | 0.0 FPS";
    return;
  }

  const s = p.stats;
  if (perfStatGrab && s.grab_ms) perfStatGrab.textContent = `${s.grab_ms.avg}ms`;
  if (perfStatOcr && s.ocr_total_ms) perfStatOcr.textContent = `${s.ocr_total_ms.avg}ms`;
  if (perfStatMatch && s.match_ms) perfStatMatch.textContent = `${s.match_ms.avg}ms`;
  if (perfStatTotal && s.total_frame_ms) perfStatTotal.textContent = `${s.total_frame_ms.avg}ms`;
  
  if (perfLifetimeStats) {
    const cpu = (p.capture_cpu_percent === null || p.capture_cpu_percent === undefined)
      ? "n/a" : `${p.capture_cpu_percent}% of one core`;
    const ocrShare = (p.ocr_ratio_recent === undefined) ? "" : ` | OCR on ${Math.round(p.ocr_ratio_recent * 100)}% of frames`;
    perfLifetimeStats.textContent = `Lifetime: ${p.total_frames_lifetime} frames (${p.combat_frames_lifetime} combat) | ` +
      `Sampling: ${p.sample_fps ?? p.effective_fps} FPS${ocrShare} | Capture CPU: ${cpu} | Uptime: ${p.uptime_seconds}s`;
  }
}

if (refreshPerfBtn) {
  refreshPerfBtn.addEventListener("click", () => {
    loadPerformanceMetrics();
  });
}

if (togglePerfLogsBtn && perfLogsContainer) {
  togglePerfLogsBtn.addEventListener("click", () => {
    const isHidden = perfLogsContainer.classList.contains("hidden");
    if (isHidden) {
      perfLogsContainer.classList.remove("hidden");
      perfLogsContainer.textContent = "Loading performance log lines...";
      fetch("/api/performance/logs?lines=40")
        .then(res => res.json())
        .then(data => {
          if (data && data.logs && data.logs.length > 0) {
            perfLogsContainer.textContent = data.logs.join("\n");
            perfLogsContainer.scrollTop = perfLogsContainer.scrollHeight;
          } else {
            perfLogsContainer.textContent = "No log lines recorded yet. Start capture to generate logs.";
          }
        })
        .catch(err => {
          perfLogsContainer.textContent = "Error reading log: " + err;
        });
    } else {
      perfLogsContainer.classList.add("hidden");
    }
  });
}

// Preset selection with event delegation
if (presetSelectGrid) {
  presetSelectGrid.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-preset-calib");
    if (!btn) return;
    presetSelectGrid.querySelectorAll(".btn-preset-calib").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const presetKey = btn.getAttribute("data-preset");
    if (presetKey) {
      fetch(`/api/calibration/preset/${presetKey}`, { method: "POST" })
        .then(res => res.json())
        .catch(err => console.error("Error setting preset:", err));
    }
  });
}

if (autoAddPartnersCheckbox) {
  autoAddPartnersCheckbox.addEventListener("change", () => {
    fetch("/api/calibration/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_add_scene_partners: autoAddPartnersCheckbox.checked })
    }).catch(err => console.error("Error updating auto-add partners config:", err));
  });
}

// Diagnostic Snapshot Test
testSnapshotBtn.addEventListener("click", () => {
  diagnosticOutput.classList.remove("hidden");
  diagDim.textContent = "Capturing the region and recognizing its text...";
  if (diagPerf) diagPerf.textContent = "";
  diagLines.textContent = "";
  diagEnemies.textContent = "";

  fetch("/api/calibration/snapshot", { method: "POST" })
    .then(res => res.json())
    .then(data => {
      diagDim.textContent = `Captured region: ${data.bbox.width}x${data.bbox.height} at screen (${data.bbox.left}, ${data.bbox.top}), ` +
        `OCR input ${data.dimensions.width}x${data.dimensions.height}`;
      
      if (data.performance && diagPerf) {
        const perf = data.performance;
        diagPerf.innerHTML = `<strong>Pipeline Latency:</strong> ` +
          `Grab: <strong>${perf.grab_ms}ms</strong> | ` +
          `Cvt: <strong>${perf.cvt_color_ms}ms</strong> | ` +
          `OCR Det: <strong>${perf.ocr_det_ms}ms</strong> | ` +
          `OCR Rec: <strong>${perf.ocr_rec_ms}ms</strong> (${perf.ocr_boxes ?? 0} boxes) | ` +
          `Match: <strong>${perf.match_ms}ms</strong> | ` +
          `Total: <strong>${perf.total_frame_ms}ms</strong> (${perf.effective_fps} FPS)`;
      }

      if (data.recognized_lines && data.recognized_lines.length > 0) {
        diagLines.innerHTML = `<strong>OCR Recognized Text Lines:</strong><br>${data.recognized_lines.map(escapeHtml).join("<br>")}`;
      } else {
        diagLines.textContent = "No text recognized in target region.";
      }

      if (data.matched_enemies && data.matched_enemies.length > 0) {
        diagEnemies.textContent = `Matched Enemies: ${data.matched_enemies.join(", ")}`;
      } else {
        diagEnemies.textContent = "No enemy names matched in active view.";
      }

      loadPerformanceMetrics();
    })
    .catch(err => {
      diagDim.textContent = "Error capturing diagnostic snapshot: " + err;
    });
});

// Native Desktop Application Integration
const pinWindowBtn = document.getElementById("pinWindowBtn");

function updatePinButton(isOnTop) {
  if (!pinWindowBtn) return;
  if (isOnTop) {
    pinWindowBtn.textContent = "PIN: ON";
    pinWindowBtn.classList.add("btn-pinned");
    pinWindowBtn.title = "Always on Top: Enabled (Click to unpin)";
  } else {
    pinWindowBtn.textContent = "PIN: OFF";
    pinWindowBtn.classList.remove("btn-pinned");
    pinWindowBtn.title = "Always on Top: Disabled (Click to pin)";
  }
}

function initDesktopBridge() {
  if (!pinWindowBtn) return;
  fetch("/api/desktop/status")
    .then(res => res.json())
    .then(data => {
      if (data && data.is_desktop) {
        pinWindowBtn.classList.remove("hidden");
        updatePinButton(data.on_top);
        pinWindowBtn.onclick = () => {
          fetch("/api/desktop/toggle-pin", { method: "POST" })
            .then(r => r.json())
            .then(resData => {
              if (resData) updatePinButton(resData.on_top);
            })
            .catch(err => console.error("Error toggling pin:", err));
        };
      }
    })
    .catch(() => {});
}

// Startup
window.addEventListener("DOMContentLoaded", () => {
  initWebSocket();
  checkCaptureStatus();
  initDesktopBridge();
});
