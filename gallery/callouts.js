"use strict";

const editableCalloutMaps = new Set([
    "de_ancient", "de_anubis", "de_cache", "de_dust2", "de_inferno",
    "de_mirage", "de_nuke", "de_overpass", "de_train", "de_vertigo",
]);

function zoneContains([x, y], vertices) {
    let inside = false;
    for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
        const [ax, ay] = vertices[j];
        const [bx, by] = vertices[i];
        const cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax);
        if (Math.abs(cross) < 0.000001 && x >= Math.min(ax, bx) && x <= Math.max(ax, bx)
            && y >= Math.min(ay, by) && y <= Math.max(ay, by)) return true;
        if ((ay > y) !== (by > y) && x < (bx - ax) * (y - ay) / (by - ay) + ax) inside = !inside;
    }
    return inside;
}

function compareZoneLayers(a, b) {
    return (a.priority ?? 0) - (b.priority ?? 0) || b.area - a.area;
}

function zonesOverlap(first, second) {
    const a = first.points;
    const b = second.points;
    const ax = a.map(([x]) => x);
    const bx = b.map(([x]) => x);
    const left = Math.max(Math.min(...ax), Math.min(...bx));
    const right = Math.min(Math.max(...ax), Math.max(...bx));
    const ay = a.map(([, y]) => y);
    const by = b.map(([, y]) => y);
    if (left >= right || Math.max(Math.min(...ay), Math.min(...by)) >= Math.min(Math.max(...ay), Math.max(...by))) return false;

    const cross = ([x, y], [u, v], [p, q]) => (u - x) * (q - y) - (v - y) * (p - x);
    for (let i = 0; i < a.length; i++) {
        const startA = a[i];
        const endA = a[(i + 1) % a.length];
        for (let j = 0; j < b.length; j++) {
            const startB = b[j];
            const endB = b[(j + 1) % b.length];
            if (cross(startA, endA, startB) * cross(startA, endA, endB) < 0
                && cross(startB, endB, startA) * cross(startB, endB, endA) < 0) return true;
        }
    }

    // Without edge crossings, testing between vertex X coordinates covers containment and shared edges.
    function section(points, x) {
        const intersections = [];
        for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
            const [u, v] = points[j];
            const [p, q] = points[i];
            if ((u > x) !== (p > x)) intersections.push(v + (q - v) * (x - u) / (p - u));
        }
        return intersections.sort((u, v) => u - v);
    }
    const cuts = [...new Set([left, right, ...ax, ...bx].filter((x) => x >= left && x <= right))].sort((u, v) => u - v);
    for (let i = 1; i < cuts.length; i++) {
        const x = (cuts[i - 1] + cuts[i]) / 2;
        const ysA = section(a, x);
        const ysB = section(b, x);
        for (let j = 0; j < ysA.length; j += 2) {
            for (let k = 0; k < ysB.length; k += 2) {
                if (Math.min(ysA[j + 1], ysB[k + 1]) - Math.max(ysA[j], ysB[k]) > 1e-7) return true;
            }
        }
    }
    return false;
}

function calloutName(clip, match) {
    const radar = match?.radar;
    if (!radar || !clip.impact) return "À définir";
    const level = radar.lower && clip.impact[2] < radar.thresholdZ ? "lower" : "upper";
    let best;
    for (const zone of match.zones ?? []) {
        if (zone.level === level && (!best || compareZoneLayers(zone, best) > 0) && zoneContains(clip.impact, zone.points)) best = zone;
    }
    return best?.name ?? "À définir";
}

function initializeCalloutEditor({ matches, currentMatch, currentClip, changed }) {
    const open = byId("edit-callouts");
    const dialog = byId("callout-editor");
    const mapSelect = byId("callout-map");
    const floorSelect = byId("callout-floor");
    const zoneSelect = byId("callout-zone");
    const image = byId("callout-radar-image");
    const overlay = byId("callout-overlay");
    const radarView = createRadarViewport(byId("callout-radar"), byId("callout-radar-content"));
    const newZone = byId("new-zone");
    const form = byId("save-zone");
    const name = byId("zone-name");
    const status = byId("callout-status");
    const help = byId("callout-help");
    const remove = byId("delete-zone");
    const undo = byId("undo-vertex");
    const closeShape = byId("close-zone-shape");
    const resume = byId("resume-zone");
    const layerList = byId("zone-layer-list");
    const accept = form.querySelector('button[type="submit"]');
    const maps = new Map();
    let editing;
    let drag;
    let activePolygon;
    let saving = false;
    let confirmDelete = false;
    let draggedLayer;
    let dropTarget;

    function shape(tag, attributes, parent = overlay) {
        const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
        parent.append(element);
        return element;
    }

    const project = (points, radar) => points.map(([x, y]) => [
        (x - radar.originX) / radar.units * 100, (radar.originY - y) / radar.units * 100,
    ]);
    const pointsText = (points) => points.map((point) => point.join(",")).join(" ");

    function previewZones(zones, radar) {
        if (!editing?.closed) return zones;
        const original = zones.find((zone) => zone.id === editing.id);
        const area = original && !editing.moved ? original.area : Math.abs(editing.points.reduce((sum, [x, y], index, points) => {
            const [nextX, nextY] = points[(index + 1) % points.length];
            return sum + x * nextY - y * nextX;
        }, 0)) / 2 * (radar.units / 100) ** 2;
        const preview = { ...original, area, preview: true };
        return original
            ? zones.map((zone) => zone === original ? preview : zone) : [...zones, preview];
    }

    function renderLayers(zones) {
        const focus = layerList.contains(document.activeElement) ? document.activeElement.dataset.layer : undefined;
        const scroll = layerList.scrollTop;
        const selectedZone = zones.find((zone) => zone.id === editing?.id);
        const ordered = selectedZone ? zones.filter((zone) => zone === selectedZone || zonesOverlap(selectedZone, zone))
            .sort((a, b) => compareZoneLayers(b, a)) : [];
        byId("zone-layers").hidden = ordered.length < 2;
        layerList.replaceChildren(...ordered.map((zone, index) => {
            const item = document.createElement("li");
            const button = document.createElement("button");
            button.type = "button";
            button.className = "zone-layer";
            button.dataset.layer = zone.id;
            button.draggable = !saving && ordered.length > 1;
            button.setAttribute("aria-pressed", String(zone.id === editing?.id));
            button.setAttribute("aria-keyshortcuts", "ArrowUp ArrowDown");
            const number = document.createElement("span");
            number.className = "layer-number";
            number.textContent = `${index + 1} –`;
            const grip = document.createElement("span");
            grip.className = "layer-grip";
            grip.setAttribute("aria-hidden", "true");
            grip.textContent = "⠿";
            grip.hidden = ordered.length < 2;
            button.append(number, zone.name, grip);
            item.append(button);
            return item;
        }));
        layerList.scrollTop = scroll;
        if (focus) [...layerList.querySelectorAll(".zone-layer")].find((item) => item.dataset.layer === focus)?.focus({ preventScroll: true });
    }

    function render() {
        const map = maps.get(mapSelect.value);
        const { radar } = map;
        const floor = floorSelect.value;
        const zones = map.zones.filter((zone) => zone.level === floor);
        zoneSelect.replaceChildren(new Option(zones.length ? "Choisir une zone" : "Aucune zone", ""),
            ...zones.map((zone) => new Option(zone.name, zone.id)));
        zoneSelect.value = editing?.id ?? "";
        image.src = radar[floor];
        image.alt = `Radar de ${mapLabel(map.map)}`;
        byId("callout-floor-control").hidden = !radar.lower;

        const drawing = editing && !editing.closed;
        const creating = editing && !editing.id;
        overlay.classList.toggle("drawing", Boolean(drawing));
        byId("draw-zone-actions").hidden = !drawing;
        form.hidden = !editing?.closed;
        remove.hidden = !editing?.id;
        resume.hidden = !(creating && editing.closed);
        remove.textContent = confirmDelete ? "Confirmer la suppression" : "Supprimer la zone";
        mapSelect.disabled = floorSelect.disabled = zoneSelect.disabled = newZone.disabled = saving || Boolean(creating);
        for (const control of [name, accept, remove, resume, byId("discard-zone"), byId("close-callouts")]) control.disabled = saving;
        closeShape.disabled = !editing || editing.points.length < 3;
        undo.disabled = !editing?.points.length;
        help.textContent = drawing ? "Clique pour placer les sommets. Clique sur le premier sommet pour fermer la zone."
            : editing ? "Glisse les sommets, puis enregistre la zone."
                : "Sélectionne une zone sur le radar ou dans la liste.";
        renderLayers(zones);

        overlay.replaceChildren();
        activePolygon = undefined;
        const visibleImpacts = matches.find((match) => match.map === map.map)?.clips ?? [];
        const activeClip = currentMatch()?.map === map.map ? currentClip() : undefined;
        for (const clip of visibleImpacts) {
            const position = radarPosition(clip.impact, radar);
            if (position && position.lower === (floor === "lower")) {
                shape("circle", { cx: position.left, cy: position.top, r: clip === activeClip ? 1.1 : 0.35,
                    class: clip === activeClip ? "callout-impact active" : "callout-impact" });
            }
        }
        for (const zone of previewZones(zones, radar).reverse().sort(compareZoneLayers)) {
            const points = zone.preview ? editing.points : project(zone.points, radar);
            const polygon = shape("polygon", { points: pointsText(points), "data-zone": zone.id ?? "",
                class: zone.preview ? "callout-shape active" : "callout-shape" });
            if (zone.preview) activePolygon = polygon;
            if (zone.name) shape("title", {}, polygon).textContent = zone.name;
        }
        if (drawing) shape("polyline", { points: pointsText(editing.points), class: "callout-draft open" });
        if (editing) {
            editing.points.forEach(([x, y], index) => {
                const first = drawing && index === 0;
                const type = first ? " first" : drawing ? "" : " handle";
                shape("circle", { cx: x, cy: y, r: first || !drawing ? 0.675 : 0.325,
                    "data-first": String(first), "data-vertex": drawing ? "" : index, class: `callout-vertex${type}` });
            });
        }
    }

    function refreshZones(map, zones) {
        map.zones = zones;
        const match = matches.find((item) => item.map === map.map);
        if (match) match.zones = zones;
        changed();
    }

    function chooseZone(id) {
        if (drag) {
            const { pointer } = drag;
            drag = undefined;
            overlay.classList.remove("dragging");
            if (overlay.hasPointerCapture(pointer)) overlay.releasePointerCapture(pointer);
        }
        const map = maps.get(mapSelect.value);
        const zone = map.zones.find((item) => item.id === id && item.level === floorSelect.value);
        editing = zone && { id: zone.id, points: project(zone.points, map.radar), closed: true };
        name.value = zone?.name ?? "";
        confirmDelete = false;
        status.textContent = "";
        render();
    }

    function finishDrawing() {
        if (!editing || editing.points.length < 3) {
            status.textContent = "Place au moins trois sommets.";
            return;
        }
        editing.closed = true;
        status.textContent = "";
        render();
        name.focus();
    }

    async function update(action) {
        if (saving || drag) return;
        const map = maps.get(mapSelect.value);
        const id = editing.id;
        const { radar } = map;
        const points = !id || editing.moved ? editing.points.map(([left, top]) => [
            radar.originX + left / 100 * radar.units, radar.originY - top / 100 * radar.units,
        ]) : undefined;
        saving = true;
        status.textContent = "";
        render();
        try {
            const result = await request("/api/callout-zones", {
                map: map.map, action, id, name: name.value, level: floorSelect.value, points,
            });
            refreshZones(map, result.zones);
            chooseZone(action === "save" ? result.id : "");
            status.textContent = action === "save" ? "Zone enregistrée." : "Zone supprimée.";
        } catch (error) {
            status.textContent = error.message;
        } finally {
            saving = false;
            render();
        }
    }

    open.hidden = false;
    open.addEventListener("click", async () => {
        open.disabled = true;
        try {
            const result = await request("/api/callout-maps");
            maps.clear();
            for (const item of result.maps) {
                if (editableCalloutMaps.has(item.map)) maps.set(item.map, item);
            }
            for (const match of matches) {
                const map = maps.get(match.map);
                if (map && match.radar) map.radar = match.radar;
                if (map) match.zones = map.zones;
            }
            if (!maps.size) throw new Error("Aucune carte avec radar n’est disponible.");
            changed();
            const options = [...maps.keys()].map((map) => new Option(map === "de_dust2" ? "Dust 2" : mapLabel(map), map));
            mapSelect.replaceChildren(...options.sort((a, b) => a.text.localeCompare(b.text, "fr")));
            const match = currentMatch();
            if (maps.has(match?.map)) mapSelect.value = match.map;
            floorSelect.value = maps.get(mapSelect.value).radar.lower ? byId("level").value : "upper";
            radarView?.reset();
            chooseZone("");
            dialog.showModal();
        } catch (error) {
            window.alert(error.message);
        } finally {
            open.disabled = false;
        }
    });

    mapSelect.addEventListener("change", () => {
        floorSelect.value = "upper";
        radarView?.reset();
        chooseZone("");
    });
    floorSelect.addEventListener("change", () => chooseZone(""));
    zoneSelect.addEventListener("change", () => chooseZone(zoneSelect.value));
    newZone.addEventListener("click", () => {
        chooseZone("");
        editing = { points: [], closed: false };
        render();
    });
    const cancel = () => chooseZone(editing?.id);
    byId("cancel-zone").addEventListener("click", cancel);
    byId("discard-zone").addEventListener("click", cancel);
    byId("close-callouts").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => chooseZone(""));
    dialog.addEventListener("cancel", (event) => {
        const editingShape = editing && (!editing.id || editing.moved || drag);
        if (saving || editingShape) event.preventDefault();
        if (editingShape && !saving) cancel();
    });
    closeShape.addEventListener("click", finishDrawing);
    resume.addEventListener("click", () => {
        editing.closed = false;
        status.textContent = "";
        render();
    });
    undo.addEventListener("click", () => {
        editing.points.pop();
        status.textContent = "";
        render();
    });
    async function moveLayer(id, targetId, before) {
        if (saving || drag || id === targetId) return;
        const map = maps.get(mapSelect.value);
        const scope = [...layerList.querySelectorAll(".zone-layer")].map((item) => item.dataset.layer);
        saving = true;
        status.textContent = "";
        render();
        try {
            const { zones } = await request("/api/callout-zones", { map: map.map, action: "reorder", id, targetId, before, scope });
            refreshZones(map, zones);
            status.textContent = "Ordre enregistré.";
        } catch (error) {
            status.textContent = error.message;
        } finally {
            saving = false;
            render();
        }
    }

    function clearDropTarget() {
        dropTarget?.classList.remove("drop-before", "drop-after");
        dropTarget = undefined;
    }

    function isBefore(event, target) {
        const bounds = target.getBoundingClientRect();
        return event.clientY < bounds.top + bounds.height / 2;
    }

    layerList.addEventListener("dragstart", (event) => {
        const layer = event.target.closest('.zone-layer[draggable="true"]');
        if (!layer) return;
        draggedLayer = layer.dataset.layer;
        event.dataTransfer.setData("text/plain", draggedLayer);
        event.dataTransfer.effectAllowed = "move";
    });
    layerList.addEventListener("dragover", (event) => {
        if (!draggedLayer) return;
        const target = event.target.closest(".zone-layer");
        clearDropTarget();
        if (!target || target.dataset.layer === draggedLayer) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        dropTarget = target;
        target.classList.add(isBefore(event, target) ? "drop-before" : "drop-after");
    });
    layerList.addEventListener("dragleave", (event) => {
        if (!layerList.contains(event.relatedTarget)) clearDropTarget();
    });
    layerList.addEventListener("dragend", () => {
        draggedLayer = undefined;
        clearDropTarget();
    });
    layerList.addEventListener("drop", (event) => {
        const target = event.target.closest(".zone-layer");
        if (!draggedLayer || !target) return;
        event.preventDefault();
        const id = draggedLayer;
        draggedLayer = undefined;
        clearDropTarget();
        moveLayer(id, target.dataset.layer, isBefore(event, target));
    });
    layerList.addEventListener("keydown", (event) => {
        const layer = event.target.closest(".zone-layer");
        if (!layer || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
        const before = event.key === "ArrowUp";
        const item = layer.parentElement;
        const neighbor = before ? item.previousElementSibling : item.nextElementSibling;
        if (!neighbor) return;
        event.preventDefault();
        moveLayer(layer.dataset.layer, neighbor.firstElementChild.dataset.layer, before);
    });
    layerList.addEventListener("click", (event) => {
        const layer = event.target.closest(".zone-layer");
        if (layer && !saving && (editing?.id || !editing)) chooseZone(layer.dataset.layer);
    });

    const clamp = (value) => Math.max(0, Math.min(100, value));
    function pointerPosition(event) {
        const bounds = overlay.getBoundingClientRect();
        return [(event.clientX - bounds.left) / bounds.width * 100, (event.clientY - bounds.top) / bounds.height * 100];
    }

    overlay.addEventListener("pointerdown", (event) => {
        const handle = event.target.closest(".callout-vertex.handle");
        if (event.button !== 0 || saving || drag || !handle || !editing?.closed) return;
        const index = Number(handle.dataset.vertex);
        drag = { pointer: event.pointerId, start: pointerPosition(event), point: editing.points[index], index, handle };
        overlay.setPointerCapture(event.pointerId);
        overlay.classList.add("dragging");
        event.preventDefault();
    });
    overlay.addEventListener("pointermove", (event) => {
        if (drag?.pointer !== event.pointerId) return;
        const cursor = pointerPosition(event);
        const point = drag.point.map((value, index) => clamp(value + cursor[index] - drag.start[index]));
        editing.points[drag.index] = point;
        drag.handle.setAttribute("cx", point[0]);
        drag.handle.setAttribute("cy", point[1]);
        activePolygon.setAttribute("points", pointsText(editing.points));
    });
    function endDrag(event) {
        if (drag?.pointer !== event.pointerId) return;
        if (event.type === "pointerup") editing.moved ||= drag.point.some((value, index) => value !== editing.points[drag.index][index]);
        else editing.points[drag.index] = drag.point;
        drag = undefined;
        overlay.classList.remove("dragging");
        status.textContent = "";
        render();
    }
    for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) overlay.addEventListener(event, endDrag);

    overlay.addEventListener("click", (event) => {
        if (saving || drag) return;
        if (!editing || editing.closed) {
            if (editing?.closed && !editing.id) return;
            if (event.target.dataset.zone) chooseZone(event.target.dataset.zone);
            return;
        }
        if (event.target.dataset.first === "true") {
            finishDrawing();
            return;
        }
        if (editing.points.length >= 128) {
            status.textContent = "La zone a atteint la limite de 128 sommets.";
            return;
        }
        editing.points.push(pointerPosition(event).map(clamp));
        status.textContent = "";
        render();
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        update("save");
    });
    remove.addEventListener("click", () => {
        if (confirmDelete) update("delete");
        else {
            confirmDelete = true;
            render();
        }
    });
}
