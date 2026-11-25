"use strict";

/* Shared by the library panel and the Compilations page: the selection saved in this browser and the post text. */

const COMPILATION_STORAGE_KEY = "utilityCompilation";
const COMPILATION_MAX_CLIPS = 30;
// Instagram's caption limit, the shortest of the platforms.
const COMPILATION_DESCRIPTION_MAX = 2200;
const COMPILATION_KIND_WORDS = {
    smoke: ["smoke", "smokes", "💨", "#cs2smokes"],
    flash: ["flash", "flashes", "⚡", "#cs2flashes"],
    molo: ["molotov", "molotovs", "🔥", "#cs2molotov"],
    he: ["HE grenade", "HE grenades", "💥", "#cs2nades"],
};

function loadCompilationState() {
    try {
        const saved = JSON.parse(localStorage.getItem(COMPILATION_STORAGE_KEY));
        if (saved && typeof saved.active === "boolean" && Array.isArray(saved.ids)) {
            const edited = saved.descriptionEdited === true && typeof saved.description === "string";
            return {
                active: saved.active, ids: saved.ids.filter((id) => typeof id === "string"),
                description: edited ? saved.description : "", descriptionEdited: edited,
                recapture: saved.recapture !== false,
            };
        }
    } catch { }
    return { active: false, ids: [], description: "", descriptionEdited: false, recapture: true };
}

function saveCompilationState(state) {
    try {
        localStorage.setItem(COMPILATION_STORAGE_KEY, JSON.stringify(state));
    } catch { }
}

function compilationClipId(clip) {
    return clip?.exportId ? `${clip.exportId}/${clip.file}` : undefined;
}

function compilationMapLabel(name) {
    const title = name.replace(/^(de|cs|ar)_/, "").replaceAll("_", " ");
    return title.charAt(0).toUpperCase() + title.slice(1);
}

function compilationCallout(clip, match) {
    const name = calloutName(clip, match);
    return name === "À définir" ? "" : name;
}

// The selected clips still present in the library, in the saved order.
function compilationEntries(catalogue, state) {
    const clips = new Map();
    for (const match of catalogue?.matches ?? []) {
        for (const clip of match.clips) {
            const id = compilationClipId(clip);
            if (id) clips.set(id, { clip, match });
        }
    }
    return state ? state.ids.map((id) => clips.get(id)).filter(Boolean) : [];
}

function joinWords(words) {
    return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} & ${words.at(-1)}`;
}

// An English post for TikTok, Instagram Reels and YouTube Shorts: a title line, one line per clip, hashtags.
function compilationDescription(entries) {
    if (!entries.length) return "";
    const maps = [...new Set(entries.map(({ match }) => compilationMapLabel(match.map)))];
    const kindKeys = [...new Set(entries.map(({ clip }) => clip.kind))].filter((kind) => COMPILATION_KIND_WORDS[kind]);
    const where = maps.length <= 3 ? ` on ${joinWords(maps)}` : "";
    let title;
    if (entries.length === 1) {
        const { clip, match } = entries[0];
        const callout = compilationCallout(clip, match);
        title = `CS2 ${compilationMapLabel(match.map)}${callout ? ` ${callout}` : ""} `
            + `${COMPILATION_KIND_WORDS[clip.kind]?.[0] ?? "utility"} lineup`;
    } else {
        const what = kindKeys.length === 1 ? COMPILATION_KIND_WORDS[kindKeys[0]][1] : "lineups";
        title = `${entries.length} CS2 ${what}${where} you need to know`;
    }
    const lines = entries.map(({ clip, match }) => {
        const words = COMPILATION_KIND_WORDS[clip.kind] ?? ["utility", "utility", "🎯"];
        const callout = compilationCallout(clip, match);
        const credit = [clip.player, clip.team].filter(Boolean).join(", ");
        const spot = callout ? `${callout} ${words[0]}` : words[0];
        return `${words[2]} ${compilationMapLabel(match.map)} · ${spot}${credit ? ` (${credit})` : ""}`;
    });
    const tags = ["#cs2", "#counterstrike", "#cs2lineups", "#cs2tips",
        ...maps.slice(0, 3).map((map) => `#${map.toLowerCase().replace(/[^a-z0-9]/g, "")}`),
        ...kindKeys.map((kind) => COMPILATION_KIND_WORDS[kind][3]), "#gaming"];
    const question = entries.length > 1 ? "Which one did you already know? 👇" : "Did you already know this one? 👇";
    return [title.slice(0, 100), "", ...lines, "", question, "", [...new Set(tags)].join(" ")]
        .join("\n").slice(0, COMPILATION_DESCRIPTION_MAX);
}

// The text shown for a selection: generated from the list until it is edited.
function compilationText(state, entries) {
    if (!state.descriptionEdited) state.description = compilationDescription(entries);
    return state.description;
}

function compilationRequest(entries, description, recapture = true) {
    return {
        recapture,
        clips: entries.map(({ clip, match }) => ({
            exportId: clip.exportId, file: clip.file, title: compilationCallout(clip, match),
        })),
        description: description.trim(),
    };
}

// Matches whose demo is gone: what to look for on HLTV before importing them again.
function renderMissingDemos(container, missing, onImport) {
    container.replaceChildren();
    container.hidden = !missing?.length;
    if (!missing?.length) return;
    const title = document.createElement("p");
    title.className = "missing-demos-title";
    title.textContent = "Démo introuvable · réimporte ces matchs :";
    const list = document.createElement("ul");
    for (const item of missing) {
        const row = document.createElement("li");
        const name = document.createElement("strong");
        name.textContent = item.label;
        const details = document.createElement("small");
        const date = item.date ? new Date(item.date).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" }) : "";
        details.textContent = [item.event, item.score ? `Score ${item.score}` : "", date,
            `${item.clips} ${item.clips === 1 ? "clip" : "clips"}`, item.archive].filter(Boolean).join(" · ");
        const link = document.createElement("a");
        link.href = item.hltv;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Chercher sur HLTV ↗";
        row.append(name, details, link);
        list.append(row);
    }
    container.append(title, list);
    if (onImport) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "zone-button";
        button.textContent = "Importer une démo";
        button.addEventListener("click", onImport);
        container.append(button);
    }
}

async function copyCompilationText(text, field) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        if (!field) return false;
        field.select();
        return document.execCommand("copy");
    }
}
