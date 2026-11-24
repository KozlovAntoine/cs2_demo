// HLAE runs this file in CS2. The generator appends the trajectories for one demo.
if (!globalThis.csUtilityVideo) {
    let clip;
    globalThis.csUtilityVideo = (number) => { clip = grenadeClips[number - 1]; };

    function positionAt(samples, tick) {
        let low = 0;
        let high = samples.length - 1;
        while (high - low > 1) {
            const mid = Math.floor((low + high) / 2);
            if (samples[mid][0] <= tick) low = mid;
            else high = mid;
        }
        const [start, x, y, z] = samples[low];
        const [end, nextX, nextY, nextZ] = samples[high];
        const weight = end > start ? Math.max(0, Math.min(1, (tick - start) / (end - start))) : 0;
        return [x + (nextX - x) * weight, y + (nextY - y) * weight, z + (nextZ - z) * weight];
    }

    mirv.events.cViewRenderSetupView.on("cs-utility-video", () => {
        const tick = mirv.getDemoTick();
        const cameraEnd = clip?.cameraEndTick ?? clip?.endTick;
        if (!clip || !Number.isFinite(tick) || tick < clip.launchTick || tick >= cameraEnd) return;

        const [x, y, z] = positionAt(clip.flight, tick);
        const [behindX, behindY] = positionAt(clip.flight, Math.min(tick, clip.activationTick) - 5);
        const angle = Math.hypot(x - behindX, y - behindY) > 1
            ? Math.atan2(y - behindY, x - behindX)
            : clip.yaw * Math.PI / 180;
        const distance = 42;
        const height = 28;
        const cameraX = x - Math.cos(angle) * distance;
        const cameraY = y - Math.sin(angle) * distance;
        const target = tick >= clip.activationTick ? clip.impact : [x, y, z];
        const dx = target[0] - cameraX;
        const dy = target[1] - cameraY;
        return {
            x: cameraX, y: cameraY, z: z + height,
            rX: Math.atan2(z + height - target[2], Math.hypot(dx, dy)) * 180 / Math.PI,
            rY: Math.atan2(dy, dx) * 180 / Math.PI, rZ: 0, fov: 90,
        };
    });
}
