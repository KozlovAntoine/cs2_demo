"use strict";

// The library catalogue is slow to build: the history does not wait for it.
let catalogue;
const navigation = window.parent !== window ? window.parent.utilityNavigation : undefined;
const KIND_LABELS = { smoke: "Smoke", flash: "Flash", molo: "Molotov", he: "HE" };
const KIND_COLORS = { smoke: "#c4cad4", flash: "#ebcb52", molo: "#ee7b4c", he: "#d9719e" };
const PLATFORM_LABELS = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram" };
const PLATFORM_HINTS = {
    youtube: "https://youtube.com/shorts/…", tiktok: "https://www.tiktok.com/@…/video/…",
    instagram: "https://www.instagram.com/reel/…",
};
// Posted by hand: the button copies the post text, opens the upload page and shows the file in Explorer.
const UPLOAD_PAGES = {
    tiktok: "https://www.tiktok.com/tiktokstudio/upload",
    instagram: "https://www.instagram.com/",
};

const byId = (id) => document.getElementById(id);
let state = loadCompilationState();
let compilations = [];
let jobTimer;
let wasRunning = false;
let lastFile;
let youtube;
let youtubeTimer;
const cards = new Map();

async function request(path, payload) {
    const options = payload === undefined ? undefined : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    };
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "La requête a échoué.");
    return data;
}

function formatDuration(seconds) {
    if (typeof seconds !== "number") return "";
    const total = Math.round(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function formatSize(bytes) {
    return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} Go` : `${Math.max(1, Math.round(bytes / 1e6))} Mo`;
}

function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("fr-FR", {
        day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
}

function mediaUrl(file, suffix) {
    return `/compilations/media/${encodeURIComponent(file.replace(/\.mp4$/, suffix))}`;
}

/* ---------- Nouvelle compilation ---------- */

function entries() {
    return compilationEntries(catalogue, state);
}

function persist() {
    saveCompilationState(state);
}

function renderDescription(list) {
    const field = byId("comp-description");
    const text = compilationText(state, list);
    if (field.value !== text) field.value = text;
    byId("comp-description-length").textContent = `${field.value.length} / ${COMPILATION_DESCRIPTION_MAX} caractères`;
    byId("comp-description-reset").disabled = !state.descriptionEdited;
}

function renderDraft() {
    if (!catalogue) {
        byId("comp-draft-empty").hidden = false;
        byId("comp-draft-empty").textContent = "Chargement de la sélection…";
        byId("comp-draft-body").hidden = true;
        byId("comp-clear").hidden = true;
        return;
    }
    const list = entries();
    // Clips deleted from the library leave the selection.
    state.ids = list.map(({ clip }) => compilationClipId(clip));
    renderDescription(list);
    persist();
    byId("comp-draft-count").textContent = list.length ? `${list.length} ${list.length === 1 ? "clip" : "clips"}` : "";
    byId("comp-draft-empty").hidden = list.length > 0;
    byId("comp-draft-body").hidden = list.length === 0;
    byId("comp-clear").hidden = list.length === 0;
    byId("comp-draft-list").replaceChildren(...list.map(({ clip, match }, index) => {
        const item = document.createElement("li");
        item.style.setProperty("--tint", KIND_COLORS[clip.kind] ?? "");
        const image = document.createElement("img");
        image.alt = "";
        image.loading = "lazy";
        const thumbnail = clip.video?.replace(/clips\/([\w-]+)\.mp4$/, "thumbs/$1.jpg");
        if (thumbnail && thumbnail !== clip.video) image.src = thumbnail;
        const text = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = compilationCallout(clip, match) || "Zone à définir";
        const details = document.createElement("small");
        details.textContent = [compilationMapLabel(match.map), KIND_LABELS[clip.kind], clip.player, clip.team]
            .filter(Boolean).join(" · ");
        text.append(title, details);
        const controls = document.createElement("span");
        controls.className = "comp-item-actions";
        for (const [label, name, action, disabled] of [
            ["↑", "Monter", () => move(index, -1), index === 0],
            ["↓", "Descendre", () => move(index, 1), index === list.length - 1],
            ["✕", "Retirer", () => { state.ids.splice(index, 1); renderDraft(); }, false],
        ]) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "zone-button";
            button.textContent = label;
            button.title = `${name} · ${clip.player}`;
            button.setAttribute("aria-label", button.title);
            button.disabled = disabled;
            button.addEventListener("click", action);
            controls.append(button);
        }
        item.append(image, text, controls);
        return item;
    }));
    const start = byId("comp-start");
    const tooMany = list.length > COMPILATION_MAX_CLIPS;
    start.disabled = !list.length || tooMany || start.dataset.busy === "true";
    start.textContent = list.length ? `Générer la compilation (${list.length})` : "Générer la compilation";
    if (tooMany) showStatus(`${COMPILATION_MAX_CLIPS} clips au maximum par compilation.`, true);
}

function move(index, offset) {
    const target = index + offset;
    if (target < 0 || target >= state.ids.length) return;
    [state.ids[index], state.ids[target]] = [state.ids[target], state.ids[index]];
    renderDraft();
}

function showStatus(text, error = false) {
    const status = byId("comp-status");
    status.textContent = text;
    status.classList.toggle("is-error", error);
}

function showJob(job) {
    const start = byId("comp-start");
    start.dataset.busy = String(Boolean(job?.busy));
    if (job?.error) showStatus(job.error, true);
    else if (job?.running) showStatus(job.phase || "Compilation en cours…");
    else if (job?.busy) showStatus("Un autre traitement est en cours (import ou rendu).");
    else if (job?.file) showStatus(`Terminé · ${job.file}`);
    else showStatus("");
    renderDraft();
    clearTimeout(jobTimer);
    if (job?.running || job?.busy) jobTimer = setTimeout(refreshJob, 1500);
    // A compilation just finished: show it at the top of the history.
    if (wasRunning && !job?.running && job?.file) {
        lastFile = job.file;
        loadHistory();
    }
    wasRunning = Boolean(job?.running);
}

async function refreshJob() {
    try {
        showJob(await request("/api/compilation"));
    } catch (error) {
        showStatus(error.message, true);
    }
}

byId("comp-pick").addEventListener("click", () => {
    state.active = true;
    persist();
    if (navigation?.openLibrary) navigation.openLibrary();
    else location.href = "/library/index.html";
});
byId("comp-clear").addEventListener("click", () => {
    state.ids = [];
    state.descriptionEdited = false;
    renderDraft();
});
byId("comp-description").addEventListener("input", (event) => {
    state.description = event.target.value;
    state.descriptionEdited = true;
    renderDescription(entries());
    persist();
});
byId("comp-description-reset").addEventListener("click", () => {
    state.descriptionEdited = false;
    renderDraft();
});
byId("comp-description-copy").addEventListener("click", async (event) => {
    const field = byId("comp-description");
    if (!await copyCompilationText(field.value, field)) return;
    event.target.textContent = "Copié";
    setTimeout(() => { event.target.textContent = "Copier"; }, 1500);
});
byId("comp-start").addEventListener("click", async () => {
    const payload = compilationRequest(entries(), byId("comp-description").value);
    byId("comp-start").dataset.busy = "true";
    showStatus("Démarrage…");
    renderDraft();
    try {
        await request("/api/compilation", payload);
    } catch (error) {
        byId("comp-start").dataset.busy = "false";
        showStatus(error.message, true);
        renderDraft();
        return;
    }
    wasRunning = true;
    refreshJob();
});
// The library may change the selection in another tab.
window.addEventListener("storage", (event) => {
    if (event.key !== COMPILATION_STORAGE_KEY) return;
    state = loadCompilationState();
    renderDraft();
});

/* ---------- Historique ---------- */

function cardTitle(item) {
    const first = item.description.split("\n").find((line) => line.trim());
    return first?.trim() || item.file.replace(/\.mp4$/, "");
}

function linkRow(item, platform, card) {
    const row = document.createElement("li");
    row.className = "comp-link";
    const name = document.createElement("span");
    name.className = "comp-link-name";
    name.textContent = PLATFORM_LABELS[platform];
    row.append(name);
    const link = item.links[platform];
    const edit = (value) => {
        const input = document.createElement("input");
        input.type = "url";
        input.value = value ?? "";
        input.placeholder = PLATFORM_HINTS[platform];
        input.setAttribute("aria-label", `Lien ${PLATFORM_LABELS[platform]} · ${cardTitle(item)}`);
        const save = document.createElement("button");
        save.type = "button";
        save.className = "zone-button comp-small";
        save.textContent = "OK";
        const submit = () => saveLinks(item, { [platform]: input.value }, card);
        save.addEventListener("click", submit);
        input.addEventListener("keydown", (event) => {
            if (event.key === "Enter") submit();
            if (event.key === "Escape") renderCard(card, item);
        });
        row.replaceChildren(name, input, save);
        input.focus();
    };
    if (link) {
        const anchor = document.createElement("a");
        anchor.href = link;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.textContent = link.replace(/^https:\/\/(www\.)?/, "");
        anchor.title = link;
        const change = document.createElement("button");
        change.type = "button";
        change.className = "zone-button comp-small";
        change.textContent = "Modifier";
        change.setAttribute("aria-label", `Modifier le lien ${PLATFORM_LABELS[platform]}`);
        change.addEventListener("click", () => edit(link));
        row.append(anchor, change);
    } else {
        const missing = document.createElement("span");
        missing.className = "comp-link-missing";
        missing.textContent = "Non publié";
        const add = document.createElement("button");
        add.type = "button";
        add.className = "zone-button comp-small";
        add.textContent = "+ Lien";
        add.setAttribute("aria-label", `Ajouter le lien ${PLATFORM_LABELS[platform]}`);
        add.addEventListener("click", () => edit(""));
        row.append(missing);
        if (UPLOAD_PAGES[platform]) row.append(publishButton(item, platform, card));
        row.append(add);
    }
    return row;
}

function publishButton(item, platform, card) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zone-button comp-small comp-publish";
    button.textContent = "Publier ↗";
    button.title = `Copie le texte, ouvre ${PLATFORM_LABELS[platform]} et affiche la vidéo dans l’Explorateur`;
    button.addEventListener("click", async () => {
        // Copy first, while the page still has the focus, then open the upload page.
        const field = card.querySelector(".comp-card-description");
        const copied = item.description ? await copyCompilationText(item.description, field) : false;
        window.open(UPLOAD_PAGES[platform], "_blank", "noopener");
        try {
            await request("/api/open-compilations", { file: item.file });
        } catch { }
        cardStatus(card, `${copied ? "Texte copié · colle-le" : "Aucun texte à copier"} dans ${PLATFORM_LABELS[platform]}, `
            + "glisse la vidéo depuis l’Explorateur, puis ajoute ici le lien une fois publié.");
    });
    return button;
}

function cardStatus(card, text, error = false) {
    const status = card.querySelector(".comp-card-status");
    status.textContent = text;
    status.classList.toggle("is-error", error);
}

async function saveLinks(item, links, card) {
    try {
        const { compilation } = await request("/api/compilations/update", { file: item.file, links });
        Object.assign(item, compilation);
        renderCard(card, item);
    } catch (error) {
        cardStatus(card, error.message, true);
    }
}

function renderCard(card, item) {
    card.classList.toggle("is-new", item.file === lastFile);
    const poster = card.querySelector(".comp-poster");
    const image = poster.querySelector("img");
    if (!image.getAttribute("src")) image.src = mediaUrl(item.file, ".jpg");
    poster.querySelector(".comp-duration").textContent = formatDuration(item.duration);
    poster.setAttribute("aria-label", `Lire · ${cardTitle(item)}`);
    card.querySelector(".comp-title").textContent = cardTitle(item);
    const count = item.clipCount ? `${item.clipCount} ${item.clipCount === 1 ? "clip" : "clips"}` : "";
    card.querySelector(".comp-meta").textContent = [formatDate(item.createdAt), count, formatSize(item.size)]
        .filter(Boolean).join(" · ");
    card.querySelector(".comp-links").replaceChildren(...Object.keys(PLATFORM_LABELS).map((platform) => linkRow(item, platform, card)));
    const field = card.querySelector(".comp-card-description");
    if (document.activeElement !== field) field.value = item.description;
    card.querySelector(".comp-card-save").disabled = field.value.trim() === item.description.trim();
    cardStatus(card, "");
    renderYoutubeButton(card, item);
}

function renderYoutubeButton(card, item) {
    const button = card.querySelector(".comp-card-youtube");
    const upload = youtube?.upload;
    const mine = upload?.file === item.file;
    button.hidden = !youtube?.connected || Boolean(item.links.youtube && !(mine && upload.running));
    button.disabled = Boolean(youtube?.busy);
    button.textContent = mine && upload.running ? "Envoi en cours…" : "Envoyer sur YouTube (privé)";
    if (mine && upload.running) cardStatus(card, upload.phase || "Envoi vers YouTube…");
    else if (mine && upload.error) cardStatus(card, upload.error, true);
}

function createCard(item) {
    const card = byId("comp-card-template").content.firstElementChild.cloneNode(true);
    const poster = card.querySelector(".comp-poster");
    poster.addEventListener("click", () => {
        const video = document.createElement("video");
        video.src = mediaUrl(item.file, ".mp4");
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        video.setAttribute("aria-label", cardTitle(item));
        poster.replaceWith(video);
        video.focus();
    });
    poster.querySelector("img").addEventListener("error", (event) => { event.target.hidden = true; });
    const field = card.querySelector(".comp-card-description");
    const save = card.querySelector(".comp-card-save");
    field.addEventListener("input", () => { save.disabled = field.value.trim() === item.description.trim(); });
    save.addEventListener("click", async () => {
        save.disabled = true;
        try {
            const { compilation } = await request("/api/compilations/update", { file: item.file, description: field.value });
            Object.assign(item, compilation);
            renderCard(card, item);
            cardStatus(card, "Description enregistrée.");
        } catch (error) {
            save.disabled = false;
            cardStatus(card, error.message, true);
        }
    });
    card.querySelector(".comp-card-copy").addEventListener("click", async (event) => {
        if (!await copyCompilationText(field.value, field)) return;
        event.target.textContent = "Copié";
        setTimeout(() => { event.target.textContent = "Copier"; }, 1500);
    });
    card.querySelector(".comp-card-folder").addEventListener("click", async () => {
        try {
            await request("/api/open-compilations", { file: item.file });
        } catch (error) {
            cardStatus(card, error.message, true);
        }
    });
    card.querySelector(".comp-card-delete").addEventListener("click", () => confirmDelete(item, card));
    card.querySelector(".comp-card-youtube").addEventListener("click", async (event) => {
        event.target.disabled = true;
        cardStatus(card, "Démarrage de l’envoi…");
        try {
            await request("/api/youtube/upload", { file: item.file, privacy: "private" });
        } catch (error) {
            event.target.disabled = false;
            cardStatus(card, error.message, true);
            return;
        }
        refreshYoutube();
    });
    cards.set(item.file, { card, item });
    renderCard(card, item);
    return card;
}

function confirmDelete(item, card) {
    const dialog = byId("comp-dialog");
    byId("comp-dialog-text").textContent = `${cardTitle(item)} · ${formatSize(item.size)}. La vidéo, sa description `
        + "et ses liens sont supprimés du PC. Les vidéos déjà publiées restent en ligne.";
    dialog.returnValue = "";
    dialog.addEventListener("close", async () => {
        if (dialog.returnValue !== "confirm") return;
        // Stop the stream first: Windows refuses to delete a file that is still being read.
        const video = card.querySelector("video");
        if (video) {
            video.pause();
            video.removeAttribute("src");
            video.load();
        }
        try {
            await request("/api/compilations/delete", { file: item.file });
            compilations = compilations.filter((entry) => entry !== item);
            card.remove();
            renderSummary();
        } catch (error) {
            cardStatus(card, error.message, true);
        }
    }, { once: true });
    dialog.showModal();
}

function renderSummary() {
    const total = compilations.reduce((sum, item) => sum + item.size, 0);
    byId("comp-history-summary").textContent = compilations.length
        ? `${compilations.length} ${compilations.length === 1 ? "vidéo" : "vidéos"} · ${formatSize(total)}` : "";
    const status = byId("comp-history-status");
    status.hidden = compilations.length > 0;
    status.textContent = "Aucune compilation pour l’instant.";
}

async function loadHistory() {
    try {
        const data = await request("/api/compilations");
        compilations = data.compilations;
        cards.clear();
        byId("comp-grid").replaceChildren(...compilations.map(createCard));
        renderSummary();
        return data.job;
    } catch (error) {
        byId("comp-history-status").hidden = false;
        byId("comp-history-status").textContent = error.message;
        return undefined;
    }
}

const emptyText = byId("comp-draft-empty").textContent;
const catalogueScript = document.createElement("script");
catalogueScript.src = "/library/catalogue.js";
catalogueScript.addEventListener("load", () => {
    catalogue = window.utilityCatalogue ?? { matches: [] };
    byId("comp-draft-empty").textContent = emptyText;
    renderDraft();
});
catalogueScript.addEventListener("error", () => {
    byId("comp-draft-empty").textContent = "La vidéothèque n’a pas pu être chargée.";
});
document.head.append(catalogueScript);
/* ---------- YouTube ---------- */

function renderYoutube() {
    const box = byId("comp-youtube");
    const label = byId("comp-youtube-state");
    box.classList.toggle("is-error", Boolean(youtube?.error));
    byId("comp-youtube-connect").hidden = !youtube?.configured || youtube.connected || youtube.pending;
    byId("comp-youtube-disconnect").hidden = !youtube?.connected;
    label.textContent = !youtube ? "…"
        : !youtube.configured ? "fichier client absent (.local/youtube)"
        : youtube.pending ? "autorise l’accès dans l’onglet Google…"
        : youtube.connected ? (youtube.channel?.title ?? "connecté")
        : youtube.error ?? "non connecté";
    for (const { card, item } of cards.values()) renderYoutubeButton(card, item);
}

async function refreshYoutube() {
    clearTimeout(youtubeTimer);
    const before = youtube?.upload;
    try {
        youtube = await request("/api/youtube/status");
    } catch (error) {
        youtube = { error: error.message };
    }
    renderYoutube();
    // An upload just finished: its link is now in the history.
    if (before?.running && !youtube.upload?.running) loadHistory();
    if (youtube.pending || youtube.upload?.running) youtubeTimer = setTimeout(refreshYoutube, 1500);
}

byId("comp-youtube-connect").addEventListener("click", async () => {
    try {
        const { url } = await request("/api/youtube/connect", {});
        window.open(url, "_blank", "noopener");
    } catch (error) {
        byId("comp-youtube-state").textContent = error.message;
    }
    refreshYoutube();
});
byId("comp-youtube-disconnect").addEventListener("click", async () => {
    youtube = await request("/api/youtube/disconnect", {});
    renderYoutube();
});

renderDraft();
refreshYoutube();
loadHistory().then((job) => {
    wasRunning = Boolean(job?.running);
    showJob(job);
});
