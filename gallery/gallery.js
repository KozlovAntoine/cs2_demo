"use strict";

const catalogue = window.utilityCatalogue;
const RADAR_CLUSTER_RADIUS_PX = 48;
const CLIPS_PER_PAGE = 36;
const library = window.utilityLibrary === true;
const portrait = library && window.utilityPortrait === true;
const clipStateEnabled = library && window.utilityClipState === true;
const kinds = {
    smoke: { label: "Smoke", color: "#8dd3f3" },
    flash: { label: "Flash", color: "#f7df88" },
    molo: { label: "Molotov", color: "#fa9c77" },
    he: { label: "HE", color: "#ed91af" },
};
const mouseModes = { left: "Clic gauche", middle: "Clic milieu · gauche + droite", right: "Clic droit" };
const RARE_BELOW = 2;
const COMMON_MAX = 10;
const frequencyLabels = { rare: "Rare", common: "Commun", frequent: "Très fréquent" };
const frequencyFormat = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
const HEATMAP_COLORS = [[40, 91, 241], [36, 190, 230], [251, 212, 65], [250, 122, 46], [225, 42, 49]];
const HEATMAP_TIMER_SPEED = 5;
const HEATMAP_TIMER_WINDOW_SECONDS = 3;

const byId = (id) => document.getElementById(id);
const matchSelect = byId("match");
const roundSelect = byId("round");
const clipSearch = byId("clip-search");
const favoritesFilter = byId("favorites");
const unwatchedFilter = byId("unwatched");
const potentialFilter = byId("potential-favorites");
const statusFilters = new Map([["favorites", favoritesFilter], ["unwatched", unwatchedFilter], ["potential", potentialFilter]]);
const frequencySelect = byId("frequency");
const sideSelects = [byId("clip-side"), byId("radar-side")];
const pointSelect = byId("point");
const levelSelect = byId("level");
const video = byId("video");
const skipIntroButton = byId("skip-intro");
let skipIntro = false;
try {
    skipIntro = localStorage.getItem("utilitySkipIntro") === "true";
} catch {
    // The setting remains available for this page when storage is unavailable.
}
skipIntroButton.setAttribute("aria-pressed", String(skipIntro));
skipIntroButton.addEventListener("click", () => {
    skipIntro = !skipIntro;
    skipIntroButton.setAttribute("aria-pressed", String(skipIntro));
    try {
        localStorage.setItem("utilitySkipIntro", String(skipIntro));
    } catch {
        // Keep the setting for the current page.
    }
});
const detail = byId("video-empty");
const radarImage = byId("radar-image");
const markers = byId("points");
const radarRoutes = byId("radar-routes");
const heatmapCanvas = byId("radar-heatmap");
const heatmapToggle = byId("heatmap-toggle");
const heatmapModeSelect = byId("heatmap-mode");
const heatmapTimeControl = byId("heatmap-time");
const heatmapPlay = byId("heatmap-play");
const roundDuration = Number(heatmapTimeControl.max);
const radarView = createRadarViewport(byId("radar"), byId("radar-content"), refreshRadarGrouping);
const list = byId("clips");
const clipPages = byId("clip-pages");
const previousPage = byId("clip-prev-page");
const nextPage = byId("clip-next-page");
const deleteButton = byId("delete-clip");
const favoriteButton = byId("favorite-clip");
const dismissPotentialButton = byId("dismiss-potential");
const editClipCallout = byId("edit-clip-callout");
const clipCalloutDialog = byId("clip-callout-dialog");
const clipCalloutForm = byId("clip-callout-form");
const clipCalloutInput = byId("clip-callout-name");
const deleteDialog = byId("confirm-delete");
const deleteToast = byId("delete-toast");
const clipActions = byId("clip-actions");
const portraitButton = byId("portrait-action");
const throwControls = byId("throw-controls");
const throwKeys = byId("throw-keys");
const throwCommand = byId("throw-command");
const copyStatus = byId("copy-status");
let currentMatch;
let filter = "all";
let activeStatusFilters = new Set();
let clipSort = "";
let selectedSide = "";
let selectedTeam = "";
let selectedPlayers = new Set();
const teamOptions = new Map();
const playerOptions = new Map();
const clipThrowers = new WeakMap();
let heatmapMode = "";
let heatmapTime = 0;
let heatmapFrame;
let heatmapTimeline;
const reviewPending = new Set();
let filtered = [];
let visible = [];
let clipPage = 0;
let radarSelection;
let radarGroupingCache;
let selected;
let selectionHistory = [];
let playbackHistory = [];
let playbackIndex = -1;
let playbackScope;
let clipToDelete;
let deleteToastTimer;
let portraitTimer;
let portraitState;
let portraitPending = false;
let portraitError = "";

const navigation = library && window.parent !== window ? window.parent.utilityNavigation : undefined;

function createRadarViewport(viewport, content, onZoom) {
    if (!content) return;
    const minZoom = 100;
    const maxZoom = 500;
    const step = 10;
    const controls = viewport.querySelector(".radar-zoom");
    const zoomOut = controls.querySelector('[data-zoom="out"]');
    const zoomIn = controls.querySelector('[data-zoom="in"]');
    const output = controls.querySelector("output");
    let zoom = minZoom;
    let x = 0;
    let y = 0;
    let available = true;
    let wheelDelta = 0;
    let pan;
    let suppressClick = false;

    function render() {
        const scale = zoom / 100;
        x = Math.min(0, Math.max(1 - scale, x));
        y = Math.min(0, Math.max(1 - scale, y));
        content.style.transform = `translate(${x * 100}%, ${y * 100}%) scale(${scale})`;
        content.style.setProperty("--radar-inverse", 1 / scale);
        viewport.classList.toggle("can-pan", available && zoom > minZoom);
        controls.hidden = !available;
        zoomOut.disabled = zoom === minZoom;
        zoomIn.disabled = zoom === maxZoom;
        output.value = `${zoom} %`;
    }

    function zoomBy(direction, focusX = 0.5, focusY = 0.5) {
        if (!available || pan) return;
        const next = Math.max(minZoom, Math.min(maxZoom, zoom + direction * step));
        if (next === zoom) return;
        const ratio = next / zoom;
        x = focusX - (focusX - x) * ratio;
        y = focusY - (focusY - y) * ratio;
        zoom = next;
        render();
        onZoom?.();
    }

    zoomOut.addEventListener("click", () => zoomBy(-1));
    zoomIn.addEventListener("click", () => zoomBy(1));
    viewport.addEventListener("wheel", (event) => {
        if (!available || !event.deltaY || event.ctrlKey || event.target.closest(".radar-zoom")) return;
        event.preventDefault();
        if (pan || content.querySelector(".dragging")) return;
        const bounds = viewport.getBoundingClientRect();
        const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? bounds.height : 1);
        if (Math.sign(delta) !== Math.sign(wheelDelta)) wheelDelta = 0;
        wheelDelta += delta;
        if (Math.abs(wheelDelta) < 40) return;
        zoomBy(-Math.sign(wheelDelta), (event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height);
        wheelDelta = 0;
    });

    viewport.addEventListener("pointerdown", (event) => {
        if (event.button !== 0 || !event.isPrimary) return;
        suppressClick = false;
        if (!available || zoom === minZoom || event.target.closest(".marker, .radar-link, .callout-vertex, .radar-zoom")) return;
        const { width, height } = viewport.getBoundingClientRect();
        pan = { id: event.pointerId, clientX: event.clientX, clientY: event.clientY, x, y, width, height };
    });
    viewport.addEventListener("pointermove", (event) => {
        if (pan?.id !== event.pointerId) return;
        const dx = event.clientX - pan.clientX;
        const dy = event.clientY - pan.clientY;
        if (!pan.moved) {
            if (Math.hypot(dx, dy) < 4) return;
            pan.moved = true;
            viewport.setPointerCapture(event.pointerId);
            viewport.classList.add("panning");
        }
        x = pan.x + dx / pan.width;
        y = pan.y + dy / pan.height;
        render();
    });
    function stopPan(event) {
        if (pan?.id !== event.pointerId) return;
        suppressClick = Boolean(pan.moved);
        pan = undefined;
        viewport.classList.remove("panning");
    }
    for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) viewport.addEventListener(event, stopPan);
    viewport.addEventListener("pointerleave", (event) => {
        if (pan && !pan.moved) stopPan(event);
        wheelDelta = 0;
    });
    viewport.addEventListener("click", (event) => {
        if (!suppressClick) return;
        suppressClick = false;
        event.preventDefault();
        event.stopImmediatePropagation();
    }, true);

    render();
    return {
        get zoom() { return zoom; },
        reset() {
            if (pan && viewport.hasPointerCapture(pan.id)) viewport.releasePointerCapture(pan.id);
            pan = undefined;
            suppressClick = false;
            viewport.classList.remove("panning");
            x = y = wheelDelta = 0;
            zoom = minZoom;
            render();
        },
        setAvailable(value) {
            available = value;
            render();
        },
    };
}

function clipId(clip) {
    return clip?.exportId ? `${clip.exportId}/${clip.file}` : undefined;
}

function syncAddress(replace = false) {
    if (!library) return;
    const params = new URLSearchParams();
    if (currentMatch) params.set("map", currentMatch.map);
    if (filter !== "all") params.set("type", filter);
    if (clipSearch?.value.trim()) params.set("q", clipSearch.value.trim());
    if (roundSelect.value) params.set("round", roundSelect.value);
    if (clipSort) params.set("sort", clipSort);
    for (const status of statusFilters.keys()) {
        if (activeStatusFilters.has(status)) params.append("status", status);
    }
    if (frequencySelect?.value) params.set("frequency", frequencySelect.value);
    if (selectedSide) params.set("side", selectedSide);
    if (selectedTeam) params.set("team", selectedTeam);
    for (const player of selectedPlayers) params.append("player", player);
    if (pointSelect.value !== "impact") params.set("points", pointSelect.value);
    if (levelSelect.value === "lower") params.set("floor", "lower");
    if (heatmapMode) params.set("heatmap", heatmapMode === "timer" ? "timer" : "1");
    if (heatmapMode === "timer" && heatmapTime) params.set("time", heatmapTime);
    const group = clipId(radarSelection?.[0]);
    if (group) params.set("group", group);
    const clip = clipId(selected);
    if (clip) params.set("clip", clip);
    const search = params.toString();
    if (navigation) navigation.update(search, replace);
    else if (search !== location.search.slice(1)) {
        const url = search ? `?${search}` : location.pathname;
        if (replace) history.replaceState(null, "", url);
        else history.pushState(null, "", url);
    }
}

function showStatusFilter() {
    for (const [status, button] of statusFilters) {
        button.setAttribute("aria-pressed", String(activeStatusFilters.has(status)));
    }
}

function throwFrequency(clip) {
    const { total, demos } = clip.usage ?? {};
    if (!(total > 0 && demos > 0)) return;
    const average = total / demos;
    const category = average < RARE_BELOW ? "rare" : average <= COMMON_MAX ? "common" : "frequent";
    return { average, category, total, demos };
}

function frequencyBadge(clip, showAverage = false) {
    const frequency = throwFrequency(clip);
    if (!frequency) return;
    const { average, category, total, demos } = frequency;
    const mean = frequencyFormat.format(average);
    const badge = document.createElement("span");
    badge.className = `frequency-badge frequency-${category}`;
    badge.textContent = showAverage ? `${frequencyLabels[category]} · ${mean}/partie` : frequencyLabels[category];
    badge.title = `${mean} en moyenne · ${total} ${total === 1 ? "lancer" : "lancers"} en ${demos} ${demos === 1 ? "partie" : "parties"}`;
    return badge;
}

function addOption(select, value, label) {
    select.add(new Option(label, value));
}

function dot() {
    const element = document.createElement("span");
    element.className = "kind-dot";
    element.setAttribute("aria-hidden", "true");
    return element;
}

function mapLabel(name) {
    const title = name.replace(/^(de|cs|ar)_/, "").replaceAll("_", " ");
    return title.charAt(0).toUpperCase() + title.slice(1);
}

function clipReference(clip) {
    return { exportId: clip.exportId, file: clip.file };
}

async function request(path, payload) {
    const options = payload === undefined ? undefined : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    };
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error ?? "La requête a échoué.");
    return result;
}

function showPortrait(state, message = "") {
    portraitState = state;
    portraitButton.textContent = state?.exists ? "Ouvrir le dossier" : "Générer vidéo 9:16";
    portraitButton.disabled = !state || (!state.exists && state.busy) || portraitPending;
    const status = byId("portrait-status");
    const text = message || portraitError || state?.error || (state?.running ? state.phase : "") || "";
    if (status.textContent !== text) status.textContent = text;
}

async function refreshPortrait(clip) {
    clearTimeout(portraitTimer);
    if (!portrait || !clip?.exportId || selected !== clip || portraitPending) return;
    try {
        const status = await request(`/api/portrait?${new URLSearchParams(clipReference(clip))}`);
        if (selected === clip && !portraitPending) showPortrait(status);
    } catch (error) {
        if (selected === clip && !portraitPending) showPortrait(undefined, error.message);
    } finally {
        if (selected === clip && !portraitPending) {
            clearTimeout(portraitTimer);
            portraitTimer = setTimeout(() => refreshPortrait(clip), 1500);
        }
    }
}

function showLineup(clip) {
    const lineup = clip?.lineup;
    const controls = lineup?.controls;
    const badges = [];
    if (controls?.mouse && Object.hasOwn(mouseModes, controls.mouse)) {
        const mouse = document.createElement("kbd");
        mouse.className = "throw-key";
        mouse.title = mouseModes[controls.mouse];
        mouse.setAttribute("aria-label", mouse.title);
        const icon = document.createElement("span");
        icon.className = `mouse-icon mouse-${controls.mouse}`;
        icon.setAttribute("aria-hidden", "true");
        mouse.append(icon);
        badges.push(mouse);
    }
    const keys = [
        ...(controls?.directions ?? []).filter((key) => ["W", "Q", "S", "D"].includes(key)),
        controls?.walk && "SHIFT", controls?.crouch && "CROUCH", controls?.jump && "JUMP",
    ];
    for (const key of keys.filter(Boolean)) {
        const badge = document.createElement("kbd");
        badge.className = "throw-key";
        badge.textContent = key;
        badges.push(badge);
    }
    if (!badges.length) {
        const text = document.createElement("span");
        text.textContent = "Commandes indisponibles";
        badges.push(text);
    }
    throwKeys.replaceChildren(...badges);
    throwControls.hidden = !clip?.video;

    const { position, angles } = lineup ?? {};
    const hasCommand = position?.length === 3 && angles?.length === 2 && [...position, ...angles].every(Number.isFinite);
    throwCommand.textContent = hasCommand ? `setpos ${position.join(" ")};\nsetang ${angles.join(" ")} 0;` : "";
    byId("throw-console").hidden = !clip?.video;
    byId("console-command").hidden = !hasCommand;
    copyStatus.textContent = hasCommand ? "" : "Position et angle indisponibles pour ce clip.";
}

function renderClipTitle(clip) {
    const title = byId("clip-title");
    if (!clip) {
        title.textContent = "Clip";
        return;
    }
    const location = library ? calloutName(clip, currentMatch) : clip.player;
    const score = isPotential(clip) ? ` · ${potentialPoints(clip)}` : "";
    title.textContent = `${kinds[clip.kind].label} · ${location}${score}`;
}

function selectClip(clip, autoplay = false) {
    const changed = selected !== clip;
    const scope = JSON.stringify([currentMatch?.map, filter, [...activeStatusFilters], roundSelect.value, clipSortMode(),
        frequencySelect?.value, selectedSide, selectedTeam, [...selectedPlayers]]);
    if (scope !== playbackScope) {
        playbackScope = scope;
        playbackHistory = [];
        playbackIndex = -1;
    }
    if (clip && playbackHistory[playbackIndex] !== clip) {
        playbackHistory.splice(playbackIndex + 1);
        playbackHistory.push(clip);
        playbackIndex = playbackHistory.length - 1;
    }
    if (selected !== clip || !video.getAttribute("src")) {
        video.pause();
        video.removeAttribute("src");
        video.hidden = !clip?.video;
        detail.hidden = Boolean(clip?.video);
        if (clip?.video) {
            video.src = clip.video;
            if (skipIntro) video.currentTime = 2;
        }
        renderClipTitle(clip);
        if (!clip) {
            detail.textContent = "Aucun clip pour ce filtre.";
            byId("clip-meta").textContent = "";
        } else {
            byId("clip-meta").textContent = `R${clip.round} · ${clip.duration.toLocaleString("fr-FR")} s${clip.estimated ? " · fin estimée" : ""}`;
            const badge = frequencyBadge(clip, true);
            if (badge) byId("clip-meta").append(" ", badge);
            detail.textContent = "Clip préparé. Le rendu vidéo est encore à faire.";
        }
    }
    selected = clip;
    deleteButton.hidden = !library || !clip?.exportId;
    clipActions.hidden = !clip?.video;
    editClipCallout.hidden = !window.utilityClipCallout || !clip?.exportId;
    syncReviewButtons();
    if (changed) showLineup(clip);
    if (portrait && changed) {
        portraitError = "";
        showPortrait(undefined);
        refreshPortrait(clip);
    }
    if (autoplay && clip) {
        selectionHistory = [clip, ...selectionHistory.filter((item) => item !== clip)];
        if (clip.video) video.play().catch(() => {});
    }
    for (const row of list.querySelectorAll(".clip-row")) {
        row.setAttribute("aria-pressed", String(visible[Number(row.dataset.index)] === selected));
    }
    for (const marker of radarRoutes.querySelectorAll("button")) {
        marker.setAttribute("aria-pressed", String(marker.clip === selected));
    }
    if (autoplay) syncAddress();
}

function clipRowAt(index) {
    return list.querySelector(`.clip-row[data-index="${index}"]`);
}

function showClipRow(index) {
    if (index < 0) return;
    const page = Math.floor(index / CLIPS_PER_PAGE);
    if (page !== clipPage) renderClipPage(page);
    return clipRowAt(index);
}

function revealSelectedRow() {
    const row = showClipRow(visible.indexOf(selected));
    if (!row) return;
    const item = row.getBoundingClientRect();
    const viewport = list.getBoundingClientRect();
    if (item.top < viewport.top) list.scrollTop += item.top - viewport.top;
    if (item.bottom > viewport.bottom) list.scrollTop += item.bottom - viewport.bottom;
}

function adjacentRowIndex(direction) {
    const index = visible.indexOf(selected);
    if (index !== -1 || !selected) return index + direction;

    // A clip marked as watched can stay in the player after leaving the filtered list.
    const compare = clipComparator();
    if (compare) {
        const clips = currentMatch.clips;
        const selectedOrder = clips.indexOf(selected);
        const next = visible.findIndex((clip) => (compare(selected, clip) || selectedOrder - clips.indexOf(clip)) < 0);
        const insertion = next < 0 ? visible.length : next;
        return direction > 0 ? insertion : insertion - 1;
    }
    const clips = currentMatch.clips;
    const visibleClips = new Set(visible);
    for (let i = clips.indexOf(selected) + direction; i >= 0 && i < clips.length; i += direction) {
        if (visibleClips.has(clips[i])) return visible.indexOf(clips[i]);
    }
    return -1;
}

function playClip(clip) {
    const { radar } = currentMatch;
    if (radar?.lower) {
        const endpoint = heatmapMode ? "impact" : pointSelect.value;
        const level = clip[endpoint][2] < radar.thresholdZ ? "lower" : "upper";
        if (level !== levelSelect.value) {
            levelSelect.value = level;
            renderRadar();
        }
    }
    selectClip(clip, true);
}

function handleLibraryShortcut(event) {
    if (event.defaultPrevented || event.isComposing || event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
    const key = event.key.toLowerCase();
    if (key !== "f" && key !== "n" && key !== "b") return;
    const target = event.target;
    if (target?.isContentEditable || target?.closest?.("input, textarea, select, .throw-filter") || document.querySelector("dialog[open]")) return;

    if (key === "f") {
        if (favoriteButton.hidden || favoriteButton.disabled) return;
        event.preventDefault();
        favoriteButton.click();
        return;
    }

    const direction = key === "n" ? 1 : -1;
    const historyIndex = playbackIndex + direction;
    const previousClip = playbackHistory[historyIndex];
    const clip = previousClip ?? visible[adjacentRowIndex(direction)];
    if (!clip) return;
    if (previousClip) playbackIndex = historyIndex;
    event.preventDefault();
    const listFocused = list.contains(document.activeElement);
    const row = showClipRow(visible.indexOf(clip));
    if (row) {
        if (listFocused) row.focus({ preventScroll: true });
        row.click();
    } else {
        playClip(clip);
    }
    revealSelectedRow();
}

function radarPosition([x, y, z], radar) {
    const left = (x - radar.originX) / radar.units * 100;
    const top = (radar.originY - y) / radar.units * 100;
    if (!(left >= 0 && left <= 100 && top >= 0 && top <= 100)) return;
    return { left, top, lower: Boolean(radar.lower) && z < radar.thresholdZ };
}

function positionOnRadar(element, { left, top }) {
    element.style.left = `${left}%`;
    element.style.top = `${top}%`;
}

function renderRadarLinks() {
    radarRoutes.replaceChildren();
    const selection = new Set(radarSelection);
    const anchors = new Set([...markers.children].filter((marker) => marker.clips.some((clip) => selection.has(clip))));
    for (const marker of markers.children) {
        marker.hidden = anchors.size > 0 && !anchors.has(marker);
        marker.setAttribute("aria-pressed", String(anchors.has(marker)));
    }
    for (const anchor of anchors) renderRadarAnchorLinks(anchor);
}

function renderRadarAnchorLinks(anchor) {
    const { radar } = currentMatch;
    const endpoint = pointSelect.value === "impact" ? "launch" : "impact";
    const label = endpoint === "launch" ? "Départ" : "Activation";
    const lower = levelSelect.value === "lower";
    for (const clip of anchor.clips) {
        const position = radarPosition(clip[endpoint], radar);
        if (!position) continue;
        const tint = kinds[clip.kind].color;
        const dx = anchor.position.left - position.left;
        const dy = anchor.position.top - position.top;
        const line = document.createElement("button");
        line.className = "radar-link";
        line.tabIndex = -1;
        line.style.setProperty("--tint", tint);
        positionOnRadar(line, position);
        line.style.width = `${Math.hypot(dx, dy)}%`;
        line.style.transform = `rotate(${Math.atan2(dy, dx)}rad) translateY(-50%)`;

        const point = document.createElement("button");
        point.className = "marker linked-marker";
        point.style.setProperty("--tint", tint);
        positionOnRadar(point, position);
        const floor = position.lower !== lower ? ` · niveau ${position.lower ? "bas" : "haut"}` : "";
        point.title = `${label} · ${clip.player} · R${clip.round}${floor}`;
        line.title = `Trajectoire · ${point.title}`;
        const selectRoute = () => {
            selectClip(clip, true);
            revealSelectedRow();
        };
        for (const target of [line, point]) {
            target.type = "button";
            target.clip = clip;
            target.setAttribute("aria-label", target.title);
            target.setAttribute("aria-pressed", String(clip === selected));
            target.addEventListener("click", selectRoute);
        }
        radarRoutes.append(line, point);
    }
}

function heatmapDensity(positions) {
    const { width, height } = heatmapCanvas;
    const density = new Float32Array(width * height);
    const radius = Math.round(width * .055);
    const radiusSquared = radius ** 2;
    const gaussianScale = 2 * (radius / 2.5) ** 2;
    const edgeWeight = Math.exp(-radiusSquared / gaussianScale);
    const radialWeights = Array.from({ length: radiusSquared }, (_, distance) => Math.exp(-distance / gaussianScale) - edgeWeight);
    let peak = 0;
    for (const { left, top, weight } of positions) {
        const centerX = Math.round(left / 100 * (width - 1));
        const centerY = Math.round(top / 100 * (height - 1));
        for (let y = Math.max(0, centerY - radius); y <= Math.min(height - 1, centerY + radius); y++) {
            const dySquared = (y - centerY) ** 2;
            for (let x = Math.max(0, centerX - radius); x <= Math.min(width - 1, centerX + radius); x++) {
                const distanceSquared = (x - centerX) ** 2 + dySquared;
                if (distanceSquared >= radiusSquared) continue;
                const index = y * width + x;
                density[index] += weight * radialWeights[distanceSquared];
                peak = Math.max(peak, density[index]);
            }
        }
    }

    return { density, peak };
}

function paintHeatmap(context, density, peak) {
    const image = context.createImageData(heatmapCanvas.width, heatmapCanvas.height);
    const lastColor = HEATMAP_COLORS.length - 1;
    for (let index = 0; index < density.length; index++) {
        if (!density[index]) continue;
        const strength = density[index] / peak;
        const color = strength * lastColor;
        const step = Math.floor(color);
        const start = HEATMAP_COLORS[step];
        const end = HEATMAP_COLORS[Math.min(step + 1, lastColor)];
        const pixel = index * 4;
        for (let channel = 0; channel < 3; channel++) {
            image.data[pixel + channel] = start[channel] + (end[channel] - start[channel]) * (color - step);
        }
        image.data[pixel + 3] = 220 * Math.sqrt(strength);
    }
    context.putImageData(image, 0, 0);
}

function heatmapPositions(radar, lower, timed) {
    return filtered.flatMap((clip) => {
        const position = radarPosition(clip.impact, radar);
        if (position?.lower !== lower) return [];
        const occurrences = selectedSide ? matchingSideOccurrences(clip) : undefined;
        if (!timed) return [{ ...position, weight: occurrences?.length ?? throwFrequency(clip)?.total ?? 1 }];
        const activations = occurrences ? occurrences.map(([, , , , activation]) => activation)
            : clip.activationSeconds ?? [clip.roundActivationSeconds];
        const times = activations
            .filter((time) => Number.isFinite(time) && time >= 0 && time <= roundDuration);
        return times.length ? [{ ...position, times }] : [];
    });
}

function renderHeatmap(radar, lower) {
    const context = heatmapCanvas.getContext("2d");
    if (!context) return;
    const timed = heatmapMode === "timer";
    let positions;
    if (timed) {
        if (!heatmapTimeline || heatmapTimeline.filtered !== filtered || heatmapTimeline.radar !== radar
            || heatmapTimeline.lower !== lower) {
            heatmapTimeline = { filtered, radar, lower, positions: heatmapPositions(radar, lower, true) };
        }
        const hasTimes = heatmapTimeline.positions.length > 0;
        heatmapTimeControl.disabled = heatmapPlay.disabled = !hasTimes;
        if (!hasTimes) stopHeatmapPlayback();
        heatmapTimeControl.value = heatmapTime;
        heatmapTimeControl.setAttribute("aria-valuetext", formatRoundTime(heatmapTime));
        byId("heatmap-elapsed").textContent = `${formatRoundTime(heatmapTime)} / ${formatRoundTime(roundDuration)}`;
        const windowStart = heatmapTime - HEATMAP_TIMER_WINDOW_SECONDS;
        positions = heatmapTimeline.positions.flatMap((position) => {
            const weight = position.times.filter((time) => time > windowStart && time <= heatmapTime).length;
            return weight ? [{ ...position, weight }] : [];
        });
    } else {
        positions = heatmapPositions(radar, lower, false);
    }
    const empty = byId("heatmap-empty");
    empty.hidden = positions.length > 0;
    if (positions.length) {
        const { density, peak } = heatmapDensity(positions);
        paintHeatmap(context, density, peak);
    } else {
        empty.textContent = timed
            ? (heatmapTimeline.positions.length
                ? `Aucune activation dans les ${HEATMAP_TIMER_WINDOW_SECONDS} dernières secondes.`
                : "Aucune activation horodatée dans le timer.")
            : "Aucune activation à afficher.";
        context.clearRect(0, 0, heatmapCanvas.width, heatmapCanvas.height);
    }
}

function formatRoundTime(seconds) {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function stopHeatmapPlayback() {
    if (heatmapFrame !== undefined) cancelAnimationFrame(heatmapFrame);
    heatmapFrame = undefined;
    heatmapPlay.textContent = `Lire ×${HEATMAP_TIMER_SPEED}`;
}

function seekHeatmap(seconds) {
    heatmapTime = seconds;
    renderHeatmap(currentMatch.radar, levelSelect.value === "lower");
}

function toggleHeatmapPlayback() {
    if (heatmapFrame !== undefined) {
        stopHeatmapPlayback();
        syncAddress(true);
        return;
    }
    if (heatmapTime === roundDuration) seekHeatmap(0);
    const start = performance.now() - heatmapTime * 1000 / HEATMAP_TIMER_SPEED;
    heatmapPlay.textContent = "Pause";
    function advance(now) {
        const time = Math.min(roundDuration, Math.floor((now - start) * HEATMAP_TIMER_SPEED / 1000));
        if (time !== heatmapTime) seekHeatmap(time);
        if (time >= roundDuration || heatmapPlay.disabled) {
            stopHeatmapPlayback();
            syncAddress(true);
        } else {
            heatmapFrame = requestAnimationFrame(advance);
        }
    }
    heatmapFrame = requestAnimationFrame(advance);
}

function clusterRadarGroups(points, radius) {
    const visited = new Uint8Array(points.length);
    const groups = [];
    const radiusSquared = radius ** 2;
    for (let index = 0; index < points.length; index++) {
        if (visited[index]) continue;
        const seed = points[index];
        const clips = [...seed.clips];
        let left = seed.left * clips.length;
        let top = seed.top * clips.length;
        for (let candidate = index + 1; candidate < points.length; candidate++) {
            if (visited[candidate]) continue;
            const point = points[candidate];
            if ((seed.left - point.left) ** 2 + (seed.top - point.top) ** 2 > radiusSquared) continue;
            visited[candidate] = 1;
            left += point.left * point.clips.length;
            top += point.top * point.clips.length;
            for (const clip of point.clips) clips.push(clip);
        }
        groups.push({ left: left / clips.length, top: top / clips.length, clips });
    }
    return groups;
}

function radarGroupsForZoom(radar, point, lower) {
    const width = Math.max(1, byId("radar").clientWidth);
    const cache = radarGroupingCache;
    if (!cache || cache.filtered !== filtered || cache.radar !== radar || cache.point !== point || cache.lower !== lower || cache.width !== width) {
        const order = new Map(filtered.map((clip, index) => [clip, index]));
        let groups = filtered.flatMap((clip) => {
            const position = radarPosition(clip[point], radar);
            return position?.lower === lower ? [{ ...position, clips: [clip] }] : [];
        });
        const levels = new Map();
        // Build nested groups so zooming in only divides an existing focus.
        for (let zoom = 500; zoom >= 100; zoom -= 10) {
            const radius = RADAR_CLUSTER_RADIUS_PX / (width * zoom / 100) * 100;
            groups = clusterRadarGroups(groups, radius);
            for (const group of groups) group.clips.sort((a, b) => order.get(a) - order.get(b));
            levels.set(zoom, groups);
        }
        radarGroupingCache = { filtered, radar, point, lower, width, levels };
    }
    return radarGroupingCache.levels.get(radarView.zoom);
}

function refreshRadarGrouping() {
    if (!currentMatch?.radar || heatmapMode) return;
    const scroll = list.scrollTop;
    const previousSelection = radarSelection;
    renderRadar();
    if (previousSelection === radarSelection || (previousSelection && radarSelection?.length === previousSelection.length
        && radarSelection.every((clip, index) => clip === previousSelection[index]))) return;
    renderClipList(selected, false, selected);
    list.scrollTop = scroll;
    syncAddress(true);
}

function selectedRadarGroups(groups, selection, radar, point) {
    return groups.flatMap((group) => {
        const clips = group.clips.filter((clip) => selection.has(clip));
        if (!clips.length) return [];
        if (clips.length === group.clips.length) return [group];
        let left = 0;
        let top = 0;
        for (const clip of clips) {
            const position = radarPosition(clip[point], radar);
            left += position.left;
            top += position.top;
        }
        return [{ left: left / clips.length, top: top / clips.length, clips }];
    });
}

function renderRadar(restoreGroupId) {
    const { radar } = currentMatch;
    radarView?.setAvailable(Boolean(radar));
    const point = pointSelect.value;
    const lower = levelSelect.value === "lower";
    const showHeatmap = Boolean(radar && heatmapMode);
    const showTimer = showHeatmap && heatmapMode === "timer";
    heatmapToggle.hidden = !radar;
    heatmapToggle.setAttribute("aria-pressed", String(showHeatmap));
    byId("heatmap-mode-control").hidden = !showHeatmap;
    heatmapModeSelect.value = showTimer ? "timer" : "cumulative";
    byId("heatmap-timer").hidden = !showTimer;
    if (!showTimer) stopHeatmapPlayback();
    heatmapCanvas.hidden = !showHeatmap;
    byId("heatmap-legend").hidden = !showHeatmap;
    byId("heatmap-empty").hidden = true;
    pointSelect.closest("label").hidden = showHeatmap;
    markers.replaceChildren();
    byId("radar-empty").hidden = Boolean(radar);
    byId("level-control").hidden = !radar?.lower;
    byId("radar-side-control").hidden = !radar;
    radarImage.hidden = !radar;
    if (!radar) {
        radarSelection = undefined;
        radarRoutes.replaceChildren();
        return;
    }
    const image = lower ? radar.lower : radar.upper;
    if (radarImage.getAttribute("src") !== image) radarImage.src = image;
    radarImage.alt = `Radar de ${mapLabel(currentMatch.map)}`;
    if (showHeatmap) {
        radarSelection = undefined;
        radarRoutes.replaceChildren();
        renderHeatmap(radar, lower);
        return;
    }
    let groups = radarGroupsForZoom(radar, point, lower);
    if (restoreGroupId) {
        radarSelection = groups.find(({ clips }) => clips.some((clip) => clipId(clip) === restoreGroupId))?.clips;
    }
    if (radarSelection) {
        // Zoom changes how the selected clips are grouped, but never adds clips to the selection.
        const focusedGroups = selectedRadarGroups(groups, new Set(radarSelection), radar, point);
        if (focusedGroups.length) {
            const selectedClips = new Set(focusedGroups.flatMap(({ clips }) => clips));
            radarSelection = filtered.filter((clip) => selectedClips.has(clip));
            groups = focusedGroups;
        } else {
            radarSelection = undefined;
        }
    }
    const points = document.createDocumentFragment();
    for (const { left, top, clips } of groups) {
        const clip = clips[0];
        const marker = document.createElement("button");
        marker.className = clips.length > 1 ? "marker cluster" : "marker";
        marker.style.setProperty("--tint", clips.some((item) => item.kind !== clip.kind) ? "#eef0f2" : kinds[clip.kind].color);
        marker.position = { left, top };
        positionOnRadar(marker, marker.position);
        marker.clips = clips;
        marker.title = clips.length > 1 ? `Afficher les ${clips.length} lancers`
            : `${kinds[clip.kind].label} · ${clip.player} · R${clip.round}`;
        marker.setAttribute("aria-label", marker.title);
        if (clips.length > 1) marker.textContent = clips.length;
        marker.addEventListener("click", () => {
            const active = radarSelection?.length === clips.length && clips.every((item) => radarSelection.includes(item));
            const next = active ? clips[(clips.indexOf(selected) + 1) % clips.length] : clips[0];
            if (!active) {
                playbackScope = undefined;
                radarSelection = clips;
                renderRadarLinks();
            }
            renderClipList(next, true);
            revealSelectedRow();
            if (matchMedia("(max-width: 780px)").matches) {
                video.closest(".panel").scrollIntoView({ block: "start" });
            }
        });
        points.append(marker);
    }
    markers.append(points);
    renderRadarLinks();
}

function showClipStateError(error) {
    const status = byId("clip-state-error");
    status.hidden = !error;
    status.textContent = error?.message ?? "";
}

async function markWatched(clip, badge) {
    if (clip.watched) return;
    clip.watched = true;
    badge.hidden = false;
    showClipStateError();
    try {
        await request("/api/clip-state", { ...clipReference(clip), watched: true });
    } catch (error) {
        clip.watched = false;
        const mountedBadge = clipRowAt(visible.indexOf(clip))?.querySelector(".watched-badge");
        if (mountedBadge) mountedBadge.hidden = true;
        if (currentMatch?.clips.includes(clip) && activeStatusFilters.has("unwatched")) {
            renderClips(undefined, selected);
            syncAddress(true);
        }
        showClipStateError(error);
        return;
    }
    if (activeStatusFilters.has("unwatched") && filtered.includes(clip)) {
        const index = visible.indexOf(clip);
        const wasFocused = document.activeElement === clipRowAt(index);
        const scroll = list.scrollTop;
        renderClips(undefined, selected);
        syncAddress(true);
        list.scrollTop = scroll;
        if (wasFocused) {
            const next = showClipRow(Math.min(index, visible.length - 1));
            (next ?? unwatchedFilter).focus();
        }
    }
}

function isPotential(clip) {
    return clip?.potentialScore > 0 && !clip.favorite && !clip.potentialDismissed;
}

function potentialPoints(clip) {
    const points = (clip.potentialScore * 100).toLocaleString("fr-FR", { maximumFractionDigits: 2 });
    return `${points}/100 pts`;
}

function comparePotential(a, b) {
    const aScore = isPotential(a) ? a.potentialScore : 0;
    const bScore = isPotential(b) ? b.potentialScore : 0;
    return bScore - aScore || (aScore > 0 ? clipId(a).localeCompare(clipId(b)) : a.round - b.round);
}

function clipSortMode() {
    return clipSort || (activeStatusFilters.has("potential") ? "points" : "round");
}

function clipComparator() {
    if (clipSortMode() === "points") return comparePotential;
    if (library) return (a, b) => a.round - b.round;
}

function syncReviewButtons() {
    dismissPotentialButton.hidden = !clipStateEnabled || !isPotential(selected);
    dismissPotentialButton.disabled = reviewPending.has(selected);
    favoriteButton.hidden = !clipStateEnabled || !selected?.exportId;
    if (favoriteButton.hidden) return;
    const favorite = Boolean(selected.favorite);
    favoriteButton.textContent = favorite ? "★" : "☆";
    favoriteButton.setAttribute("aria-pressed", String(favorite));
    favoriteButton.title = `${favorite ? "Retirer des favoris" : "Ajouter aux favoris"} · ${selected.player}`;
    favoriteButton.setAttribute("aria-label", favoriteButton.title);
    favoriteButton.disabled = reviewPending.has(selected);
}

async function reviewClip(button, updates) {
    const clip = selected;
    if (!clip?.exportId || reviewPending.has(clip)) return;
    const wasFocused = document.activeElement === button;
    reviewPending.add(clip);
    syncReviewButtons();
    showClipStateError();
    try {
        const state = await request("/api/clip-state", { ...clipReference(clip), ...updates });
        const wasPotential = isPotential(clip);
        const potentialOnly = activeStatusFilters.has("potential");
        const advance = potentialOnly && wasPotential && selected === clip
            && (state.favorite === true || state.potentialDismissed === true);
        // Use the current rank before the review removes the clip's points from the sort.
        const next = advance ? visible[adjacentRowIndex(1)] ?? visible[adjacentRowIndex(-1)] : selected;
        clip.favorite = state.favorite === true;
        clip.potentialDismissed = state.potentialDismissed === true;
        if (selected === clip) renderClipTitle(clip);
        const leavesFilter = (activeStatusFilters.has("favorites") && !clip.favorite)
            || (potentialOnly && !isPotential(clip));
        if (leavesFilter && (advance || filtered.includes(clip))) {
            const scroll = list.scrollTop;
            renderClips(undefined, undefined, next);
            syncAddress(true);
            if (advance && updates.favorite === true && selected) selectClip(selected, true);
            list.scrollTop = scroll;
            if (next !== clip) revealSelectedRow();
        } else if (clipSortMode() === "points" && wasPotential !== isPotential(clip) && filtered.includes(clip)) {
            renderClips(undefined, selected);
            syncAddress(true);
        } else {
            const row = clipRowAt(visible.indexOf(clip));
            if (row) {
                row.querySelector(".favorite-badge").hidden = !clip.favorite;
                row.querySelector(".potential-badge").hidden = !isPotential(clip);
            }
        }
    } catch (error) {
        showClipStateError(error);
    } finally {
        reviewPending.delete(clip);
        syncReviewButtons();
        if (wasFocused && document.activeElement === document.body) {
            const activeFilter = statusFilters.get(activeStatusFilters.values().next().value);
            (button.hidden ? activeFilter ?? favoriteButton : button).focus();
        }
    }
}

favoriteButton.addEventListener("click", () => reviewClip(favoriteButton, { favorite: !selected?.favorite }));
dismissPotentialButton.addEventListener("click", () => {
    if (isPotential(selected)) reviewClip(dismissPotentialButton, { potentialDismissed: true });
});

function renderClips(deletion, playingClip, preferred = selected, restoreGroupId) {
    const frequency = frequencySelect?.value;
    const query = clipSearch?.value.trim().toLocaleLowerCase("fr");
    const potentialOnly = activeStatusFilters.has("potential");
    filtered = currentMatch.clips.filter((clip) => (filter === "all" || clip.kind === filter)
        && (!roundSelect.value || String(clip.round) === roundSelect.value)
        && (!frequency || throwFrequency(clip)?.category === frequency)
        && (!activeStatusFilters.has("favorites") || clip.favorite)
        && (!activeStatusFilters.has("unwatched") || !clip.watched)
        && (!potentialOnly || isPotential(clip))
        && matchesThrowFilters(clip)
        && (!query || calloutName(clip, currentMatch).toLocaleLowerCase("fr").includes(query)));
    const compare = clipComparator();
    if (compare) filtered.sort(compare);
    if (clipStateEnabled) byId("clip-sort").textContent = clipSortMode() === "points" ? "Tri par points" : "Tri par round";
    renderRadar(restoreGroupId);
    const rows = radarSelection ?? filtered;
    if (deletion && selected === deletion.clip) {
        const index = radarSelection ? deletion.row : deletion.filtered;
        preferred = selectionHistory.find((clip) => rows.includes(clip)) ?? rows[Math.min(index, rows.length - 1)];
    }
    renderClipList(preferred, false, playingClip);
    if (deletion) {
        list.scrollTop = deletion.scroll;
        revealSelectedRow();
    }
}

function renderClipList(preferred = selected, autoplay = false, playingClip) {
    visible = radarSelection ?? filtered;
    byId("count").textContent = `${visible.length} ${visible.length === 1 ? "lancer" : "lancers"}`;
    const index = visible.indexOf(playingClip ?? preferred);
    const page = index >= 0 ? Math.floor(index / CLIPS_PER_PAGE) : playingClip ? clipPage : 0;
    renderClipPage(page);
    selectClip(playingClip ?? (index >= 0 ? preferred : visible[0]), autoplay);
}

function renderClipPage(page) {
    const pages = Math.max(1, Math.ceil(visible.length / CLIPS_PER_PAGE));
    clipPage = Math.min(pages - 1, Math.max(0, page));
    clipPages.hidden = pages === 1;
    byId("clip-page-status").textContent = `Page ${clipPage + 1} sur ${pages}`;
    previousPage.setAttribute("aria-disabled", String(clipPage === 0));
    nextPage.setAttribute("aria-disabled", String(clipPage + 1 === pages));

    const fragment = document.createDocumentFragment();
    if (!visible.length) {
        const empty = document.createElement("p");
        empty.textContent = "Aucun clip pour ces filtres.";
        if (activeStatusFilters.has("unwatched")) empty.textContent = "Aucun clip à traiter pour ces filtres.";
        if (activeStatusFilters.has("favorites")) empty.textContent = "Aucun favori pour ces filtres.";
        if (activeStatusFilters.has("potential")) {
            empty.textContent = activeStatusFilters.has("unwatched")
                ? "Aucun potentiel favori à traiter pour ces filtres."
                : "Aucun potentiel favori pour ces filtres.";
        }
        fragment.append(empty);
    }
    const start = clipPage * CLIPS_PER_PAGE;
    visible.slice(start, start + CLIPS_PER_PAGE).forEach((clip, offset) => {
        const row = document.createElement("button");
        row.className = "clip-row";
        row.dataset.index = start + offset;
        row.setAttribute("aria-pressed", String(clip === selected));
        row.style.setProperty("--tint", kinds[clip.kind].color);
        row.append(dot());
        const badge = clipStateEnabled ? document.createElement("small") : null;
        if (badge) {
            badge.className = "watched-badge";
            badge.textContent = "Vu";
            badge.hidden = !clip.watched;
        }
        const fields = [["span", clip.player], ["span", kinds[clip.kind].label], ["small", `R${clip.round}`],
            ["small", `${clip.duration.toLocaleString("fr-FR")} s`]];
        for (const [index, [tag, value]] of fields.entries()) {
            const cell = document.createElement(tag);
            if (library && index === 0) {
                const callout = document.createElement("span");
                callout.className = "clip-callout";
                callout.textContent = calloutName(clip, currentMatch);
                cell.append(callout);
                if (badge) cell.append(badge);
                const source = document.createElement("small");
                source.className = "clip-source";
                source.title = clip.source;
                source.textContent = value;
                cell.append(source);
            } else {
                cell.textContent = value;
            }
            if (index === 1) {
                const tags = document.createElement("span");
                tags.className = "clip-tags";
                const frequency = frequencyBadge(clip);
                if (frequency) tags.append(frequency);
                if (clipStateEnabled) {
                    const favorite = document.createElement("span");
                    favorite.className = "favorite-badge";
                    favorite.textContent = "Favori";
                    favorite.hidden = !clip.favorite;
                    const potential = document.createElement("span");
                    potential.className = "potential-badge";
                    potential.textContent = clip.potentialScore > 0
                        ? `Potentiel favori · ${potentialPoints(clip)}` : "Potentiel favori";
                    potential.hidden = !isPotential(clip);
                    tags.append(favorite, potential);
                }
                cell.append(tags);
            }
            row.append(cell);
        }
        row.addEventListener("click", () => {
            playClip(clip);
            if (badge) markWatched(clip, badge);
        });
        fragment.append(row);
    });
    list.replaceChildren(fragment);
    list.scrollTop = 0;
}

for (const [button, direction] of [[previousPage, -1], [nextPage, 1]]) {
    button.addEventListener("click", () => {
        if (button.getAttribute("aria-disabled") === "false") renderClipPage(clipPage + direction);
    });
}

function refreshCalloutLabels() {
    if (clipSearch?.value.trim() && currentMatch) {
        radarSelection = undefined;
        renderClips();
        syncAddress(true);
        return;
    }
    renderClipTitle(selected);
    for (const row of list.querySelectorAll(".clip-row")) {
        row.querySelector(".clip-callout").textContent = calloutName(visible[Number(row.dataset.index)], currentMatch);
    }
}

editClipCallout.addEventListener("click", () => {
    if (!selected?.exportId) return;
    clipCalloutForm.clip = selected;
    clipCalloutInput.value = selected.calloutOverride ?? "";
    clipCalloutInput.placeholder = calloutName({ ...selected, calloutOverride: null }, currentMatch);
    byId("clip-callout-options").replaceChildren(...[...new Set((currentMatch.zones ?? []).map((zone) => zone.name))]
        .sort((a, b) => a.localeCompare(b)).map((name) => new Option(name, name)));
    byId("clip-callout-error").hidden = true;
    byId("reset-clip-callout").disabled = !selected.calloutOverride;
    clipCalloutDialog.showModal();
    clipCalloutInput.focus();
});

async function saveClipCallout(name) {
    const clip = clipCalloutForm.clip;
    if (!clip) return;
    const controls = [...clipCalloutForm.querySelectorAll("button, input")];
    for (const control of controls) control.disabled = true;
    const error = byId("clip-callout-error");
    error.hidden = true;
    try {
        const state = await request("/api/clip-state", { ...clipReference(clip), calloutOverride: name });
        clip.calloutOverride = state.calloutOverride;
        refreshCalloutLabels();
        clipCalloutDialog.close();
    } catch (failure) {
        error.textContent = failure.message;
        error.hidden = false;
    } finally {
        for (const control of controls) control.disabled = false;
        byId("reset-clip-callout").disabled = !clip.calloutOverride;
    }
}

clipCalloutForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveClipCallout(clipCalloutInput.value.trim());
});
byId("reset-clip-callout").addEventListener("click", () => saveClipCallout(null));
byId("cancel-clip-callout").addEventListener("click", () => clipCalloutDialog.close());

function throwFilterKey(value) {
    return value.normalize("NFKC").trim().replace(/\s+/g, " ").toLocaleLowerCase("fr");
}

function throwFilterSearch(value) {
    return throwFilterKey(value).normalize("NFD").replace(/\p{M}/gu, "");
}

function indexThrowFilters() {
    teamOptions.clear();
    playerOptions.clear();
    const knownNames = new Set();
    for (const match of catalogue.matches) {
        for (const clip of match.clips) {
            const throwers = [...(clip.throwers ?? [])];
            if (clip.player && !throwers.some(({ player }) => player === clip.player)) {
                throwers.push({ player: clip.player, team: clip.team });
            }
            clipThrowers.set(clip, throwers.map(({ player, team, steamId }) => {
                const teamId = throwFilterKey(team ?? "");
                const name = throwFilterKey(player);
                const playerId = steamId || `name:${name}`;
                if (steamId) knownNames.add(name);
                if (teamId && !teamOptions.has(teamId)) teamOptions.set(teamId, { id: teamId, label: team });
                if (!playerOptions.has(playerId)) {
                    playerOptions.set(playerId, { id: playerId, label: player, aliases: new Set(), teams: new Set() });
                }
                const option = playerOptions.get(playerId);
                option.aliases.add(player);
                if (teamId) option.teams.add(teamId);
                return { teamId, playerId };
            }));
        }
    }
    for (const name of knownNames) {
        const legacy = playerOptions.get(`name:${name}`);
        if (legacy) legacy.label = `${legacy.label} (pseudo seul)`;
    }
}

function setThrowSide(side) {
    selectedSide = side === "t" || side === "ct" ? side : "";
    for (const select of sideSelects) select.value = selectedSide;
}

function matchingSideOccurrences(clip) {
    const occurrences = clip.sideOccurrences ?? [[clip.steamId, clip.player, clip.team, clip.side, clip.roundActivationSeconds]];
    return occurrences.filter(([steamId, player, team, side]) => side === selectedSide
        && (!selectedTeam || throwFilterKey(team ?? "") === selectedTeam)
        && (!selectedPlayers.size || selectedPlayers.has(steamId || `name:${throwFilterKey(player)}`)));
}

function matchesThrowFilters(clip) {
    if (selectedSide) return matchingSideOccurrences(clip).length > 0;
    if (!selectedTeam && !selectedPlayers.size) return true;
    return clipThrowers.get(clip)?.some(({ teamId, playerId }) => (!selectedTeam || selectedTeam === teamId)
        && (!selectedPlayers.size || selectedPlayers.has(playerId)));
}

function createThrowFilter(id, multiple, onChange) {
    const control = byId(id);
    const summary = control.querySelector("summary");
    const value = control.querySelector(".throw-filter-value");
    const menu = control.querySelector(".throw-filter-menu");
    const search = menu.querySelector('input[type="search"]');
    const clear = menu.querySelector(".throw-filter-clear");
    const choices = menu.querySelector(".throw-filter-options");
    let options = [];
    let selection = new Set();
    control.hidden = false;

    function fitMenu() {
        if (!control.open) return;
        const overhang = control.getBoundingClientRect().left + menu.offsetWidth + 12 - document.documentElement.clientWidth;
        menu.style.left = `${-Math.max(0, overhang)}px`;
    }

    function setSelection(ids) {
        selection = new Set(ids);
        const first = options.find((option) => selection.has(option.id));
        value.textContent = selection.size > 1 ? `${selection.size} joueurs` : first?.label ?? "";
        clear.hidden = !selection.size;
        for (const input of choices.querySelectorAll("input")) input.checked = selection.has(input.value);
        fitMenu();
    }

    function renderOptions() {
        const query = throwFilterSearch(search.value);
        const fragment = document.createDocumentFragment();
        for (const option of options) {
            const names = option.aliases ?? [option.label];
            if (query && ![...names].some((name) => throwFilterSearch(name).includes(query))) continue;
            const label = document.createElement("label");
            const input = document.createElement("input");
            input.type = multiple ? "checkbox" : "radio";
            input.name = id;
            input.value = option.id;
            input.checked = selection.has(option.id);
            label.append(input, option.label);
            fragment.append(label);
        }
        if (!fragment.childElementCount) {
            const empty = document.createElement("p");
            empty.textContent = "Aucun résultat";
            fragment.append(empty);
        }
        choices.replaceChildren(fragment);
        choices.scrollTop = 0;
    }

    function close() {
        control.open = false;
        summary.focus({ preventScroll: true });
    }

    control.addEventListener("toggle", () => {
        if (!control.open) return;
        search.value = "";
        renderOptions();
        fitMenu();
        search.focus({ preventScroll: true });
    });
    search.addEventListener("input", renderOptions);
    choices.addEventListener("change", ({ target }) => {
        const next = multiple ? new Set(selection) : new Set();
        if (target.checked) next.add(target.value);
        else next.delete(target.value);
        if (!multiple) close();
        onChange(next);
    });
    clear.addEventListener("click", () => {
        if (multiple) search.focus({ preventScroll: true });
        else close();
        onChange(new Set());
    });
    control.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || !control.open) return;
        event.preventDefault();
        close();
    });
    control.addEventListener("focusout", ({ relatedTarget }) => {
        if (relatedTarget && !control.contains(relatedTarget)) control.open = false;
    });
    document.addEventListener("click", ({ target }) => {
        if (control.open && !control.contains(target)) control.open = false;
    });
    window.addEventListener("resize", fitMenu);

    return {
        setSelection,
        setOptions(available, ids) {
            options = [...available].sort((a, b) => a.label.localeCompare(b.label, "fr"));
            setSelection(ids);
            if (control.open) renderOptions();
        },
    };
}

function renderThrowFilters() {
    if (!teamOptions.has(selectedTeam)) selectedTeam = "";
    const players = [...playerOptions.values()].filter((player) => !selectedTeam || player.teams.has(selectedTeam));
    const allowed = new Set(players.map((player) => player.id));
    selectedPlayers = new Set([...selectedPlayers].filter((id) => allowed.has(id)));
    teamMenu.setOptions(teamOptions.values(), selectedTeam ? [selectedTeam] : []);
    playerMenu.setOptions(players, selectedPlayers);
}

function applyThrowFilters() {
    radarSelection = undefined;
    renderClips();
    syncAddress();
}

const teamMenu = library && createThrowFilter("team-filter", false, (ids) => {
    selectedTeam = ids.values().next().value ?? "";
    renderThrowFilters();
    applyThrowFilters();
});
const playerMenu = library && createThrowFilter("player-filter", true, (ids) => {
    selectedPlayers = ids;
    playerMenu.setSelection(ids);
    applyThrowFilters();
});

function renderFilters(keepCurrent = false) {
    const bar = byId("types");
    bar.replaceChildren();
    const available = new Set(currentMatch.clips.map((clip) => clip.kind));
    if (filter !== "all" && !available.has(filter)) {
        if (keepCurrent) available.add(filter);
        else filter = "all";
    }
    for (const key of ["all", ...Object.keys(kinds).filter((kind) => available.has(kind))]) {
        const button = document.createElement("button");
        const kind = kinds[key];
        if (kind) {
            button.style.setProperty("--tint", kind.color);
            button.append(dot());
        }
        button.append(kind?.label ?? "Tous");
        button.setAttribute("aria-pressed", String(key === filter));
        button.addEventListener("click", () => {
            filter = key;
            for (const sibling of bar.children) sibling.setAttribute("aria-pressed", String(sibling === button));
            renderClips();
            syncAddress();
        });
        bar.append(button);
    }
}

function renderRounds(current = "") {
    const rounds = new Set(currentMatch.clips.map((clip) => clip.round));
    if (current) rounds.add(Number(current));
    roundSelect.replaceChildren(new Option("Tous", ""));
    for (const round of [...rounds].sort((a, b) => a - b)) addOption(roundSelect, round, round);
    roundSelect.value = current;
}

function switchMatch({ round = "", level = "upper", groupId, selectedId, fromAddress = false } = {}) {
    const previousMap = currentMatch?.map;
    currentMatch = catalogue.matches[Number(matchSelect.value)];
    if (currentMatch?.map !== previousMap) {
        radarView?.reset();
        if (!fromAddress) {
            stopHeatmapPlayback();
            heatmapTime = 0;
        }
    }
    radarSelection = undefined;
    byId("library-content").hidden = !currentMatch;
    byId("library-empty").hidden = Boolean(currentMatch);
    if (!currentMatch) {
        setLibraryFullscreen(false);
        filtered = [];
        visible = [];
        selectClip(undefined);
        return;
    }
    if (library) sessionStorage.setItem("utilityMap", currentMatch.map);
    renderRounds(round);
    levelSelect.value = currentMatch.radar?.lower ? level : "upper";
    renderFilters(fromAddress);
    const clip = selectedId && currentMatch.clips.find((item) => clipId(item) === selectedId);
    // A clip just marked as seen can still be open in the player under À traiter.
    const keepPlaying = clip?.watched && activeStatusFilters.has("unwatched") && matchesThrowFilters(clip)
        && (!activeStatusFilters.has("potential") || isPotential(clip));
    renderClips(undefined, keepPlaying ? clip : undefined, fromAddress ? clip ?? null : selected, groupId);
    revealSelectedRow();
}

function updateMapOptions(preferred) {
    matchSelect.replaceChildren();
    catalogue.matches.forEach((match, index) => addOption(matchSelect, index, mapLabel(match.map)));
    const index = catalogue.matches.findIndex((match) => match.map === preferred);
    if (index >= 0) matchSelect.value = String(index);
    matchSelect.disabled = catalogue.matches.length === 0;
}

portraitButton.hidden = !portrait;
if (frequencySelect && library) {
    byId("frequency-control").hidden = false;
    for (const [value, label] of Object.entries(frequencyLabels)) addOption(frequencySelect, value, label);
    frequencySelect.addEventListener("change", () => {
        renderClips();
        syncAddress();
    });
}
const clipSortButton = byId("clip-sort");
clipSortButton.hidden = !clipStateEnabled;
clipSortButton.addEventListener("click", () => {
    clipSort = clipSortMode() === "points" ? "round" : "points";
    renderClips(undefined, selected);
    syncAddress();
});

for (const [status, button] of statusFilters) {
    button.hidden = !clipStateEnabled;
    button.addEventListener("click", () => {
        const enable = !activeStatusFilters.has(status);
        if (enable) {
            if (status === "favorites") activeStatusFilters.clear();
            else activeStatusFilters.delete("favorites");
            activeStatusFilters.add(status);
        } else {
            activeStatusFilters.delete(status);
        }
        showStatusFilter();
        renderClips(undefined, undefined, enable && status === "potential" ? null : selected);
        syncAddress();
    });
}

function restoreLibraryAddress() {
    const params = new URLSearchParams(navigation ? navigation.search() : location.search);
    const exportId = params.get("export");
    const recent = exportId && catalogue.matches.find((match) => match.clips.some((clip) => clip.exportId === exportId));
    updateMapOptions(params.get("map") ?? recent?.map ?? sessionStorage.getItem("utilityMap"));
    if (clipSearch) clipSearch.value = (params.get("q") ?? "").slice(0, 64);
    const type = params.get("type");
    filter = Object.hasOwn(kinds, type) ? type : "all";
    const statuses = clipStateEnabled ? params.getAll("status") : [];
    activeStatusFilters = new Set(statuses.includes("favorites") ? ["favorites"]
        : statuses.filter((status) => statusFilters.has(status)));
    const sort = params.get("sort");
    clipSort = clipStateEnabled && (sort === "round" || sort === "points") ? sort : "";
    const frequency = params.get("frequency");
    if (frequencySelect) frequencySelect.value = Object.hasOwn(frequencyLabels, frequency) ? frequency : "";
    setThrowSide(params.get("side"));
    selectedTeam = params.get("team") ?? "";
    selectedPlayers = new Set(params.getAll("player"));
    renderThrowFilters();
    showStatusFilter();
    const heatmap = params.get("heatmap");
    stopHeatmapPlayback();
    heatmapMode = heatmap === "timer" ? "timer" : heatmap === "1" ? "cumulative" : "";
    const time = Number(params.get("time"));
    heatmapTime = Number.isInteger(time) && time >= 0 && time <= roundDuration ? time : 0;
    pointSelect.value = params.get("points") === "launch" ? "launch" : "impact";
    const round = params.get("round");
    switchMatch({
        round: /^[1-9]\d{0,3}$/.test(round) ? round : "", level: params.get("floor") === "lower" ? "lower" : "upper",
        groupId: params.get("group"), selectedId: params.get("clip"), fromAddress: true,
    });
    syncAddress(true);
}

function rememberVideoAudio() {
    const key = "utilityVideoAudio";
    try {
        const saved = JSON.parse(localStorage.getItem(key));
        if (typeof saved?.volume === "number" && saved.volume >= 0 && saved.volume <= 1) video.volume = saved.volume;
        if (typeof saved?.muted === "boolean") video.muted = saved.muted;
    } catch {
        // Use the player's defaults when the browser cannot read the preference.
    }
    video.addEventListener("volumechange", () => {
        try {
            localStorage.setItem(key, JSON.stringify({ volume: video.volume, muted: video.muted }));
        } catch {
            // Volume controls still work when browser storage is unavailable.
        }
    });
}

rememberVideoAudio();

byId("heatmap-scale").style.backgroundImage = `linear-gradient(to right, ${HEATMAP_COLORS.map((color) => `rgb(${color.join(",")})`).join(", ")})`;
heatmapToggle.addEventListener("click", () => {
    heatmapMode = heatmapMode ? "" : "cumulative";
    renderClips();
    syncAddress();
});
heatmapModeSelect.addEventListener("change", () => {
    heatmapMode = heatmapModeSelect.value;
    renderRadar();
    syncAddress();
});
heatmapTimeControl.addEventListener("input", () => {
    stopHeatmapPlayback();
    seekHeatmap(heatmapTimeControl.valueAsNumber);
});
heatmapTimeControl.addEventListener("change", () => syncAddress(true));
heatmapPlay.addEventListener("click", toggleHeatmapPlayback);
if (library) {
    if (clipSearch) {
        clipSearch.hidden = false;
        clipSearch.addEventListener("input", () => {
            if (!currentMatch) return;
            radarSelection = undefined;
            renderClips();
            syncAddress(true);
        });
    }
    window.handleLibraryShortcut = handleLibraryShortcut;
    document.addEventListener("keydown", handleLibraryShortcut);
    document.body.classList.add("library-view");
    document.querySelector("h1").textContent = "Votre prochain lancer décisif.";
    byId("match-label").textContent = "Carte";
    indexThrowFilters();
    if (navigation) window.restoreLibraryAddress = restoreLibraryAddress;
    else window.addEventListener("popstate", restoreLibraryAddress);
    restoreLibraryAddress();
    if (window.utilityCallouts) initializeCalloutEditor({
        matches: catalogue.matches, currentMatch: () => currentMatch, currentClip: () => selected, changed: refreshCalloutLabels,
    });
} else {
    byId("demo-name").textContent = catalogue.title;
    updateMapOptions();
    switchMatch();
}
matchSelect.addEventListener("change", () => {
    switchMatch();
    syncAddress();
});
const radarViewport = byId("radar");
let resizeTimer;
new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
        const width = radarViewport.clientWidth;
        if (width > 0 && radarGroupingCache?.width !== width) refreshRadarGrouping();
    }, 120);
}).observe(radarViewport);
roundSelect.addEventListener("change", () => {
    renderClips();
    syncAddress();
});
for (const select of sideSelects) {
    select.addEventListener("change", () => {
        setThrowSide(select.value);
        renderClips();
        syncAddress();
    });
}
for (const control of [pointSelect, levelSelect]) {
    control.addEventListener("change", () => {
        radarSelection = undefined;
        renderClips();
        syncAddress();
    });
}
byId("radar").addEventListener("click", (event) => {
    if (radarSelection && !event.target.closest(".marker, .radar-link, .radar-zoom")) {
        playbackScope = undefined;
        radarSelection = undefined;
        renderRadar();
        renderClipList();
        syncAddress();
    }
});
video.addEventListener("error", () => {
    video.hidden = true;
    detail.hidden = false;
    detail.textContent = "Ce fichier vidéo n’a pas pu être lu.";
});

byId("copy-command").addEventListener("click", async () => {
    const clip = selected;
    if (!throwCommand.textContent) return;
    try {
        await navigator.clipboard.writeText(throwCommand.textContent.replace("\n", " "));
        if (selected === clip) copyStatus.textContent = "Copié";
    } catch {
        if (selected !== clip) return;
        const range = document.createRange();
        range.selectNodeContents(throwCommand);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        copyStatus.textContent = "Appuie sur Ctrl+C pour copier les commandes.";
    }
});

portraitButton.addEventListener("click", async () => {
    const clip = selected;
    if (!portraitState || !clip?.exportId || portraitButton.disabled) return;
    const open = portraitState.exists;
    portraitPending = true;
    portraitError = "";
    showPortrait(portraitState, open ? "" : "Préparation du 9:16…");
    try {
        await request(open ? "/api/open-portrait" : "/api/portrait", clipReference(clip));
    } catch (error) {
        if (selected === clip) portraitError = error.message;
    } finally {
        portraitPending = false;
        refreshPortrait(selected);
    }
});

deleteButton.addEventListener("click", () => {
    if (!selected?.exportId || deleteButton.disabled) return;
    clipToDelete = selected;
    byId("delete-description").textContent = `${selected.player} · ${kinds[selected.kind].label} · ${selected.source}`;
    deleteDialog.returnValue = "cancel";
    deleteDialog.showModal();
});

deleteDialog.addEventListener("close", async () => {
    if (deleteDialog.returnValue !== "delete" || !clipToDelete) return;
    const clip = clipToDelete;
    clipToDelete = undefined;
    deleteButton.disabled = true;
    const status = byId("delete-status");
    status.hidden = true;
    status.textContent = "";
    clearTimeout(deleteToastTimer);
    deleteToast.textContent = "";
    if (selected === clip) {
        video.pause();
        video.removeAttribute("src");
        video.load();
    }
    try {
        await request("/api/delete-clip", clipReference(clip));
        const map = catalogue.matches.find((match) => match.clips.includes(clip));
        const deletion = { clip, row: visible.indexOf(clip), filtered: filtered.indexOf(clip), scroll: list.scrollTop };
        selectionHistory = selectionHistory.filter((item) => item !== clip);
        playbackIndex -= playbackHistory.slice(0, playbackIndex + 1).filter((item) => item === clip).length;
        playbackHistory = playbackHistory.filter((item) => item !== clip);
        map.clips = map.clips.filter((item) => item !== clip);
        indexThrowFilters();
        renderThrowFilters();
        if (!map.clips.length) {
            catalogue.matches.splice(catalogue.matches.indexOf(map), 1);
            updateMapOptions(currentMatch?.map);
            if (map === currentMatch) switchMatch();
        } else if (map === currentMatch) {
            renderRounds(roundSelect.value);
            renderFilters(true);
            renderClips(deletion);
        }
        syncAddress(true);
        deleteToast.textContent = "Clip supprimé";
        deleteToastTimer = setTimeout(() => { deleteToast.textContent = ""; }, 3000);
    } catch (error) {
        selectClip(selected);
        status.textContent = error.message;
        status.hidden = false;
    } finally {
        deleteButton.disabled = false;
    }
});
