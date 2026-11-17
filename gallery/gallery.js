"use strict";

const catalogue = window.utilityCatalogue;
const groupRadius = catalogue.groupRadius ?? 4;
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

const byId = (id) => document.getElementById(id);
const matchSelect = byId("match");
const roundSelect = byId("round");
const favoritesFilter = byId("favorites");
const unwatchedFilter = byId("unwatched");
const frequencySelect = byId("frequency");
const pointSelect = byId("point");
const levelSelect = byId("level");
const video = byId("video");
const detail = byId("video-empty");
const radarImage = byId("radar-image");
const markers = byId("points");
const radarRoutes = byId("radar-routes");
const radarView = createRadarViewport(byId("radar"), byId("radar-content"));
const list = byId("clips");
const deleteButton = byId("delete-clip");
const favoriteButton = byId("favorite-clip");
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
let statusFilter = "";
const favoritePending = new Set();
let filtered = [];
let visible = [];
let radarSelection;
let selected;
let selectionHistory = [];
let clipToDelete;
let deleteToastTimer;
let portraitTimer;
let portraitState;
let portraitPending = false;
let portraitError = "";

const navigation = library && window.parent !== window ? window.parent.utilityNavigation : undefined;

function createRadarViewport(viewport, content) {
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
        if (!available || zoom === minZoom || event.target.closest(".marker, .callout-vertex, .radar-zoom")) return;
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
    if (roundSelect.value) params.set("round", roundSelect.value);
    if (statusFilter) params.set("status", statusFilter);
    if (frequencySelect?.value) params.set("frequency", frequencySelect.value);
    if (pointSelect.value !== "impact") params.set("points", pointSelect.value);
    if (levelSelect.value === "lower") params.set("floor", "lower");
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
    favoritesFilter.setAttribute("aria-pressed", String(statusFilter === "favorites"));
    unwatchedFilter?.setAttribute("aria-pressed", String(statusFilter === "unwatched"));
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
    const mean = average.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
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
    title.textContent = `${kinds[clip.kind].label} · ${location}`;
}

function selectClip(clip, autoplay = false) {
    const changed = selected !== clip;
    if (selected !== clip || !video.getAttribute("src")) {
        video.pause();
        video.removeAttribute("src");
        video.hidden = !clip?.video;
        detail.hidden = Boolean(clip?.video);
        if (clip?.video) video.src = clip.video;
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
    syncFavoriteButton();
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
    for (const marker of markers.querySelectorAll("button")) {
        marker.setAttribute("aria-pressed", String(marker.clips === radarSelection));
    }
    for (const marker of radarRoutes.querySelectorAll("button")) {
        marker.setAttribute("aria-pressed", String(marker.clip === selected));
    }
    if (autoplay) syncAddress();
}

function revealSelectedRow() {
    const row = list.querySelector('.clip-row[aria-pressed="true"]');
    if (!row) return;
    const item = row.getBoundingClientRect();
    const viewport = list.getBoundingClientRect();
    if (item.top < viewport.top) list.scrollTop += item.top - viewport.top;
    if (item.bottom > viewport.bottom) list.scrollTop += item.bottom - viewport.bottom;
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
    const anchor = [...markers.children].find((marker) => marker.clips === radarSelection);
    for (const marker of markers.children) marker.hidden = Boolean(anchor && marker !== anchor);
    if (!anchor) return;

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
        const line = document.createElement("span");
        line.className = "radar-link";
        line.setAttribute("aria-hidden", "true");
        line.style.setProperty("--tint", tint);
        positionOnRadar(line, position);
        line.style.width = `${Math.hypot(dx, dy)}%`;
        line.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`;

        const point = document.createElement("button");
        point.type = "button";
        point.className = "marker linked-marker";
        point.clip = clip;
        point.style.setProperty("--tint", tint);
        positionOnRadar(point, position);
        const floor = position.lower !== lower ? ` · niveau ${position.lower ? "bas" : "haut"}` : "";
        point.title = `${label} · ${clip.player} · R${clip.round}${floor}`;
        point.setAttribute("aria-label", point.title);
        point.addEventListener("click", () => {
            selectClip(clip, true);
            revealSelectedRow();
        });
        radarRoutes.append(line, point);
    }
}

function renderRadar() {
    const { radar } = currentMatch;
    radarView?.setAvailable(Boolean(radar));
    const point = pointSelect.value;
    const lower = levelSelect.value === "lower";
    markers.replaceChildren();
    byId("radar-empty").hidden = Boolean(radar);
    byId("level-control").hidden = !radar?.lower;
    radarImage.hidden = !radar;
    if (!radar) {
        radarSelection = undefined;
        radarRoutes.replaceChildren();
        return;
    }
    radarImage.src = lower ? radar.lower : radar.upper;
    radarImage.alt = `Radar de ${mapLabel(currentMatch.map)}`;
    const groups = [];
    for (const clip of filtered) {
        const position = radarPosition(clip[point], radar);
        if (!position || position.lower !== lower) continue;
        const { left, top } = position;
        const group = groups.find((item) => Math.hypot(item.left - left, item.top - top) <= groupRadius);
        if (group) group.clips.push(clip);
        else groups.push({ left, top, clips: [clip] });
    }
    if (radarSelection) {
        radarSelection = groups.find(({ clips }) => clips.some((clip) => radarSelection.includes(clip)))?.clips;
    }
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
            const next = radarSelection === clips ? clips[(clips.indexOf(selected) + 1) % clips.length] : clips[0];
            if (radarSelection !== clips) {
                radarSelection = clips;
                renderRadarLinks();
            }
            renderClipList(next, true);
            revealSelectedRow();
            if (matchMedia("(max-width: 780px)").matches) {
                video.closest(".panel").scrollIntoView({ block: "start" });
            }
        });
        markers.append(marker);
    }
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
        if (badge.isConnected) badge.hidden = true;
        if (currentMatch?.clips.includes(clip) && (statusFilter === "unwatched" || !badge.isConnected)) {
            renderClips(undefined, selected);
            syncAddress(true);
        }
        showClipStateError(error);
        return;
    }
    if (statusFilter === "unwatched" && filtered.includes(clip)) {
        const index = visible.indexOf(clip);
        const wasFocused = document.activeElement === badge.closest(".clip-row");
        const scroll = list.scrollTop;
        renderClips(undefined, selected);
        syncAddress(true);
        list.scrollTop = scroll;
        if (wasFocused) {
            const next = list.querySelectorAll(".clip-row")[Math.min(index, visible.length - 1)];
            (next ?? unwatchedFilter).focus();
        }
    }
}

function syncFavoriteButton() {
    if (!favoriteButton) return;
    favoriteButton.hidden = !clipStateEnabled || !selected?.exportId;
    if (favoriteButton.hidden) return;
    const favorite = Boolean(selected.favorite);
    favoriteButton.textContent = favorite ? "★" : "☆";
    favoriteButton.setAttribute("aria-pressed", String(favorite));
    favoriteButton.title = `${favorite ? "Retirer des favoris" : "Ajouter aux favoris"} · ${selected.player}`;
    favoriteButton.setAttribute("aria-label", favoriteButton.title);
    favoriteButton.disabled = favoritePending.has(selected);
}

favoriteButton?.addEventListener("click", async () => {
    const clip = selected;
    if (!clip?.exportId || favoritePending.has(clip)) return;
    const favorite = !clip.favorite;
    const wasFocused = document.activeElement === favoriteButton;
    favoritePending.add(clip);
    syncFavoriteButton();
    showClipStateError();
    try {
        await request("/api/clip-state", { ...clipReference(clip), favorite });
        clip.favorite = favorite;
        if (statusFilter === "favorites" && !favorite && filtered.includes(clip)) {
            const scroll = list.scrollTop;
            renderClips();
            syncAddress(true);
            list.scrollTop = scroll;
        } else {
            const row = list.children[visible.indexOf(clip)];
            const badge = row?.querySelector(".favorite-badge");
            if (badge) badge.hidden = !favorite;
        }
    } catch (error) {
        showClipStateError(error);
    } finally {
        favoritePending.delete(clip);
        syncFavoriteButton();
        if (wasFocused && document.activeElement === document.body) {
            (favoriteButton.hidden ? favoritesFilter : favoriteButton).focus();
        }
    }
});

function renderClips(deletion, playingClip, preferred = selected) {
    const frequency = frequencySelect?.value;
    filtered = currentMatch.clips.filter((clip) => (filter === "all" || clip.kind === filter)
        && (!roundSelect.value || String(clip.round) === roundSelect.value)
        && (!frequency || throwFrequency(clip)?.category === frequency)
        && (statusFilter !== "favorites" || clip.favorite)
        && (statusFilter !== "unwatched" || !clip.watched));
    renderRadar();
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
    const fragment = document.createDocumentFragment();
    if ((statusFilter || frequencySelect?.value) && !visible.length) {
        const empty = document.createElement("p");
        empty.textContent = "Aucun clip pour ces filtres.";
        if (statusFilter === "unwatched") empty.textContent = "Aucun clip à traiter pour ces filtres.";
        if (statusFilter === "favorites") empty.textContent = "Aucun favori pour ces filtres.";
        fragment.append(empty);
    }
    visible.forEach((clip, index) => {
        const row = document.createElement("button");
        row.className = "clip-row";
        row.dataset.index = index;
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
                    tags.append(favorite);
                }
                cell.append(tags);
            }
            row.append(cell);
        }
        row.addEventListener("click", () => {
            const { radar } = currentMatch;
            if (radar?.lower) {
                const level = clip[pointSelect.value][2] < radar.thresholdZ ? "lower" : "upper";
                if (level !== levelSelect.value) {
                    levelSelect.value = level;
                    renderRadar();
                }
            }
            selectClip(clip, true);
            if (badge) markWatched(clip, badge);
        });
        fragment.append(row);
    });
    list.replaceChildren(fragment);
    list.scrollTop = 0;
    selectClip(playingClip ?? (visible.includes(preferred) ? preferred : visible[0]), autoplay);
}

function refreshCalloutLabels() {
    renderClipTitle(selected);
    for (const row of list.querySelectorAll(".clip-row")) {
        row.querySelector(".clip-callout").textContent = calloutName(visible[Number(row.dataset.index)], currentMatch);
    }
}

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
    if (currentMatch?.map !== previousMap) radarView?.reset();
    radarSelection = undefined;
    byId("library-content").hidden = !currentMatch;
    byId("library-empty").hidden = Boolean(currentMatch);
    if (!currentMatch) {
        filtered = [];
        visible = [];
        selectClip(undefined);
        return;
    }
    if (library) sessionStorage.setItem("utilityMap", currentMatch.map);
    renderRounds(round);
    levelSelect.value = currentMatch.radar?.lower ? level : "upper";
    renderFilters(fromAddress);
    const group = groupId && currentMatch.clips.find((clip) => clipId(clip) === groupId);
    if (group) radarSelection = [group];
    const clip = selectedId && currentMatch.clips.find((item) => clipId(item) === selectedId);
    // A clip just marked as seen can still be open in the player under À traiter.
    if (clip && statusFilter === "unwatched" && clip.watched) renderClips(undefined, clip);
    else renderClips(undefined, undefined, fromAddress ? clip || null : selected);
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
favoritesFilter.hidden = !clipStateEnabled;
if (unwatchedFilter) unwatchedFilter.hidden = !clipStateEnabled;
if (frequencySelect && library) {
    byId("frequency-control").hidden = false;
    for (const [value, label] of Object.entries(frequencyLabels)) addOption(frequencySelect, value, label);
    frequencySelect.addEventListener("change", () => {
        renderClips();
        syncAddress();
    });
}
for (const [button, status] of [[favoritesFilter, "favorites"], [unwatchedFilter, "unwatched"]]) {
    button?.addEventListener("click", () => {
        statusFilter = statusFilter === status ? "" : status;
        showStatusFilter();
        renderClips();
        syncAddress();
    });
}

function restoreLibraryAddress() {
    const params = new URLSearchParams(navigation ? navigation.search() : location.search);
    const exportId = params.get("export");
    const recent = exportId && catalogue.matches.find((match) => match.clips.some((clip) => clip.exportId === exportId));
    updateMapOptions(params.get("map") ?? recent?.map ?? sessionStorage.getItem("utilityMap"));
    const type = params.get("type");
    filter = Object.hasOwn(kinds, type) ? type : "all";
    const status = params.get("status");
    statusFilter = clipStateEnabled && ["favorites", "unwatched"].includes(status) ? status : "";
    const frequency = params.get("frequency");
    if (frequencySelect) frequencySelect.value = Object.hasOwn(frequencyLabels, frequency) ? frequency : "";
    showStatusFilter();
    pointSelect.value = params.get("points") === "launch" ? "launch" : "impact";
    const round = params.get("round");
    switchMatch({
        round: /^[1-9]\d{0,3}$/.test(round) ? round : "", level: params.get("floor") === "lower" ? "lower" : "upper",
        groupId: params.get("group"), selectedId: params.get("clip"), fromAddress: true,
    });
    syncAddress(true);
}

if (library) {
    document.body.classList.add("library-view");
    document.querySelector("h1").textContent = "Vidéothèque";
    byId("match-label").textContent = "Carte";
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
roundSelect.addEventListener("change", () => {
    renderClips();
    syncAddress();
});
for (const control of [pointSelect, levelSelect]) {
    control.addEventListener("change", () => {
        radarSelection = undefined;
        renderClips();
        syncAddress();
    });
}
byId("radar").addEventListener("click", (event) => {
    if (radarSelection && !event.target.closest(".marker, .radar-zoom")) {
        radarSelection = undefined;
        renderRadarLinks();
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
        map.clips = map.clips.filter((item) => item !== clip);
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
