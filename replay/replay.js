"use strict";

const ROUND_SECONDS = 115;
const BOMB_SECONDS = 40;
const SEEK_SECONDS = 5;
const KILL_LINE_SECONDS = 1.6;
const KILL_SEEK_LEAD_SECONDS = 3;
const MAX_ZOOM = 6;
const ZOOM_STEP = 1.2;
// Effect radii in game units. They are drawn to scale on the radar.
const SMOKE_RADIUS = 144;
const INFERNO_RADIUS = 110;
const BURST_RADIUS = { flash: 130, he: 170 };
const FIRE_POINT_RADIUS = 34;
const KIND_COLORS = { smoke: "#8dd3f3", flash: "#f7df88", molo: "#fa9c77", he: "#ed91af" };
const END_REASONS = {
    1: "Bombe explosée", 7: "Bombe désamorcée", 8: "Élimination des T", 9: "Élimination des CT", 12: "Temps écoulé",
    17: "Abandon des T", 18: "Abandon des CT",
};
const ECONOMY_LABELS = { pistol: "Pistol", eco: "Éco", semi: "Semi-éco", "force-buy": "Force", full: "Full buy" };
const KILL_FLAGS = { hs: "HS", wall: "Mur", smoke: "Smoke", noscope: "No scope", flash: "Aveuglé" };

const byId = (id) => document.getElementById(id);
const navigation = window.parent !== window ? window.parent.utilityNavigation : undefined;
const style = getComputedStyle(document.documentElement);
const SIDE_COLORS = { t: style.getPropertyValue("--side-t").trim(), ct: style.getPropertyValue("--side-ct").trim() };

const listView = byId("replay-list-view");
const viewer = byId("replay-viewer");
const canvas = byId("replay-canvas");
const context = canvas.getContext("2d");
const radarElement = byId("replay-radar");
const timeControl = byId("replay-time");
const playButton = byId("replay-play");
const speedSelect = byId("replay-speed");
const floorSelect = byId("replay-floor");
const namesToggle = byId("replay-names");
const trailsToggle = byId("replay-trails");

let replays = [];
let listTimer;
let replay;
let rounds = [];
let round;
let time = 0;
let playbackFrame;
let lastFrameTime;
let selectedPlayer;
let images = {};
let view = { zoom: 1, x: 0.5, y: 0.5 };
let pan;
let loadToken = 0;

function mapLabel(name) {
    const short = name.replace(/^(de|cs|ar)_/, "").replaceAll("_", " ");
    return short.charAt(0).toUpperCase() + short.slice(1);
}

function formatBytes(bytes) {
    if (!bytes) return "—";
    const units = ["o", "Ko", "Mo", "Go"];
    let value = bytes;
    let unit = 0;
    while (value >= 1000 && unit < units.length - 1) {
        value /= 1024;
        unit++;
    }
    return `${value.toLocaleString("fr-FR", { maximumFractionDigits: value < 10 && unit ? 1 : 0 })} ${units[unit]}`;
}

function formatDate(replayEntry) {
    const date = replayEntry.date ? new Date(replayEntry.date) : new Date(replayEntry.importedAt * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });
}

function clockLabel(seconds) {
    const value = Math.max(0, Math.ceil(seconds));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

function matchLabel(entry) {
    const teams = entry.teams ?? [];
    if (teams.length === 2 && teams.every((team) => Number.isInteger(team.score))) {
        return `${teams[0].name} ${teams[0].score} – ${teams[1].score} ${teams[1].name}`;
    }
    if (teams.length) return teams.map((team) => team.name).join(" vs ");
    return entry.demo || "Démo sans nom";
}

async function fetchJson(path, payload) {
    const options = payload === undefined ? undefined : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    };
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "La requête a échoué.");
    return data;
}

function currentParams() {
    return new URLSearchParams(navigation ? navigation.search() : location.search);
}

function syncAddress(replace = false) {
    const params = new URLSearchParams();
    if (replay) {
        params.set("id", replay.id);
        if (round) params.set("round", round.number);
        const seconds = Math.floor(time / replay.tickRate);
        if (seconds) params.set("t", seconds);
    }
    if (navigation?.updateReplay) navigation.updateReplay(params.toString(), replace);
    else if (`?${params}` !== location.search) {
        if (replace) history.replaceState(null, "", `?${params}`);
        else history.pushState(null, "", `?${params}`);
    }
}

function confirmAction(title, text, confirmLabel) {
    const dialog = byId("replay-dialog");
    byId("replay-dialog-title").textContent = title;
    byId("replay-dialog-text").textContent = text;
    byId("replay-dialog-confirm").textContent = confirmLabel;
    dialog.returnValue = "";
    dialog.showModal();
    return new Promise((resolve) => {
        dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    });
}

/* ---------- Replay list ---------- */

function renderList() {
    const query = byId("replay-search").value.trim().toLocaleLowerCase("fr");
    const visible = replays.filter((entry) => !query || [matchLabel(entry), entry.demo, mapLabel(entry.map), entry.map]
        .some((text) => text.toLocaleLowerCase("fr").includes(query)));
    const status = byId("replay-list-status");
    byId("replay-table-panel").hidden = !visible.length;
    status.hidden = Boolean(visible.length);
    status.textContent = replays.length ? "Aucun replay ne correspond à la recherche."
        : "Aucun replay pour l’instant. Importe une démo .dem ou une archive .rar pour créer un replay.";
    const template = byId("replay-row-template");
    byId("replay-rows").replaceChildren(...visible.map((entry) => {
        const row = template.content.firstElementChild.cloneNode(true);
        const open = row.querySelector(".replay-open");
        open.textContent = matchLabel(entry);
        open.addEventListener("click", () => openReplay(entry.id));
        if (!entry.current) {
            const badge = document.createElement("span");
            badge.className = "replay-badge";
            badge.textContent = entry.simplified ? "Simplifié" : "À mettre à jour";
            badge.title = entry.simplified ? "Relevé ancien : positions uniquement, sans kills ni bombe."
                : "Relevé d’une version précédente : la forme des molotovs est approximative.";
            open.append(badge);
        }
        row.querySelector(".replay-match small").textContent = entry.demo;
        row.querySelector(".replay-map").textContent = mapLabel(entry.map);
        row.querySelector(".replay-date").textContent = formatDate(entry);
        row.querySelector(".replay-rounds").textContent = entry.rounds;
        row.querySelector(".replay-size span").textContent = formatBytes(entry.replaySize + entry.demoSize);
        row.querySelector(".replay-size small").textContent = entry.demoSaved
            ? `Démo ${formatBytes(entry.demoSize)}` : "Démo non conservée";
        const update = row.querySelector(".replay-update");
        update.hidden = entry.current || !entry.demoSaved;
        update.addEventListener("click", () => reanalyze(entry));
        const deleteDemo = row.querySelector(".replay-delete-demo");
        deleteDemo.hidden = !entry.demoSaved;
        deleteDemo.addEventListener("click", () => removeReplay(entry, "demo"));
        row.querySelector(".replay-delete").addEventListener("click", () => removeReplay(entry, "all"));
        return row;
    }));
}

function renderUsage(data) {
    const { usage, running } = data;
    const parts = [`${replays.length} ${replays.length === 1 ? "replay" : "replays"}`,
        `relevés ${formatBytes(usage.replays)}`, `démos conservées ${formatBytes(usage.demos)}`];
    if (running) parts.push("mise à jour en cours…");
    byId("replay-usage").textContent = parts.join(" · ");
    for (const button of document.querySelectorAll(".replay-actions button")) button.disabled = data.busy;
}

async function loadList() {
    clearTimeout(listTimer);
    try {
        const data = await fetchJson("/api/replays");
        replays = data.replays;
        renderList();
        renderUsage(data);
        if (data.busy) listTimer = setTimeout(loadList, 1500);
    } catch (error) {
        byId("replay-list-status").hidden = false;
        byId("replay-list-status").textContent = error.message;
    }
}

async function removeReplay(entry, scope) {
    const demoOnly = scope === "demo";
    const confirmed = await confirmAction(
        demoOnly ? "Supprimer la démo conservée ?" : "Supprimer ce replay ?",
        demoOnly
            ? `${entry.demo} · ${formatBytes(entry.demoSize)} libérés. Le replay reste consultable, mais il ne pourra plus être mis à jour sans réimporter la démo.`
            : `${matchLabel(entry)} · ${formatBytes(entry.replaySize + entry.demoSize)} libérés. Ses positions disparaissent aussi de la heatmap Joueurs. Les clips de la vidéothèque ne changent pas.`,
        demoOnly ? "Supprimer la démo" : "Supprimer le replay");
    if (!confirmed) return;
    try {
        await fetchJson("/api/replays/delete", { id: entry.id, scope });
    } catch (error) {
        byId("replay-usage").textContent = error.message;
    }
    loadList();
}

async function reanalyze(entry) {
    try {
        await fetchJson("/api/replays/reanalyze", { id: entry.id });
    } catch (error) {
        byId("replay-usage").textContent = error.message;
        return;
    }
    loadList();
}

/* ---------- Replay data ---------- */

function fireAt(grenade, tick) {
    // Timeline of [tick, hull]: the latest hull at or before the current tick is the burning area.
    let hull;
    for (const [hullTick, values] of grenade.fire ?? []) {
        if (hullTick > tick) break;
        hull = values;
    }
    return hull?.length ? hull : undefined;
}

function prepareRound(stage, index) {
    if (stage.prepared) return stage;
    const players = Object.entries(stage.sides).map(([id, side]) => ({
        id, side, name: replay.players[id] ?? id, track: stage.tracks[id] ?? [],
        death: stage.deathTicks?.[id], health: [],
    }));
    const byPlayer = new Map(players.map((player) => [player.id, player]));
    for (const [tick, id, health] of stage.health ?? []) byPlayer.get(id)?.health.push([tick, health]);
    for (const grenade of stage.grenades ?? []) {
        const path = [];
        for (let i = 0; i < grenade.path.length; i += 4) path.push(grenade.path.slice(i, i + 4));
        grenade.points = path;
        grenade.side = stage.sides[grenade.p];
    }
    stage.kills ??= [];
    if (!stage.kills.length && stage.deathTicks) {
        stage.deathList = Object.entries(stage.deathTicks).map(([id, tick]) => ({ t: tick, v: id })).sort((a, b) => a.t - b.t);
    }
    const previous = rounds[index - 1];
    stage.scoreBefore = previous?.score ?? (stage.score ? { t: 0, ct: 0 } : undefined);
    stage.duration = stage.endTick - stage.startTick;
    stage.players = players;
    stage.byPlayer = byPlayer;
    stage.prepared = true;
    return stage;
}

function interpolatePosition(track, tick) {
    if (!track.length) return null;
    if (tick <= track[0][0]) return track[0];
    let low = 0;
    let high = track.length - 1;
    if (tick >= track[high][0]) return track[high];
    while (high - low > 1) {
        const middle = (low + high) >> 1;
        if (track[middle][0] <= tick) low = middle;
        else high = middle;
    }
    const [t0, x0, y0, z0, yaw0] = track[low];
    const [t1, x1, y1, z1, yaw1] = track[high];
    const ratio = (tick - t0) / (t1 - t0 || 1);
    let yaw;
    if (yaw0 !== undefined && yaw1 !== undefined) {
        const turn = ((yaw1 - yaw0 + 540) % 360) - 180;
        yaw = yaw0 + turn * ratio;
    }
    return [tick, x0 + (x1 - x0) * ratio, y0 + (y1 - y0) * ratio, z0 + (z1 - z0) * ratio, yaw];
}

function grenadePosition(points, tick) {
    if (tick <= points[0][0]) return points[0];
    for (let i = 1; i < points.length; i++) {
        if (points[i][0] >= tick) {
            const [t0, x0, y0, z0] = points[i - 1];
            const [t1, x1, y1, z1] = points[i];
            const ratio = (tick - t0) / (t1 - t0 || 1);
            return [tick, x0 + (x1 - x0) * ratio, y0 + (y1 - y0) * ratio, z0 + (z1 - z0) * ratio];
        }
    }
    return points.at(-1);
}

function healthAt(player, tick) {
    if (player.death !== undefined && tick >= player.death) return 0;
    let health = 100;
    for (const [eventTick, value] of player.health) {
        if (eventTick > tick) break;
        health = value;
    }
    return health;
}

/* ---------- Radar drawing ---------- */

function resizeCanvas() {
    const ratio = window.devicePixelRatio || 1;
    const size = Math.round(radarElement.clientWidth * ratio);
    if (size && canvas.width !== size) canvas.width = canvas.height = size;
}

function toScreen(x, y) {
    const { radar } = replay;
    const size = canvas.width;
    const u = (x - radar.originX) / radar.units;
    const v = (radar.originY - y) / radar.units;
    return [(u - view.x) * view.zoom * size + size / 2, (v - view.y) * view.zoom * size + size / 2];
}

function unitsToPixels(units) {
    return units / replay.radar.units * view.zoom * canvas.width;
}

function floorOf(z) {
    const { radar } = replay;
    return radar.lower && z < radar.thresholdZ ? "lower" : "upper";
}

function floorAlpha(z) {
    return floorOf(z) === floorSelect.value ? 1 : 0.28;
}

function drawImageLayer() {
    const size = canvas.width;
    const image = images[floorSelect.value];
    context.fillStyle = "#10161c";
    context.fillRect(0, 0, size, size);
    if (!image?.complete || !image.naturalWidth) return;
    const span = view.zoom * size;
    context.drawImage(image, (0 - view.x) * span + size / 2, (0 - view.y) * span + size / 2, span, span);
}

function effectAlpha(tick, start, end) {
    const fade = replay.tickRate * 0.8;
    return Math.max(0, Math.min(1, (tick - start) / fade, (end - tick) / fade));
}

function drawEffects(stage, tick) {
    for (const grenade of stage.grenades ?? []) {
        const [launch, activation, end] = grenade.t;
        if (tick < activation || tick > end) continue;
        const [x, y, z] = grenade.at;
        const [sx, sy] = toScreen(x, y);
        const alpha = floorAlpha(z);
        context.save();
        if (grenade.k === "smoke") {
            context.globalAlpha = 0.62 * alpha * effectAlpha(tick, activation, end);
            context.fillStyle = "#cfd8df";
            context.beginPath();
            context.arc(sx, sy, unitsToPixels(SMOKE_RADIUS), 0, Math.PI * 2);
            context.fill();
            context.globalAlpha = 0.9 * alpha * effectAlpha(tick, activation, end);
            context.strokeStyle = SIDE_COLORS[grenade.side] ?? "#ffffff";
            context.lineWidth = Math.max(1.5, canvas.width / 600);
            context.stroke();
        } else if (grenade.k === "molo") {
            context.globalAlpha = 0.55 * alpha * effectAlpha(tick, activation, end);
            context.fillStyle = "#ff6a2b";
            context.strokeStyle = "#ffb067";
            context.lineWidth = Math.max(1, canvas.width / 900);
            const hull = fireAt(grenade, tick);
            if (hull) {
                // The demo records the hull of the flame centres. Widen it by the radius of one flame.
                context.beginPath();
                for (let index = 0; index < hull.length; index += 2) {
                    const [hx, hy] = toScreen(hull[index], hull[index + 1]);
                    if (index) context.lineTo(hx, hy);
                    else context.moveTo(hx, hy);
                }
                if (hull.length === 2) {
                    const [hx, hy] = toScreen(hull[0], hull[1]);
                    context.lineTo(hx + 0.01, hy);
                }
                context.closePath();
                context.lineJoin = context.lineCap = "round";
                context.lineWidth = unitsToPixels(FIRE_POINT_RADIUS * 2);
                context.strokeStyle = "#ff6a2b";
                if (hull.length >= 6) context.fill();
                context.stroke();
            } else if (!grenade.fire) {
                context.beginPath();
                context.arc(sx, sy, unitsToPixels(INFERNO_RADIUS), 0, Math.PI * 2);
                context.fill();
                context.stroke();
            }
        } else {
            const progress = (tick - activation) / Math.max(1, end - activation);
            context.globalAlpha = (1 - progress) * alpha;
            context.strokeStyle = KIND_COLORS[grenade.k];
            context.fillStyle = grenade.k === "flash" ? "#ffffff" : KIND_COLORS.he;
            context.lineWidth = Math.max(2, canvas.width / 400);
            context.beginPath();
            context.arc(sx, sy, unitsToPixels(BURST_RADIUS[grenade.k]) * (0.35 + 0.65 * progress), 0, Math.PI * 2);
            context.globalAlpha = (1 - progress) * 0.35 * alpha;
            context.fill();
            context.globalAlpha = (1 - progress) * alpha;
            context.stroke();
        }
        context.restore();
    }
}

function drawProjectiles(stage, tick) {
    const width = Math.max(1.2, canvas.width / 700);
    for (const grenade of stage.grenades ?? []) {
        const [launch, activation] = grenade.t;
        if (tick < launch || tick >= activation || !grenade.points.length) continue;
        const current = grenadePosition(grenade.points, tick);
        const color = KIND_COLORS[grenade.k];
        context.save();
        context.globalAlpha = floorAlpha(current[3]);
        if (trailsToggle.checked) {
            context.strokeStyle = color;
            context.lineWidth = width;
            context.setLineDash(grenade.approximate ? [width * 3, width * 3] : []);
            context.globalAlpha *= 0.75;
            context.beginPath();
            for (const [index, point] of grenade.points.entries()) {
                if (point[0] > tick) break;
                const [px, py] = toScreen(point[1], point[2]);
                if (index) context.lineTo(px, py);
                else context.moveTo(px, py);
            }
            const [cx, cy] = toScreen(current[1], current[2]);
            context.lineTo(cx, cy);
            context.stroke();
            context.globalAlpha /= 0.75;
        }
        const [cx, cy] = toScreen(current[1], current[2]);
        context.setLineDash([]);
        context.fillStyle = color;
        context.strokeStyle = "#0c1013";
        context.lineWidth = width;
        context.beginPath();
        context.arc(cx, cy, Math.max(3, canvas.width / 260), 0, Math.PI * 2);
        context.fill();
        context.stroke();
        context.restore();
    }
}

function drawKills(stage, tick) {
    const span = KILL_LINE_SECONDS * replay.tickRate;
    for (const kill of stage.kills) {
        if (tick < kill.t || tick > kill.t + span || !kill.k) continue;
        const killer = stage.byPlayer.get(kill.k);
        const [kx, ky] = toScreen(kill.kp[0], kill.kp[1]);
        const [vx, vy] = toScreen(kill.vp[0], kill.vp[1]);
        context.save();
        context.globalAlpha = 1 - (tick - kill.t) / span;
        context.strokeStyle = SIDE_COLORS[killer?.side] ?? "#ffffff";
        context.lineWidth = Math.max(1.5, canvas.width / 520);
        context.setLineDash([canvas.width / 160, canvas.width / 260]);
        context.beginPath();
        context.moveTo(kx, ky);
        context.lineTo(vx, vy);
        context.stroke();
        context.restore();
    }
}

function drawBomb(stage, tick) {
    const plant = stage.bomb?.plant;
    if (!plant || tick < plant.t) return;
    const [sx, sy] = toScreen(plant.at[0], plant.at[1]);
    const exploded = stage.bomb.explode !== undefined && tick >= stage.bomb.explode;
    const defused = stage.bomb.defuse !== undefined && tick >= stage.bomb.defuse;
    const size = Math.max(7, canvas.width / 110);
    context.save();
    context.globalAlpha = floorAlpha(plant.at[2]);
    if (exploded) {
        const progress = Math.min(1, (tick - stage.bomb.explode) / (replay.tickRate * 2));
        context.globalAlpha *= 0.5 * (1 - progress) + 0.15;
        context.fillStyle = "#ff8a3d";
        context.beginPath();
        context.arc(sx, sy, unitsToPixels(500) * (0.4 + 0.6 * progress), 0, Math.PI * 2);
        context.fill();
        context.globalAlpha = floorAlpha(plant.at[2]);
    }
    const blink = !defused && !exploded && Math.floor(tick / (replay.tickRate / 2)) % 2 === 0;
    context.translate(sx, sy);
    context.rotate(Math.PI / 4);
    context.fillStyle = defused ? "#7fd99a" : exploded ? "#ff8a3d" : blink ? "#ff4b3e" : "#b3261e";
    context.strokeStyle = "#0c1013";
    context.lineWidth = Math.max(1.5, canvas.width / 600);
    context.fillRect(-size / 2, -size / 2, size, size);
    context.strokeRect(-size / 2, -size / 2, size, size);
    context.restore();
}

function drawPlayers(stage, tick) {
    const radius = Math.max(5, canvas.width / 105);
    const font = Math.max(10, Math.round(canvas.width / 72));
    const labels = [];
    for (const player of stage.players) {
        const alive = player.death === undefined || tick < player.death;
        const sample = interpolatePosition(player.track, alive ? tick : player.death);
        if (!sample) continue;
        const [, x, y, z, yaw] = sample;
        const [sx, sy] = toScreen(x, y);
        const color = SIDE_COLORS[player.side];
        context.save();
        context.globalAlpha = floorAlpha(z);
        if (!alive) {
            context.globalAlpha *= 0.75;
            context.strokeStyle = color;
            context.lineWidth = Math.max(2, radius / 2.6);
            const arm = radius * 0.8;
            context.beginPath();
            context.moveTo(sx - arm, sy - arm);
            context.lineTo(sx + arm, sy + arm);
            context.moveTo(sx + arm, sy - arm);
            context.lineTo(sx - arm, sy + arm);
            context.stroke();
            context.restore();
            continue;
        }
        if (yaw !== undefined) {
            const angle = -yaw * Math.PI / 180;
            context.fillStyle = color;
            context.globalAlpha *= 0.28;
            context.beginPath();
            context.moveTo(sx, sy);
            context.arc(sx, sy, radius * 3.4, angle - 0.42, angle + 0.42);
            context.closePath();
            context.fill();
            context.globalAlpha /= 0.28;
            context.beginPath();
            context.moveTo(sx + Math.cos(angle) * radius * 1.75, sy + Math.sin(angle) * radius * 1.75);
            context.lineTo(sx + Math.cos(angle + 0.55) * radius * 0.95, sy + Math.sin(angle + 0.55) * radius * 0.95);
            context.lineTo(sx + Math.cos(angle - 0.55) * radius * 0.95, sy + Math.sin(angle - 0.55) * radius * 0.95);
            context.closePath();
            context.fill();
        }
        context.fillStyle = color;
        context.strokeStyle = player.id === selectedPlayer ? "#ffffff" : "#0c1013";
        context.lineWidth = player.id === selectedPlayer ? Math.max(2.5, radius / 2.2) : Math.max(1.5, radius / 4);
        context.beginPath();
        context.arc(sx, sy, radius, 0, Math.PI * 2);
        context.fill();
        context.stroke();
        const health = healthAt(player, tick);
        if (health < 100) {
            context.strokeStyle = "#0c1013a0";
            context.lineWidth = Math.max(2, radius / 2.6);
            context.beginPath();
            context.arc(sx, sy, radius * 0.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (1 - health / 100));
            context.stroke();
        }
        context.restore();
        if (namesToggle.checked || player.id === selectedPlayer) labels.push([player.name, sx, sy - radius * 1.6, floorAlpha(z)]);
    }
    context.save();
    context.font = `600 ${font}px "Geist CS2", "Segoe UI", sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "bottom";
    context.lineJoin = "round";
    context.lineWidth = Math.max(2.5, font / 3.5);
    context.strokeStyle = "#0c1013";
    context.fillStyle = "#f4f6f8";
    for (const [name, x, y, alpha] of labels) {
        context.globalAlpha = alpha;
        context.strokeText(name, x, y);
        context.fillText(name, x, y);
    }
    context.restore();
}

function draw() {
    if (!replay?.radar || !round) return;
    resizeCanvas();
    drawImageLayer();
    drawEffects(round, time);
    drawProjectiles(round, time);
    drawKills(round, time);
    drawBomb(round, time);
    drawPlayers(round, time);
    drawHighlight(time);
}

/* ---------- Side panel ---------- */

function renderScoreboard() {
    for (const side of ["ct", "t"]) {
        const container = byId(`replay-team-${side}`);
        const heading = document.createElement("h2");
        const name = document.createElement("span");
        name.textContent = `${round.teams?.[side] ?? (side === "t" ? "Terroristes" : "Anti-terroristes")} · ${side.toUpperCase()}`;
        const details = document.createElement("span");
        const score = round.scoreBefore?.[side];
        const economy = ECONOMY_LABELS[round.economy?.[side]] ?? "";
        details.textContent = [Number.isInteger(score) ? score : "", economy].filter((part) => part !== "").join(" · ");
        heading.append(name, details);
        const rows = round.players.filter((player) => player.side === side).map((player) => {
            const row = document.createElement("div");
            row.className = "replay-player";
            row.dataset.player = player.id;
            row.setAttribute("role", "button");
            row.tabIndex = 0;
            row.setAttribute("aria-pressed", String(player.id === selectedPlayer));
            const label = document.createElement("span");
            label.className = "replay-player-name";
            label.textContent = player.name;
            const stats = document.createElement("span");
            stats.className = "replay-player-stats";
            const bar = document.createElement("div");
            bar.className = "replay-health";
            bar.append(document.createElement("span"));
            row.append(label, stats, bar);
            const toggle = () => {
                selectedPlayer = selectedPlayer === player.id ? undefined : player.id;
                for (const element of document.querySelectorAll(".replay-player")) {
                    element.setAttribute("aria-pressed", String(element.dataset.player === selectedPlayer));
                }
                draw();
            };
            row.addEventListener("click", toggle);
            row.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    event.stopPropagation();
                    toggle();
                }
            });
            return row;
        });
        container.replaceChildren(heading, ...rows);
    }
}

function updateScoreboard() {
    const kills = new Map();
    for (const kill of round.kills) {
        if (kill.t <= time && kill.k) kills.set(kill.k, (kills.get(kill.k) ?? 0) + 1);
    }
    for (const row of document.querySelectorAll(".replay-player")) {
        const player = round.byPlayer.get(row.dataset.player);
        const health = healthAt(player, time);
        row.classList.toggle("dead", health === 0);
        const count = kills.get(player.id) ?? 0;
        row.querySelector(".replay-player-stats").textContent = `${health} PV${count ? ` · ${count} K` : ""}`;
        row.querySelector(".replay-health span").style.setProperty("--hp", `${health}%`);
    }
}

function playerSpan(id, className) {
    const element = document.createElement("span");
    const player = round.byPlayer.get(id);
    element.className = className;
    element.textContent = player?.name ?? replay.players[id] ?? "Monde";
    if (player) element.style.setProperty("--side", SIDE_COLORS[player.side]);
    return element;
}

function renderFeed() {
    const feed = byId("replay-feed");
    const entries = round.kills.length ? round.kills : round.deathList ?? [];
    byId("replay-feed-title").textContent = round.kills.length ? "Kills" : "Morts";
    if (!entries.length) {
        const empty = document.createElement("li");
        empty.className = "replay-feed-empty";
        empty.textContent = "Aucun kill dans ce round.";
        feed.replaceChildren(empty);
        return;
    }
    feed.replaceChildren(...entries.map((kill) => {
        const item = document.createElement("li");
        item.dataset.tick = kill.t;
        const button = document.createElement("button");
        button.type = "button";
        const clock = document.createElement("time");
        clock.textContent = clockLabel(ROUND_SECONDS - kill.t / replay.tickRate);
        const text = document.createElement("span");
        if (kill.k) text.append(playerSpan(kill.k, "killer"), " ");
        if (kill.w) {
            const weapon = document.createElement("span");
            weapon.className = "weapon";
            weapon.textContent = kill.k ? `${kill.w} ` : `${kill.w} · `;
            text.append(weapon);
        } else {
            text.append("✕ ");
        }
        text.append(playerSpan(kill.v, "victim"));
        for (const flag of kill.f ?? []) {
            const tag = document.createElement("span");
            tag.className = "flag";
            tag.textContent = KILL_FLAGS[flag] ?? flag;
            text.append(tag);
        }
        button.append(clock, text);
        button.addEventListener("click", () => {
            stopPlayback();
            seek(kill.t - KILL_SEEK_LEAD_SECONDS * replay.tickRate);
            syncAddress(true);
        });
        item.append(button);
        return item;
    }));
}

function updateFeed() {
    for (const item of byId("replay-feed").children) {
        if (item.dataset.tick !== undefined) item.classList.toggle("future", Number(item.dataset.tick) > time);
    }
}

function updateClock() {
    const seconds = time / replay.tickRate;
    const plant = round.bomb?.plant;
    const clock = byId("replay-clock");
    const bombClock = byId("replay-bomb-clock");
    if (plant && time >= plant.t) {
        const defused = round.bomb.defuse !== undefined && time >= round.bomb.defuse;
        const exploded = round.bomb.explode !== undefined && time >= round.bomb.explode;
        clock.textContent = clockLabel(BOMB_SECONDS - (time - plant.t) / replay.tickRate);
        clock.style.color = defused ? "#7fd99a" : "#ff7a6b";
        bombClock.textContent = defused ? `Bombe désamorcée · ${plant.site}` : exploded ? `Bombe explosée · ${plant.site}`
            : `Bombe posée · ${plant.site}`;
        bombClock.hidden = false;
    } else {
        clock.textContent = clockLabel(ROUND_SECONDS - seconds);
        clock.style.color = "";
        bombClock.hidden = true;
    }
    timeControl.value = Math.round(time);
    byId("replay-timeline").style.setProperty("--progress", round.duration ? time / round.duration : 0);
    timeControl.setAttribute("aria-valuetext", clock.textContent);
}

function renderMarkers() {
    const killMarkers = [];
    for (const kill of round.kills.length ? round.kills : round.deathList ?? []) {
        const victim = round.byPlayer.get(kill.v);
        killMarkers.push({ tick: kill.t, icon: "skull", color: SIDE_COLORS[victim?.side] ?? "#ffffff",
            seek: kill.t - KILL_SEEK_LEAD_SECONDS * replay.tickRate, highlight: { kill }, tooltip: () => killTooltip(kill) });
    }
    const bomb = round.bomb ?? {};
    for (const [key, color, label] of [["plant", "#ff4b3e", "Bombe posée"], ["defuse", "#7fd99a", "Bombe désamorcée"],
        ["explode", "#ff8a3d", "Bombe explosée"]]) {
        const tick = key === "plant" ? bomb.plant?.t : bomb[key];
        if (tick === undefined) continue;
        killMarkers.push({ tick, icon: "bomb", color, seek: tick - 2 * replay.tickRate, highlight: { bomb: true },
            tooltip: () => [label + (bomb.plant ? ` · site ${bomb.plant.site}` : ""), timeAt(tick)] });
    }
    const utilityMarkers = (round.grenades ?? []).map((grenade) => ({
        tick: grenade.t[1], icon: grenade.k, color: KIND_COLORS[grenade.k],
        seek: grenade.t[0] - replay.tickRate, highlight: { grenade }, tooltip: () => grenadeTooltip(grenade),
    }));
    timelineMarkers = { kills: killMarkers, utilities: utilityMarkers };
    layoutMarkers();
}

let timelineMarkers = { kills: [], utilities: [] };
let highlight;
const MARKER_SPACING_PX = 13;
const MARKER_LANE_PX = 17;
const MAX_MARKER_LANES = { kills: 2, utilities: 3 };

function timeAt(tick) {
    return clockLabel(ROUND_SECONDS - tick / replay.tickRate);
}

function zoneName(position) {
    if (typeof calloutName !== "function" || !replay.zones?.length) return "";
    const name = calloutName({ impact: position }, { radar: replay.radar, zones: replay.zones });
    return name === "À définir" ? "" : name;
}

function killTooltip(kill) {
    const killer = round.byPlayer.get(kill.k ?? "");
    const victim = round.byPlayer.get(kill.v);
    const title = document.createElement("strong");
    if (killer) title.append(playerSpan(kill.k, "side"), " → ");
    title.append(playerSpan(kill.v, "side"));
    const details = [kill.w, ...(kill.f ?? []).map((flag) => KILL_FLAGS[flag] ?? flag)].filter(Boolean);
    const zone = zoneName(kill.vp ?? (victim && interpolatePosition(victim.track, kill.t)?.slice(1)) ?? []);
    return [title, [details.join(" · "), zone, timeAt(kill.t)].filter(Boolean).join(" · ")];
}

function grenadeTooltip(grenade) {
    const label = { smoke: "Smoke", flash: "Flash", molo: "Molotov", he: "HE" }[grenade.k];
    const zone = zoneName(grenade.at) || "Zone à définir";
    const thrower = round.byPlayer.get(grenade.p);
    const title = document.createElement("strong");
    title.textContent = `${label} · ${zone}`;
    const line = document.createElement("span");
    if (thrower) line.append(playerSpan(grenade.p, "side"), " · ");
    line.append(`lancée ${timeAt(grenade.t[0])} · active ${timeAt(grenade.t[1])}`);
    return [title, line, grenade.approximate ? "Trajectoire approximative" : ""];
}

function markerButton(marker, x, lane) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "replay-marker";
    button.style.left = `${x}px`;
    button.style.top = `${lane * MARKER_LANE_PX}px`;
    button.style.setProperty("--marker", marker.color);
    button.dataset.tick = marker.tick;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#icon-${marker.icon}`);
    svg.append(use);
    button.append(svg);
    const show = () => showTooltip(marker, button);
    button.addEventListener("pointerenter", show);
    button.addEventListener("focus", show);
    button.addEventListener("pointerleave", hideTooltip);
    button.addEventListener("blur", hideTooltip);
    button.addEventListener("click", () => {
        stopPlayback();
        seek(marker.seek);
        syncAddress(true);
    });
    return button;
}

function layoutMarkers() {
    for (const [group, markers] of Object.entries(timelineMarkers)) {
        const container = byId(group === "kills" ? "replay-kill-markers" : "replay-utility-markers");
        const width = container.clientWidth;
        const lanes = [];
        // Place each icon in the first lane where it does not cover the previous one.
        const buttons = [...markers].sort((a, b) => a.tick - b.tick).map((marker) => {
            const x = Math.max(0, Math.min(width, marker.tick / round.duration * width));
            let lane = lanes.findIndex((last) => x - last >= MARKER_SPACING_PX);
            if (lane < 0) {
                lane = lanes.length < MAX_MARKER_LANES[group] ? lanes.length
                    : lanes.indexOf(Math.min(...lanes));
            }
            lanes[lane] = x;
            return markerButton(marker, x, lane);
        });
        container.style.setProperty("--lanes-height", `${lanes.length * MARKER_LANE_PX}px`);
        container.replaceChildren(...buttons);
    }
    updateMarkers();
}

function updateMarkers() {
    for (const button of document.querySelectorAll(".replay-marker")) {
        button.classList.toggle("past", Number(button.dataset.tick) <= time);
    }
}

function showTooltip(marker, button) {
    const tooltip = byId("replay-tooltip");
    const lines = marker.tooltip().filter(Boolean).map((line, index) => {
        if (typeof line !== "string") return line;
        const element = document.createElement(index ? "small" : "strong");
        element.textContent = line;
        return element;
    });
    for (const line of lines.slice(1)) if (line.tagName !== "SMALL") line.style.display = "block";
    tooltip.replaceChildren(...lines);
    tooltip.hidden = false;
    const timeline = tooltip.parentElement.getBoundingClientRect();
    const target = button.getBoundingClientRect();
    const half = tooltip.offsetWidth / 2;
    const center = target.left + target.width / 2 - timeline.left;
    tooltip.style.left = `${Math.max(half, Math.min(timeline.width - half, center))}px`;
    highlight = marker.highlight;
    draw();
}

function hideTooltip() {
    byId("replay-tooltip").hidden = true;
    highlight = undefined;
    draw();
}

function drawHighlight(tick) {
    if (!highlight) return;
    const ring = (x, y, radius, color) => {
        const [sx, sy] = toScreen(x, y);
        context.beginPath();
        context.arc(sx, sy, radius, 0, Math.PI * 2);
        context.strokeStyle = color;
        context.stroke();
    };
    const width = Math.max(2, canvas.width / 420);
    const radius = Math.max(10, canvas.width / 55);
    context.save();
    context.lineWidth = width;
    if (highlight.grenade) {
        const { points, at, k } = highlight.grenade;
        context.strokeStyle = "#ffffff";
        context.setLineDash([width * 2.5, width * 2]);
        context.beginPath();
        for (const [index, point] of points.entries()) {
            const [px, py] = toScreen(point[1], point[2]);
            if (index) context.lineTo(px, py);
            else context.moveTo(px, py);
        }
        context.stroke();
        context.setLineDash([]);
        ring(points[0][1], points[0][2], radius * 0.55, "#ffffff");
        ring(at[0], at[1], Math.max(radius, unitsToPixels(k === "smoke" ? SMOKE_RADIUS : 90)), KIND_COLORS[k]);
    } else if (highlight.kill) {
        const { kill } = highlight;
        const victim = round.byPlayer.get(kill.v);
        const position = kill.vp ?? interpolatePosition(victim?.track ?? [], kill.t)?.slice(1);
        if (kill.kp && kill.vp) {
            const [kx, ky] = toScreen(kill.kp[0], kill.kp[1]);
            const [vx, vy] = toScreen(kill.vp[0], kill.vp[1]);
            context.strokeStyle = "#ffffff";
            context.setLineDash([width * 2.5, width * 2]);
            context.beginPath();
            context.moveTo(kx, ky);
            context.lineTo(vx, vy);
            context.stroke();
            context.setLineDash([]);
            ring(kill.kp[0], kill.kp[1], radius * 0.7, SIDE_COLORS[round.byPlayer.get(kill.k)?.side] ?? "#ffffff");
        }
        if (position) ring(position[0], position[1], radius, "#ffffff");
    } else if (highlight.bomb && round.bomb?.plant) {
        ring(round.bomb.plant.at[0], round.bomb.plant.at[1], radius, "#ff4b3e");
    }
    context.restore();
}

function refresh() {
    draw();
    updateClock();
    updateScoreboard();
    updateFeed();
    updateMarkers();
}

/* ---------- Navigation and playback ---------- */

function seek(tick) {
    time = Math.max(0, Math.min(round.duration, tick));
    refresh();
}

function stopPlayback() {
    if (playbackFrame !== undefined) cancelAnimationFrame(playbackFrame);
    playbackFrame = undefined;
    playButton.textContent = "▶";
    playButton.setAttribute("aria-label", "Lire");
}

function startPlayback() {
    if (!round) return;
    if (time >= round.duration) time = 0;
    playButton.textContent = "❚❚";
    playButton.setAttribute("aria-label", "Pause");
    lastFrameTime = performance.now();
    const advance = (now) => {
        const elapsed = Math.min(0.25, (now - lastFrameTime) / 1000);
        lastFrameTime = now;
        time = Math.min(round.duration, time + elapsed * replay.tickRate * Number(speedSelect.value));
        refresh();
        if (time < round.duration) playbackFrame = requestAnimationFrame(advance);
        else {
            stopPlayback();
            syncAddress(true);
        }
    };
    playbackFrame = requestAnimationFrame(advance);
}

function togglePlayback() {
    if (playbackFrame !== undefined) {
        stopPlayback();
        syncAddress(true);
    } else startPlayback();
}

function roundTitle(stage) {
    const parts = [`Round ${stage.number}`];
    if (stage.winner && stage.teams) parts.push(`${stage.teams[stage.winner]} (${stage.winner.toUpperCase()})`);
    if (END_REASONS[stage.reason]) parts.push(END_REASONS[stage.reason]);
    if (stage.economy) parts.push(`T ${ECONOMY_LABELS[stage.economy.t] ?? "?"} · CT ${ECONOMY_LABELS[stage.economy.ct] ?? "?"}`);
    return parts.join(" · ");
}

function sideOfReference(stage, reference) {
    return stage.teams ? stage.teams.t : stage.sides[reference];
}

function renderRounds() {
    const nav = byId("replay-rounds");
    const reference = Object.keys(rounds[0]?.sides ?? {})[0];
    const items = [];
    rounds.forEach((stage, index) => {
        if (index && sideOfReference(stage, reference) !== sideOfReference(rounds[index - 1], reference)) {
            const gap = document.createElement("span");
            gap.className = "replay-round-gap";
            gap.setAttribute("aria-hidden", "true");
            items.push(gap);
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = "replay-round";
        button.textContent = stage.number;
        button.dataset.round = stage.number;
        if (stage.winner) button.dataset.winner = stage.winner;
        button.title = roundTitle(stage);
        button.setAttribute("aria-label", roundTitle(stage));
        button.addEventListener("click", () => {
            selectRound(index, 0);
            syncAddress();
        });
        items.push(button);
    });
    nav.replaceChildren(...items);
}

function selectRound(index, tick = 0) {
    stopPlayback();
    round = prepareRound(rounds[index], index);
    highlight = undefined;
    byId("replay-tooltip").hidden = true;
    for (const button of document.querySelectorAll(".replay-round")) {
        button.setAttribute("aria-current", String(Number(button.dataset.round) === round.number));
    }
    if (selectedPlayer && !round.byPlayer.has(selectedPlayer)) selectedPlayer = undefined;
    timeControl.max = round.duration;
    renderScoreboard();
    renderFeed();
    renderMarkers();
    seek(tick);
}

function showStatus(message = "") {
    const status = byId("replay-status");
    status.textContent = message;
    status.hidden = !message;
}

function loadImages(radar) {
    images = {};
    for (const level of ["upper", "lower"]) {
        if (!radar?.[level]) continue;
        const image = new Image();
        image.addEventListener("load", draw);
        image.src = radar[level];
        images[level] = image;
    }
}

function setZoom(zoom, anchor = [0.5, 0.5]) {
    const next = Math.max(1, Math.min(MAX_ZOOM, zoom));
    // Keep the point under the cursor fixed while zooming.
    const worldX = view.x + (anchor[0] - 0.5) / view.zoom;
    const worldY = view.y + (anchor[1] - 0.5) / view.zoom;
    view.zoom = next;
    view.x = worldX - (anchor[0] - 0.5) / next;
    view.y = worldY - (anchor[1] - 0.5) / next;
    clampView();
    byId("replay-zoom-value").textContent = `${Math.round(next * 100)} %`;
    byId("replay-zoom-out").disabled = next <= 1;
    byId("replay-zoom-in").disabled = next >= MAX_ZOOM;
    draw();
}

function clampView() {
    const half = 0.5 / view.zoom;
    view.x = Math.max(half, Math.min(1 - half, view.x));
    view.y = Math.max(half, Math.min(1 - half, view.y));
}

function showLegacyNotice() {
    const notice = byId("replay-legacy");
    notice.hidden = replay.version >= 4 || (replay.version === 3 && !replay.demoSaved);
    if (notice.hidden) return;
    const text = document.createElement("span");
    if (replay.version === 3) {
        text.textContent = "Replay créé avec une version précédente : la forme des molotovs est approximative.";
    } else {
        text.textContent = replay.demoSaved
            ? "Relevé simplifié : positions et utilitaires approximatifs, sans kills ni bombe."
            : "Relevé simplifié : positions et utilitaires approximatifs, sans kills ni bombe. Réimporte la démo avec « Replay uniquement » pour obtenir le relevé complet.";
    }
    notice.replaceChildren(text);
    if (!replay.demoSaved) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zone-button";
    button.textContent = "Mettre à jour le replay";
    button.addEventListener("click", () => updateOpenReplay(button, text));
    notice.append(button);
}

async function updateOpenReplay(button, text) {
    const { id } = replay;
    const position = { round: round?.number, seconds: Math.floor(time / replay.tickRate) };
    button.disabled = true;
    text.textContent = "Mise à jour en cours… L’analyse de la démo prend quelques dizaines de secondes.";
    try {
        await fetchJson("/api/replays/reanalyze", { id });
        let busy = true;
        while (busy) {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            ({ busy } = await fetchJson("/api/replays"));
        }
    } catch (error) {
        text.textContent = error.message;
        button.disabled = false;
        return;
    }
    if (replay?.id !== id) return;
    replay = undefined;
    openReplay(id, position.round, position.seconds, true);
}

async function openReplay(id, roundNumber, seconds, replace = false) {
    const token = ++loadToken;
    stopPlayback();
    listView.hidden = true;
    viewer.hidden = false;
    if (replay?.id !== id) {
        replay = undefined;
        round = undefined;
        rounds = [];
        byId("replay-title").textContent = "Chargement…";
        byId("replay-subtitle").textContent = "";
        byId("replay-rounds").replaceChildren();
        byId("replay-feed").replaceChildren();
        byId("replay-team-ct").replaceChildren();
        byId("replay-team-t").replaceChildren();
        resizeCanvas();
        context.clearRect(0, 0, canvas.width, canvas.height);
        showStatus("Chargement du replay…");
        let data;
        try {
            data = await fetchJson(`/api/replay?${new URLSearchParams({ id })}`);
        } catch (error) {
            if (token === loadToken) showStatus(error.message);
            return;
        }
        if (token !== loadToken) return;
        replay = data;
        if (!Array.isArray(replay.zones)) {
            // Older servers did not send the callout zones with the replay.
            try {
                const { maps } = await fetchJson("/api/callout-maps");
                replay.zones = maps.find((map) => map.map === replay.map)?.zones ?? [];
            } catch {
                replay.zones = [];
            }
            if (token !== loadToken) return;
        }
        rounds = replay.rounds.filter((stage) => stage.sides && Object.keys(stage.sides).length);
        if (!replay.radar) {
            showStatus("Aucun radar disponible pour cette carte.");
            return;
        }
        if (!rounds.length) {
            showStatus("Aucun round exploitable dans ce relevé.");
            return;
        }
        showStatus();
        loadImages(replay.radar);
        byId("replay-floor-control").hidden = !replay.radar.lower;
        floorSelect.value = "upper";
        view = { zoom: 1, x: 0.5, y: 0.5 };
        setZoom(1);
        const title = byId("replay-title");
        const teams = replay.teams ?? [];
        title.textContent = teams.length ? matchLabel(replay) : replay.demo;
        byId("replay-subtitle").textContent = [mapLabel(replay.map), replay.demo,
            replay.date ? new Date(replay.date).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }) : ""]
            .filter(Boolean).join(" · ");
        showLegacyNotice();
        renderRounds();
    }
    const index = Math.max(0, rounds.findIndex((stage) => stage.number === Number(roundNumber)));
    selectRound(index, (Number(seconds) || 0) * replay.tickRate);
    syncAddress(replace);
    radarElement.focus({ preventScroll: true });
}

function showList(replace = false) {
    stopPlayback();
    loadToken++;
    replay = undefined;
    round = undefined;
    viewer.hidden = true;
    listView.hidden = false;
    syncAddress(replace);
    loadList();
}

function restoreReplayAddress() {
    const params = currentParams();
    const id = params.get("id");
    if (id) openReplay(id, params.get("round"), params.get("t"), true);
    else showList(true);
}

/* ---------- Events ---------- */

byId("replay-search").addEventListener("input", renderList);
byId("replay-import").addEventListener("click", () => {
    if (navigation?.openImport) navigation.openImport("replay");
    else window.top.location.href = "/?view=export&mode=replay";
});
byId("replay-back").addEventListener("click", () => showList());
playButton.addEventListener("click", togglePlayback);
timeControl.addEventListener("input", () => {
    stopPlayback();
    seek(timeControl.valueAsNumber);
});
timeControl.addEventListener("change", () => syncAddress(true));
speedSelect.addEventListener("change", () => {
    lastFrameTime = performance.now();
});
for (const toggle of [namesToggle, trailsToggle]) toggle.addEventListener("change", draw);
floorSelect.addEventListener("change", draw);
byId("replay-zoom-in").addEventListener("click", () => setZoom(view.zoom * ZOOM_STEP));
byId("replay-zoom-out").addEventListener("click", () => setZoom(view.zoom / ZOOM_STEP));

radarElement.addEventListener("wheel", (event) => {
    if (!replay?.radar) return;
    event.preventDefault();
    const bounds = radarElement.getBoundingClientRect();
    const anchor = [(event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height];
    setZoom(view.zoom * (event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP), anchor);
}, { passive: false });
radarElement.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".replay-zoom") || view.zoom <= 1) return;
    pan = { id: event.pointerId, x: event.clientX, y: event.clientY, view: { ...view } };
    radarElement.setPointerCapture(event.pointerId);
    radarElement.classList.add("panning");
});
radarElement.addEventListener("pointermove", (event) => {
    if (!pan || pan.id !== event.pointerId) return;
    const size = radarElement.clientWidth;
    view.x = pan.view.x - (event.clientX - pan.x) / size / view.zoom;
    view.y = pan.view.y - (event.clientY - pan.y) / size / view.zoom;
    clampView();
    draw();
});
for (const type of ["pointerup", "pointercancel"]) {
    radarElement.addEventListener(type, (event) => {
        if (pan?.id !== event.pointerId) return;
        pan = undefined;
        radarElement.classList.remove("panning");
    });
}

document.addEventListener("keydown", (event) => {
    if (viewer.hidden || !round || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.target;
    if (target.closest?.("select, input[type='search'], dialog")) return;
    const onControl = target.closest?.("button, [role='button']");
    if (event.key === " " && !onControl) {
        event.preventDefault();
        togglePlayback();
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        stopPlayback();
        seek(time + (event.key === "ArrowLeft" ? -1 : 1) * SEEK_SECONDS * replay.tickRate);
        syncAddress(true);
    } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        const index = rounds.indexOf(round) + (event.key === "ArrowUp" ? -1 : 1);
        if (index >= 0 && index < rounds.length) {
            selectRound(index, 0);
            syncAddress();
        }
    }
});
new ResizeObserver(draw).observe(radarElement);
new ResizeObserver(() => {
    if (round) layoutMarkers();
}).observe(byId("replay-utility-markers"));

window.restoreReplayAddress = restoreReplayAddress;
if (!navigation) window.addEventListener("popstate", restoreReplayAddress);
restoreReplayAddress();
