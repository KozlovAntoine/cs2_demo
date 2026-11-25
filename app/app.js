"use strict";

const byId = (id) => document.getElementById(id);
const choose = byId("choose");
const submit = byId("submit");
const demo = byId("demo");
const limit = byId("limit");
const captureMode = byId("capture-mode");
const scoreThreshold = byId("score-threshold");
const importMode = byId("import-mode");
const keepDemo = byId("keep-demo");
const controls = byId("controls");
const gallery = byId("gallery");
const home = byId("home");
const quit = byId("quit");
let selected = null;
let awaitingJob = null;
let libraryAvailable = null;
let working = false;
let picking = false;
let closed = false;
let galleryPath;
let lastLibrarySearch = "";
let lastPlayerSearch = "view=players";
let lastReplaySearch = "view=replay";
let openCalloutsAfterLoad = false;
const ESTIMATED_SECONDS_PER_CLIP = 13;
let estimatedJobId;
let clipFinishEstimates = {};
let clipEstimatePauses = {};
let displayedJob;
let jobControlPending = false;
let queueJobId;
const queueLabels = {
    queued: "En attente", analysis: "Analyse en cours", starting: "Lancement de CS2", capture: "Capture en cours",
    generating: "Génération en cours", publishing: "Ajout à la galerie", complete: "Terminée", partial: "Rendu incomplet",
    skipped: "Sans nouveau clip", cancelled: "Annulée", error: "En erreur", paused: "En pause", cancelling: "Annulation en cours",
};
const activeQueueStates = new Set(["analysis", "starting", "capture", "generating", "publishing"]);
const loading = byId("app-loading");
const loadingLabels = {
    "/library/index.html": "Chargement de la vidéothèque…", "/players/index.html": "Chargement des joueurs…",
    "/replay/index.html": "Chargement des replays…", "/compilations/index.html": "Chargement des compilations…",
};
// The library builds its catalogue on the first visit. Explain the wait only when it lasts.
const slowLoadingTimer = setTimeout(() => { byId("loading-detail").hidden = false; }, 4000);
let frameReadyTimer;

function waitForFrame(label) {
    byId("loading-label").textContent = label;
    // The frame's load event also waits for clip thumbnails. Its page is usable once its deferred scripts have run.
    clearInterval(frameReadyTimer);
    frameReadyTimer = setInterval(() => {
        const page = gallery.contentDocument;
        if (!page || page.URL === "about:blank" || new URL(page.URL).pathname !== galleryPath) return;
        if (page.defaultView?.performance.getEntriesByType("navigation")[0]?.domContentLoadedEventEnd > 0) finishLoading();
    }, 50);
}

function finishLoading() {
    if (loading.classList.contains("is-done")) return;
    clearTimeout(slowLoadingTimer);
    clearInterval(frameReadyTimer);
    loading.classList.add("is-done");
    loading.parentElement.removeAttribute("aria-busy");
    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => { loading.hidden = true; }, reducedMotion ? 0 : 240);
}

async function request(path, payload) {
    const options = payload === undefined ? undefined : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    };
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "La requête a échoué.");
    return data;
}

function setNotification(message = "") {
    const element = byId("notification");
    element.hidden = !message;
    element.textContent = message;
}

function refreshControls() {
    controls.disabled = working || picking;
    submit.disabled = !selected || working || picking;
    quit.disabled = working || picking;
    choose.textContent = picking ? "Sélection en cours…" : "Parcourir…";
}

function importedLabel(demos) {
    const count = demos.filter((entry) => entry.imported).length;
    if (!count) return "";
    return count === demos.length ? "Déjà importé" : `Déjà importé · ${count}/${demos.length}`;
}

function updateSelectionBadge() {
    const sources = selected?.sources ?? [];
    const entries = sources.length === 1 ? sources[0].demos : [];
    const label = importedLabel(demo.value ? entries.filter((entry) => entry.member === demo.value) : entries);
    const badge = byId("source-imported");
    badge.textContent = label;
    badge.hidden = !label || importMode.value === "replay";
}

function selectSource(selection) {
    const sameSelection = selected?.id === selection?.id;
    if (sameSelection && selected?.revision === selection?.revision) return;
    const previousMember = sameSelection ? demo.value : "";
    selected = selection;
    const sources = selected?.sources ?? [];
    const multiple = sources.length > 1;
    const first = sources[0];
    byId("chosen").textContent = multiple ? `${sources.length} fichiers sélectionnés` : first?.name ?? "Aucun fichier sélectionné";
    const files = byId("source-files");
    files.hidden = !multiple;
    const items = multiple ? sources.map((source) => {
        const item = document.createElement("li");
        item.textContent = source.name;
        const label = importedLabel(source.demos);
        if (label) {
            const badge = document.createElement("span");
            badge.className = "imported-badge";
            badge.textContent = label;
            item.append(" ", badge);
        }
        return item;
    }) : [];
    files.replaceChildren(...items);
    const members = multiple ? [] : (first?.demos ?? []).filter((entry) => entry.member !== null);
    demo.replaceChildren(new Option("Toutes les démos", ""));
    for (const { member, imported } of members) {
        demo.add(new Option(imported ? `${member} · Déjà importé` : member, member));
    }
    demo.value = previousMember;
    byId("demo-field").hidden = members.length < 2;
    updateSelectionBadge();
    refreshControls();
}

demo.addEventListener("change", updateSelectionBadge);

function estimatedJobMessages(job) {
    const storageKey = "utilityClipFinishEstimates";
    if (estimatedJobId !== job.id) {
        estimatedJobId = job.id;
        clipFinishEstimates = {};
        clipEstimatePauses = {};
        try {
            const saved = JSON.parse(localStorage.getItem(storageKey));
            if (saved?.jobId === job.id && saved.secondsPerClip === ESTIMATED_SECONDS_PER_CLIP
                && saved.finishes && typeof saved.finishes === "object") {
                clipFinishEstimates = saved.finishes;
                clipEstimatePauses = saved.pauses ?? {};
            }
        } catch { }
    }
    const messages = job.messages ?? [];
    const countPattern = /\b(?:de|cs|ar)_\w+ : (\d+) clips? \(/;
    const analysis = messages.findLast((message) => countPattern.test(message));
    const pausedMs = job.pausedMs ?? 0;
    let changed = false;
    if (analysis && job.state === "running" && !Number.isFinite(clipFinishEstimates[analysis])) {
        const count = Number(analysis.match(countPattern)[1]);
        clipFinishEstimates[analysis] = Date.now() + count * ESTIMATED_SECONDS_PER_CLIP * 1000;
        clipEstimatePauses[analysis] = pausedMs;
        changed = true;
    }
    if (analysis && job.state === "running" && Number.isFinite(clipFinishEstimates[analysis])) {
        const pauseDelta = Math.max(0, pausedMs - (clipEstimatePauses[analysis] ?? 0));
        clipFinishEstimates[analysis] += pauseDelta;
        clipEstimatePauses[analysis] = pausedMs;
        changed ||= pauseDelta > 0;
    }
    if (changed) {
        try {
            localStorage.setItem(storageKey, JSON.stringify({ jobId: job.id, secondsPerClip: ESTIMATED_SECONDS_PER_CLIP,
                finishes: clipFinishEstimates, pauses: clipEstimatePauses }));
        } catch { }
    }
    if (job.paused || job.cancelRequested) return messages;
    let captureFinish;
    return messages.map((message) => {
        if (countPattern.test(message)) captureFinish = clipFinishEstimates[message];
        const finish = clipFinishEstimates[message] ?? (message.includes("CS2 enregistre ") ? captureFinish : undefined);
        if (!Number.isFinite(finish)) return message;
        const time = new Date(finish).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
        return `${message} Fin estimée : ${time}`;
    });
}

function displayQueue(job, messages) {
    const rows = job.queue ?? [];
    const body = byId("queue-rows");
    const actions = byId("job-controls");
    if (queueJobId !== job.id) {
        byId("legacy-job-actions").append(actions);
        body.replaceChildren();
        queueJobId = job.id;
    }
    byId("queue-table").hidden = byId("queue-summary").hidden = rows.length === 0;
    if (!rows.length) return;

    const active = rows.find((row) => activeQueueStates.has(row.state)) ?? rows.find((row) => row.state === "queued");
    const captureEstimate = messages.findLast((message) => message.includes("CS2 enregistre "))?.match(/Fin estimée : .+$/)?.[0];
    const summary = new Map();
    for (const [index, row] of rows.entries()) {
        if (!body.children[index]) body.append(byId("queue-row-template").content.cloneNode(true));
        const element = body.children[index];
        const current = row === active && job.state === "running";
        const state = current && job.cancelRequested ? "cancelling" : current && job.paused ? "paused" : row.state;
        element.dataset.state = state;
        element.querySelector(".queue-source strong").textContent = row.name;
        const sourceDetails = element.querySelector(".queue-source small");
        sourceDetails.textContent = row.map || row.archive || "";
        sourceDetails.title = row.archive || "";
        element.querySelector(".queue-state span").textContent = queueLabels[state];

        const count = (value) => row.total ? `${value ?? "—"} / ${row.total}` : "—";
        const [capture, generation] = element.querySelectorAll(".queue-clips span");
        capture.textContent = job.mode === "replay" ? "Replay" : `Capture ${count(row.captured)}`;
        generation.textContent = job.mode === "replay" ? (row.state === "complete" ? "Prêt" : "—")
            : `Génération ${count(row.generated)}`;

        const recording = ["starting", "capture"].includes(row.state);
        const done = recording ? row.captured : row.generated;
        const percent = row.total && Number.isInteger(done)
            ? (done >= row.total ? 100 : Math.min(99, Math.round(100 * done / row.total))) : null;
        const bar = element.querySelector("progress");
        bar.value = percent ?? 0;
        bar.hidden = percent === null;
        bar.setAttribute("aria-label", `${recording ? "Capture" : "Génération"} · ${row.name}`);
        element.querySelector(".queue-progress span").textContent = percent === null ? "—" : `${percent} %`;
        const duplicates = row.duplicates ? `${row.duplicates} ${row.duplicates === 1 ? "doublon ignoré" : "doublons ignorés"}` : "";
        const published = row.published === undefined ? "" : `${row.published} ${row.published === 1 ? "clip disponible" : "clips disponibles"}`;
        const estimate = current && recording && !job.cancelRequested ? captureEstimate : "";
        element.querySelector(".queue-progress small").textContent = [row.note, published, duplicates, estimate].filter(Boolean).join(" · ");

        const cell = element.querySelector(".queue-actions");
        const link = cell.querySelector("a");
        link.hidden = !row.url;
        if (row.url) {
            link.href = `/?${new URLSearchParams({ gallery: row.url.split("/")[2] })}`;
            link.textContent = "Voir la galerie ↗";
        }
        if (current && actions.parentElement !== cell) cell.append(actions);

        const category = activeQueueStates.has(state) || state === "cancelling" ? "running" : state;
        summary.set(category, (summary.get(category) ?? 0) + 1);
    }
    if (!active || job.state !== "running") byId("legacy-job-actions").append(actions);
    byId("queue-summary").textContent = [
        ["running", "en cours"], ["queued", "en attente"], ["paused", "en pause"],
        ["complete", "terminée", "terminées"], ["partial", "incomplète", "incomplètes"], ["skipped", "sans nouveau clip"],
        ["error", "en erreur"], ["cancelled", "annulée", "annulées"],
    ].filter(([key]) => summary.has(key)).map(([key, singular, plural = singular]) => {
        const count = summary.get(key);
        return `${count} ${count === 1 ? singular : plural}`;
    }).join(" · ");
}

function displayJob(job) {
    displayedJob = job;
    const running = job.state === "running";
    working = running;
    byId("working").hidden = !running;
    const activeRow = job.queue?.find((row) => activeQueueStates.has(row.state));
    const livePhase = ["starting", "capture", "generating", "publishing"].includes(activeRow?.state)
        ? queueLabels[activeRow.state] : "";
    const stateLabel = job.cancelRequested ? "Annulation en cours" : job.paused ? "En pause"
        : job.pauseRequested ? "Pause demandée" : running ? livePhase || `${job.phase} en cours` : "";
    byId("working").textContent = stateLabel;
    byId("working").title = stateLabel;
    byId("status").hidden = job.state === "idle";
    const hasQueue = Boolean(job.queue?.length);
    document.querySelector(".status-heading").hidden = hasQueue && job.state !== "error";
    byId("spinner").hidden = !running || job.paused;
    const phase = job.state === "error" ? "Export interrompu"
        : job.cancelRequested || job.paused || job.pauseRequested ? stateLabel : job.phase ?? "";
    if (byId("phase").textContent !== phase) byId("phase").textContent = phase;
    const messages = estimatedJobMessages(job);
    byId("latest").hidden = hasQueue && job.state !== "error" && (running || !job.failedFiles);
    byId("latest").textContent = job.error ?? messages.at(-1) ?? "";
    byId("latest").classList.toggle("error", job.state === "error");
    if (byId("messages").dataset.snapshot !== messages.join("\n")) {
        byId("messages").dataset.snapshot = messages.join("\n");
        byId("messages").replaceChildren(...messages.map((message) => {
            const item = document.createElement("li");
            item.textContent = message;
            return item;
        }));
    }
    byId("log").hidden = messages.length < 2;
    displayQueue(job, messages);
    byId("job-controls").hidden = !running || !job.controllable;
    byId("pause-job").textContent = job.paused || job.pauseRequested ? "Reprendre" : "Pause";
    byId("cancel-job").textContent = hasQueue ? "Annuler" : "Annuler le traitement";
    byId("pause-job").disabled = jobControlPending || Boolean(job.cancelRequested);
    byId("cancel-job").disabled = jobControlPending || Boolean(job.cancelRequested);
    byId("job-control-hint").hidden = !running || !job.controllable;
    byId("job-control-hint").textContent = job.cancelRequested ? "Arrêt et récupération des clips finalisés…"
        : job.paused ? "Clique sur Reprendre pour continuer."
        : job.pauseRequested ? "La pause prendra effet à la fin de la démo en cours."
        : "La pause prend effet entre deux démos.";
    refreshControls();
}

for (const id of ["pause-job", "cancel-job"]) {
    byId(id).addEventListener("click", async () => {
        const job = displayedJob;
        if (!job?.controllable || job.state !== "running" || jobControlPending) return;
        const action = id === "cancel-job" ? "cancel" : job.paused || job.pauseRequested ? "resume" : "pause";
        jobControlPending = true;
        displayJob(job);
        try {
            const result = await request("/api/job-control", { jobId: job.id, action });
            displayJob(result.job);
        } catch (error) {
            setNotification(error.message);
        } finally {
            jobControlPending = false;
            displayJob(displayedJob);
        }
    });
}

function route() {
    const params = new URLSearchParams(location.search);
    if (params.get("view") === "players") return "/players/index.html";
    if (params.get("view") === "replay") return "/replay/index.html";
    if (params.get("view") === "compilations") return "/compilations/index.html";
    const galleryId = params.get("gallery");
    const exportPath = galleryId && /^[\w-]+$/.test(galleryId) ? `/exports/${galleryId}/index.html` : null;
    if (["export", "galleries"].includes(params.get("view")) || (!exportPath && libraryAvailable === false)) return null;
    return exportPath ?? "/library/index.html";
}

function setAddress(search, replace = false) {
    const url = search ? `/?${search}` : "/";
    if (location.pathname + location.search !== url) {
        if (replace) history.replaceState(null, "", url);
        else history.pushState(null, "", url);
    }
}

window.utilityNavigation = {
    search: () => location.search,
    openLibrary: () => openLibrary(),
    openCompilations() {
        setAddress("view=compilations");
        renderRoute(true);
    },
    updateReplay(search, replace) {
        if (closed || route() !== "/replay/index.html") return;
        const params = new URLSearchParams(search);
        params.set("view", "replay");
        lastReplaySearch = params.toString();
        setAddress(lastReplaySearch, replace);
    },
    openImport(mode) {
        importMode.value = mode === "replay" ? "replay" : "clips";
        applyImportMode();
        setAddress("view=export");
        renderRoute(true);
    },
    update(search, replace) {
        if (closed) return;
        const path = route();
        if (path === "/players/index.html") {
            const params = new URLSearchParams(search);
            params.set("view", "players");
            lastPlayerSearch = params.toString();
            setAddress(lastPlayerSearch, replace);
            return;
        }
        if (path !== "/library/index.html") return;
        lastLibrarySearch = search;
        setAddress(search, replace);
        updateNavigation("library");
    },
};

function renderRoute(focus = false, refresh = false) {
    if (closed) return;
    const path = route();
    if (!path) {
        showHome(focus);
        loadGalleries().catch((error) => setNotification(error.message));
        return;
    }
    const library = path.startsWith("/library/");
    const players = path === "/players/index.html";
    const replays = path === "/replay/index.html";
    const compilations = path === "/compilations/index.html";
    if (library) lastLibrarySearch = location.search.slice(1);
    if (players) lastPlayerSearch = location.search.slice(1);
    if (replays) lastReplaySearch = location.search.slice(1);
    gallery.title = players ? "Carte des positions des joueurs" : replays ? "Replays des rounds"
        : compilations ? "Compilations 9:16" : "Radar et clips";
    byId("gallery-view").setAttribute("aria-label",
        players ? "Positions des joueurs" : replays ? "Replays" : compilations ? "Compilations" : "Galerie de clips");
    if (galleryPath !== path || refresh) {
        galleryPath = path;
        if (!loading.classList.contains("is-done")) waitForFrame(loadingLabels[path] ?? "Chargement de la galerie…");
        // The top-level URL owns history. Replacing the frame avoids extra Back steps.
        gallery.contentWindow.location.replace(path);
    } else if (library) {
        gallery.contentWindow.restoreLibraryAddress?.();
    } else if (players) {
        gallery.contentWindow.restorePlayerAddress?.();
    } else if (replays) {
        gallery.contentWindow.restoreReplayAddress?.();
    }
    home.hidden = true;
    byId("gallery-view").hidden = false;
    const section = players ? "players" : replays ? "replays" : compilations ? "compilations" : library ? "library" : "gallery";
    updateNavigation(section);
    if (focus) byId(`${section === "gallery" ? "library" : section}-link`).focus();
}

function openLibrary(exportId, focus = true) {
    if (!libraryAvailable) {
        showHome(focus);
        loadGalleries().catch((error) => setNotification(error.message));
        return;
    }
    const query = exportId ? new URLSearchParams({ export: exportId }).toString() : lastLibrarySearch;
    setAddress(query);
    renderRoute(focus, true);
}

function openGallery(url) {
    const galleryId = url.split("/")[2];
    setAddress(new URLSearchParams({ gallery: galleryId }).toString());
    renderRoute(true);
}

function showHome(focus = true) {
    finishLoading();
    openCalloutsAfterLoad = false;
    byId("gallery-view").hidden = true;
    home.hidden = false;
    const params = new URLSearchParams(location.search);
    const section = params.get("view") === "galleries" ? "galleries" : "export";
    if (params.get("mode") === "replay" && importMode.value !== "replay") {
        importMode.value = "replay";
        applyImportMode();
    }
    updateNavigation(section);
    if (focus && section === "export" && !controls.disabled) choose.focus();
}

function updateNavigation(section) {
    document.body.dataset.view = section;
    const active = {library: "library-link", players: "players-link", replays: "replays-link",
        compilations: "compilations-link", export: "back", galleries: "galleries-link", gallery: "galleries-link"}[section];
    for (const id of ["library-link", "players-link", "replays-link", "compilations-link", "back", "galleries-link"]) {
        if (id === active) byId(id).setAttribute("aria-current", "page");
        else byId(id).removeAttribute("aria-current");
    }
    byId("route-label").textContent = {library: "Vidéothèque", players: "Joueurs", replays: "Replays",
        compilations: "Compilations", export: "Importation",
        galleries: "Galeries", gallery: "Galerie"}[section];
    byId("route-action").textContent = section === "export" ? "Ouvrir la vidéothèque" : "Importer une démo";
    byId("route-action").disabled = section === "export" && !libraryAvailable;
    byId("callouts-link").disabled = !libraryAvailable;
}

function openCallouts() {
    if (route() === "/library/index.html" && !byId("gallery-view").hidden) {
        const button = gallery.contentDocument?.getElementById("edit-callouts");
        if (button && !button.hidden) {
            button.click();
            return;
        }
    }
    openCalloutsAfterLoad = true;
    openLibrary(undefined, false);
}

function mapName(map) {
    const name = map.replace(/^(de|cs|ar)_/, "").replaceAll("_", " ");
    return name.charAt(0).toUpperCase() + name.slice(1);
}

async function loadGalleries() {
    const { galleries } = await request("/api/galleries");
    byId("history-empty").hidden = galleries.length > 0;
    byId("gallery-count").textContent = galleries.length || "";
    byId("galleries").replaceChildren(...galleries.map((item) => {
        const row = document.createElement("a");
        row.href = item.url;
        row.className = "gallery-row";
        const name = document.createElement("strong");
        name.textContent = item.title;
        const info = document.createElement("small");
        info.textContent = `${item.maps.map(mapName).join(", ")} · ${item.count} ${item.count === 1 ? "clip" : "clips"}`;
        const link = document.createElement("span");
        link.textContent = "Ouvrir";
        row.append(name, info, link);
        row.addEventListener("click", (event) => {
            if (event.ctrlKey || event.shiftKey || event.metaKey || event.altKey) return;
            event.preventDefault();
            openGallery(item.url);
        });
        return row;
    }));
}

choose.addEventListener("click", async () => {
    setNotification();
    picking = true;
    refreshControls();
    try {
        const { selection } = await request("/api/select", {});
        selectSource(selection);
    } catch (error) {
        setNotification(error.message);
    } finally {
        picking = false;
        refreshControls();
        choose.focus();
    }
});

byId("generate").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selected || working || picking) return;
    setNotification();
    working = true;
    refreshControls();
    try {
        const { job } = await request("/api/generate", {
            selection: selected.id, kind: byId("kind").value, demo: demo.value || null,
            mode: importMode.value, keepDemo: keepDemo.checked,
            topCount: captureMode.value === "top" ? byId("top-count").valueAsNumber : null,
            topMixed: captureMode.value === "top" && byId("top-mixed").checked,
            enemyPov: byId("enemy-pov").checked, captureHeight: Number(byId("capture-height").value),
            limit: limit.value ? Number(limit.value) : null, captureMode: captureMode.value,
            scoreThreshold: scoreThreshold.disabled || !scoreThreshold.value ? null : scoreThreshold.valueAsNumber,
        });
        awaitingJob = job.id;
        displayJob(job);
    } catch (error) {
        working = false;
        refreshControls();
        setNotification(error.message);
    }
});
captureMode.addEventListener("change", () => {
    scoreThreshold.disabled = captureMode.value !== "potential";
    byId("top-count-field").hidden = byId("top-mixed-field").hidden = captureMode.value !== "top";
});

function applyImportMode() {
    const replayOnly = importMode.value === "replay";
    byId("clip-settings").hidden = replayOnly;
    submit.textContent = replayOnly ? "Créer les replays →" : "Générer les clips →";
    byId("import-hint").textContent = replayOnly
        ? "Sans CS2 : l’analyse crée le replay 2D de chaque démo. Aucun clip n’est généré et la vidéothèque ne change pas."
        : "Laisse Steam ouvert. CS2 se lance pour chaque démo et se ferme après sa dernière capture.";
    updateSelectionBadge();
}

importMode.addEventListener("change", applyImportMode);

byId("back").addEventListener("click", () => {
    setAddress("view=export");
    renderRoute(true);
});
byId("library-link").addEventListener("click", () => openLibrary());
byId("players-link").addEventListener("click", () => {
    setAddress(lastPlayerSearch);
    renderRoute(true);
});
byId("replays-link").addEventListener("click", () => {
    setAddress(lastReplaySearch);
    renderRoute(true);
});
byId("compilations-link").addEventListener("click", () => window.utilityNavigation.openCompilations());
byId("callouts-link").addEventListener("click", openCallouts);
byId("galleries-link").addEventListener("click", () => {
    setAddress("view=galleries");
    renderRoute(false);
});
byId("route-action").addEventListener("click", () => {
    if (document.body.dataset.view === "export") openLibrary();
    else byId("back").click();
});
window.addEventListener("popstate", () => renderRoute());
document.addEventListener("keydown", (event) => {
    if (galleryPath === "/library/index.html" && !byId("gallery-view").hidden) {
        gallery.contentWindow.handleLibraryShortcut?.(event);
    }
});
gallery.addEventListener("load", () => {
    if (galleryPath && gallery.contentDocument?.URL !== "about:blank") finishLoading();
    if (openCalloutsAfterLoad && galleryPath === "/library/index.html" && !byId("gallery-view").hidden) {
        const button = gallery.contentDocument?.getElementById("edit-callouts");
        if (button && !button.hidden) {
            openCalloutsAfterLoad = false;
            button.click();
        }
    }
    const heading = gallery.contentDocument?.querySelector("main > header h1");
    if (heading && galleryPath?.startsWith("/exports/")) heading.hidden = true;
});
quit.addEventListener("click", async () => {
    try {
        await request("/api/quit", {});
        closed = true;
        showHome();
        home.hidden = true;
        quit.hidden = true;
        document.querySelector(".app-sidebar nav").hidden = true;
        byId("route-action").hidden = true;
        byId("closed").hidden = false;
    } catch (error) {
        showHome();
        setNotification(error.message);
    }
});

async function poll() {
    if (closed) return;
    try {
        const { job, selection, library } = await request("/api/status");
        if (closed) return;
        const firstPoll = libraryAvailable === null;
        libraryAvailable = library === true;
        byId("library-notice").hidden = libraryAvailable;
        selectSource(selection);
        displayJob(job);
        if (job.state === "running" && !job.portrait && !job.compilation) awaitingJob = job.id;
        if (["complete", "error", "cancelled"].includes(job.state) && awaitingJob === job.id) {
            awaitingJob = null;
            if (job.state === "complete" && job.mode === "replay") {
                setAddress("view=replay");
                renderRoute(true, true);
            } else if (job.state === "complete" && job.url && !job.failedFiles) {
                if (libraryAvailable) openLibrary(job.url.split("/")[2]);
                else openGallery(job.url);
            }
            await loadGalleries();
        } else if (firstPoll) {
            setNotification();
            renderRoute();
        }
    } catch (error) {
        if (!closed) {
            if (libraryAvailable === null) showHome(false);
            setNotification(error.message);
        }
    } finally {
        if (!closed) setTimeout(poll, 1000);
    }
}

poll();
