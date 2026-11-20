"use strict";

const PLAYER_HEATMAP_WINDOW_SECONDS = 0.5;
const PLAYER_HEATMAP_RADIUS = 7;
const byId = (id) => document.getElementById(id);
const mapSelect = byId("player-map");
const roundSelect = byId("player-round");
const floorSelect = byId("player-floor");
const timeControl = byId("player-time");
const playButton = byId("player-play");
const radarImage = byId("player-radar-image");
const canvas = byId("player-heatmap");
const context = canvas.getContext("2d");
const mapStatus = byId("player-map-status");
const coverage = byId("player-coverage");
const navigation = window.parent !== window ? window.parent.utilityNavigation : undefined;
const accents = getComputedStyle(document.documentElement);
const colors = ["t", "ct"].map((side) =>
    accents.getPropertyValue(`--side-${side}`).trim().slice(1).match(/../g).map((value) => parseInt(value, 16)));

const kernel = [];
for (let y = -PLAYER_HEATMAP_RADIUS; y <= PLAYER_HEATMAP_RADIUS; y++) {
    for (let x = -PLAYER_HEATMAP_RADIUS; x <= PLAYER_HEATMAP_RADIUS; x++) {
        const distance = (x * x + y * y) / PLAYER_HEATMAP_RADIUS ** 2;
        if (distance < 1) kernel.push([x, y, (1 - distance) ** 2]);
    }
}

let maps = [];
let selectedMap;
let heatmap;
let dataKey;
let activeRequest;
let playbackFrame;
let densities;
let image;

function mapLabel(name) {
    const short = name.replace(/^(de|cs|ar)_/, "").replaceAll("_", " ");
    return short.charAt(0).toUpperCase() + short.slice(1);
}

function timeLabel(deciseconds, fractional = false) {
    const minutes = Math.floor(deciseconds / 600);
    const seconds = String(Math.floor(deciseconds / 10) % 60).padStart(2, "0");
    return `${minutes}:${seconds}${fractional ? `,${deciseconds % 10}` : ""}`;
}

function syncAddress(replace = false) {
    const params = new URLSearchParams({ map: selectedMap.map });
    if (roundSelect.value) params.set("round", roundSelect.value);
    if (floorSelect.value === "lower") params.set("floor", "lower");
    if (timeControl.valueAsNumber) params.set("time", timeControl.value);
    if (navigation) navigation.update(params.toString(), replace);
    else if (`?${params}` !== location.search) {
        if (replace) history.replaceState(null, "", `?${params}`);
        else history.pushState(null, "", `?${params}`);
    }
}

function showMapStatus(message = "") {
    if (mapStatus.textContent !== message) mapStatus.textContent = message;
    mapStatus.hidden = !message;
}

function drawHeatmap() {
    const time = timeControl.valueAsNumber;
    const remaining = Number(timeControl.max) - time;
    const label = timeLabel(remaining, true);
    byId("player-remaining").textContent = label;
    timeControl.setAttribute("aria-valuetext", label);
    if (!heatmap) return;
    const { size, samplesPerSecond } = heatmap;
    const start = Math.max(0, Math.floor((time - PLAYER_HEATMAP_WINDOW_SECONDS * 10) * samplesPerSecond / 10) + 1);
    const end = Math.floor(time * samplesPerSecond / 10);
    const floor = heatmap.floors[floorSelect.value];
    for (const [index, side] of ["t", "ct"].entries()) {
        const density = densities[index];
        density.fill(0);
        for (let sample = start; sample <= end; sample++) {
            for (const pixel of floor[side][sample]) {
                const x = pixel % size;
                const y = Math.floor(pixel / size);
                for (const [dx, dy, weight] of kernel) {
                    const px = x + dx;
                    const py = y + dy;
                    if (px >= 0 && py >= 0 && px < size && py < size) density[py * size + px] += weight;
                }
            }
        }
    }

    const [t, ct] = densities;
    let peak = 0;
    for (let i = 0; i < t.length; i++) peak = Math.max(peak, t[i] + ct[i]);
    if (!peak) {
        context.clearRect(0, 0, size, size);
        showMapStatus("Aucune position à cet instant.");
        return;
    }

    const pixels = image.data;
    pixels.fill(0);
    const [orange, blue] = colors;
    for (let i = 0; i < t.length; i++) {
        const heat = t[i] + ct[i];
        if (!heat) continue;
        const share = t[i] / heat;
        const pixel = i * 4;
        for (let channel = 0; channel < 3; channel++) {
            pixels[pixel + channel] = blue[channel] + (orange[channel] - blue[channel]) * share;
        }
        pixels[pixel + 3] = 240 * Math.sqrt(heat / peak);
    }
    context.putImageData(image, 0, 0);
    showMapStatus();
}

function stopPlayback() {
    if (playbackFrame !== undefined) cancelAnimationFrame(playbackFrame);
    playbackFrame = undefined;
    playButton.textContent = "Lire";
}

function showRadar() {
    const path = selectedMap.radar[floorSelect.value];
    radarImage.alt = `Radar de ${mapLabel(selectedMap.map)}`;
    if (radarImage.getAttribute("src") !== path) radarImage.src = path;
    drawHeatmap();
}

async function fetchData(path, signal) {
    const response = await fetch(path, { signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "Chargement impossible.");
    return data;
}

async function loadPositions() {
    stopPlayback();
    const query = new URLSearchParams({ map: selectedMap.map, round: roundSelect.value });
    const key = query.toString();
    if (heatmap && dataKey === key) {
        showRadar();
        return;
    }
    activeRequest?.abort();
    const request = new AbortController();
    activeRequest = request;
    heatmap = undefined;
    context.clearRect(0, 0, canvas.width, canvas.height);
    coverage.textContent = "";
    timeControl.disabled = playButton.disabled = true;
    showMapStatus("Chargement des positions…");
    showRadar();
    try {
        const result = await fetchData(`/api/player-heatmap/positions?${key}`, request.signal);
        if (request.signal.aborted) return;
        dataKey = key;
        heatmap = result;
        canvas.width = canvas.height = result.size;
        densities = [new Float32Array(result.size ** 2), new Float32Array(result.size ** 2)];
        image = context.createImageData(result.size, result.size);
        timeControl.max = result.duration * 10;
        byId("player-duration").textContent = timeLabel(result.duration * 10);
        timeControl.disabled = playButton.disabled = !result.rounds;
        coverage.textContent = `${result.rounds} ${result.rounds === 1 ? "round" : "rounds"} · ${result.demos} ${result.demos === 1 ? "démo" : "démos"}`;
        drawHeatmap();
    } catch (error) {
        if (!request.signal.aborted) showMapStatus(error.message);
    } finally {
        if (activeRequest === request) activeRequest = undefined;
    }
}

function selectMap(map, round, floor, time) {
    selectedMap = map;
    mapSelect.value = map.map;
    roundSelect.replaceChildren(new Option("Rounds 1 à 12", ""),
        ...map.rounds.map((number) => new Option(`Round ${number}`, number)));
    roundSelect.value = map.rounds.includes(Number(round)) ? round : "";
    byId("player-floor-control").hidden = !map.radar.lower;
    floorSelect.value = map.radar.lower && floor === "lower" ? "lower" : "upper";
    timeControl.value = Number.isInteger(time) ? time : 0;
    loadPositions();
}

function restorePlayerAddress() {
    if (!maps.length) return;
    const params = new URLSearchParams(navigation ? navigation.search() : location.search);
    const map = maps.find(({ map }) => map === params.get("map")) ?? maps[0];
    selectMap(map, params.get("round") ?? "", params.get("floor"), Number(params.get("time")));
    syncAddress(true);
}

mapSelect.addEventListener("change", () => {
    selectMap(maps.find(({ map }) => map === mapSelect.value), "", "upper", 0);
    syncAddress();
});
roundSelect.addEventListener("change", () => {
    loadPositions();
    syncAddress();
});
floorSelect.addEventListener("change", () => {
    showRadar();
    syncAddress();
});
timeControl.addEventListener("input", () => {
    stopPlayback();
    drawHeatmap();
});
timeControl.addEventListener("change", () => syncAddress(true));
playButton.addEventListener("click", () => {
    if (playbackFrame !== undefined) {
        stopPlayback();
        syncAddress(true);
        return;
    }
    const limit = Number(timeControl.max);
    if (timeControl.valueAsNumber >= limit) timeControl.value = 0;
    const origin = performance.now() - timeControl.valueAsNumber * 100;
    playButton.textContent = "Pause";
    function advance(now) {
        const time = Math.min(limit, Math.floor((now - origin) / 100));
        if (time !== timeControl.valueAsNumber) {
            timeControl.value = time;
            drawHeatmap();
        }
        if (time < limit) playbackFrame = requestAnimationFrame(advance);
        else {
            stopPlayback();
            syncAddress(true);
        }
    }
    playbackFrame = requestAnimationFrame(advance);
});

window.restorePlayerAddress = restorePlayerAddress;
if (!navigation) window.addEventListener("popstate", restorePlayerAddress);
fetchData("/api/player-heatmap/maps").then((result) => {
    maps = result.maps;
    if (!maps.length) {
        byId("players-empty").textContent = "Aucune démo avec des positions et un radar disponibles.";
        return;
    }
    mapSelect.replaceChildren(...maps.map(({ map }) => new Option(mapLabel(map), map)));
    byId("players-empty").hidden = true;
    byId("player-workspace").hidden = false;
    restorePlayerAddress();
}).catch((error) => {
    byId("players-empty").textContent = error.message;
});
