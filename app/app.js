"use strict";

const byId = (id) => document.getElementById(id);
const choose = byId("choose");
const submit = byId("submit");
const demo = byId("demo");
const limit = byId("limit");
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
    badge.hidden = !label;
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

function displayJob(job) {
    const running = job.state === "running";
    working = running;
    byId("working").hidden = !running;
    byId("working").textContent = running ? `${job.phase} en cours` : "";
    byId("status").hidden = job.state === "idle";
    byId("spinner").hidden = !running;
    const phase = job.state === "error" ? "Export interrompu" : job.phase ?? "";
    if (byId("phase").textContent !== phase) byId("phase").textContent = phase;
    const messages = job.messages ?? [];
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
    refreshControls();
}

function route() {
    const params = new URLSearchParams(location.search);
    const galleryId = params.get("gallery");
    const exportPath = galleryId && /^[\w-]+$/.test(galleryId) ? `/exports/${galleryId}/index.html` : null;
    if (params.get("view") === "export" || (!exportPath && libraryAvailable === false)) return null;
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
    update(search, replace) {
        if (route() !== "/library/index.html" || closed) return;
        lastLibrarySearch = search;
        setAddress(search, replace);
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
    if (library) lastLibrarySearch = location.search.slice(1);
    if (galleryPath !== path || refresh) {
        galleryPath = path;
        // The top-level URL owns history. Replacing the frame avoids extra Back steps.
        gallery.contentWindow.location.replace(path);
    } else if (library) {
        gallery.contentWindow.restoreLibraryAddress?.();
    }
    home.hidden = true;
    byId("gallery-view").hidden = false;
    byId("library-link").setAttribute("aria-pressed", String(library));
    byId("back").setAttribute("aria-pressed", "false");
    if (focus) byId("library-link").focus();
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
    byId("gallery-view").hidden = true;
    home.hidden = false;
    byId("library-link").setAttribute("aria-pressed", "false");
    byId("back").setAttribute("aria-pressed", "true");
    if (focus && !controls.disabled) choose.focus();
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
            limit: limit.value ? Number(limit.value) : null,
        });
        awaitingJob = job.id;
        displayJob(job);
    } catch (error) {
        working = false;
        refreshControls();
        setNotification(error.message);
    }
});

byId("back").addEventListener("click", () => {
    setAddress("view=export");
    renderRoute(true);
});
byId("library-link").addEventListener("click", () => openLibrary());
window.addEventListener("popstate", () => renderRoute());
document.addEventListener("keydown", (event) => {
    if (galleryPath === "/library/index.html" && !byId("gallery-view").hidden) {
        gallery.contentWindow.handleLibraryShortcut?.(event);
    }
});
gallery.addEventListener("load", () => {
    const heading = gallery.contentDocument?.querySelector("main > header h1");
    if (heading && !galleryPath?.startsWith("/library/")) heading.hidden = true;
});
quit.addEventListener("click", async () => {
    try {
        await request("/api/quit", {});
        closed = true;
        showHome();
        home.hidden = true;
        quit.hidden = true;
        byId("library-link").hidden = true;
        byId("back").hidden = true;
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
        if (job.state === "running" && !job.portrait) awaitingJob = job.id;
        if ((job.state === "complete" || job.state === "error") && awaitingJob === job.id) {
            awaitingJob = null;
            if (job.url && !job.failedFiles) {
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
