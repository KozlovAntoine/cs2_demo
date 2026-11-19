# CS2 Utilities — Website Features

## Overview

CS2 Utilities is a local, browser-based application for turning Counter-Strike 2 GOTV demos into grenade clips and exploring them on map radars. The interface is in French. The server, source files, generated videos, and library data remain on the user's Windows PC.

## Demo import and clip generation

- Select one or more `.dem` files or HLTV `.rar` archives through a file picker.
- When a single archive is selected, choose a specific demo inside it; batch imports process all demos in each selected archive.
- See which demos have already been imported.
- Generate clips for all utility types or restrict the export to smokes, flashbangs, Molotovs, or HE grenades.
- Optionally set a maximum number of clips per map.
- Automatically analyze demos, launch CS2 for capture, and close it after the last capture for each demo.
- Start clips in the thrower's point of view three seconds before the throw, follow the projectile, and continue for three seconds after activation. Clip boundaries may be shorter at the start or end of a demo. If activation is unavailable, the end is estimated from the available projectile data.
- Detect duplicate throws across current and previous exports using both launch and activation positions, and skip capturing those duplicates. Deleted clips remain registered for duplicate detection.
- Keep valid clips if a capture is incomplete, report missing sequences, and allow the demo to be imported again to capture missing throws.

## Export management

- Follow the current processing phase, latest message, and expandable log in the interface.
- Pause between demos, resume processing, or cancel the active job. Cancellation retains finalized clips and cleans up working files.
- Recover the current job's state and controls after refreshing the browser.
- Browse completed individual export galleries from the export screen.
- Save timestamped export and portrait-render logs under `exports/logs/`.
- Automatically clean temporary demo and capture files, including leftovers detected after an interrupted run.

## Video library

- Browse a unified library organized by map and open individual exported galleries.
- Play clips in the browser and inspect their player, round, utility type, callout, and available throw information.
- Filter clips by utility type, round, frequency, favorites, or unwatched status. Favorites and unwatched are mutually exclusive.
- Mark clips as favorites; opening a clip marks it as watched. These states are saved locally.
- Use keyboard shortcuts: **F** toggles the selected clip's favorite state, **N** moves forward, and **B** moves backward through clip navigation.
- Delete a selected clip after confirmation. Its MP4 is removed and the library and radar update accordingly; its throw positions are retained for duplicate detection.
- Use fullscreen library view for a larger workspace.
- Preserve the current map, clip, radar group, and filters in the URL. Browser Back and Forward restore navigation state; copied links work on the same PC.

## Radar and heatmap

- Show grenade activation points or launch points on the map radar.
- Group nearby throws into numbered markers. Selecting a marker filters the list and shows related launch and activation positions joined by dotted lines; clicking the radar background clears the selection.
- Progressively split marker clusters as the radar is zoomed in, while retaining the selected group's list.
- Switch between upper and lower radar levels on maps that support them.
- Zoom with the mouse wheel or the **−** and **+** controls, and pan by dragging when zoomed in.
- Display an activation heatmap with a density legend. The heatmap reflects active filters and weights throws by their recorded occurrence count when available.
- Continue browsing clips in the list when a compatible radar is unavailable.

## Throw frequency

- Count occurrences of a throw in processed demos, including duplicates skipped during capture. Reimporting a demo updates its count.
- Show the average number of occurrences per match in which the throw was observed, along with a frequency badge.
- Filter by **Rare** (average below 2), **Common** (2 through 10), or **Very frequent** (above 10). Existing clips gain frequency data when a subsequent analysis matches them.

## Callouts and lineup information

- Automatically label throws using the callout zone covering their activation point; throws outside defined zones show an undefined label.
- Create callout zones on a selected map and level by drawing polygons on the radar. Undo the last vertex, close or resume a shape, move vertices, rename zones, and delete them.
- Reorder overlapping zones to determine which callout takes priority. Zone changes are saved locally.
- Override the callout for an individual clip by selecting or entering a name, and restore automatic assignment when desired.
- Display detected throw inputs, including indicators for a half-power throw, when the optional demo parser is installed and data is available.
- Copy the detected CS2 position and view angles as console commands for practicing on the same map with `sv_cheats 1`. For a jump throw, the position uses the last detected grounded input when available.

## Portrait video and sharing

- Generate a 9:16 version of a selected clip with FFmpeg. The output is a centered 720 × 1280 crop that preserves aspect ratio and audio.
- Open the output folder after rendering; the filename identifies the player, map, utility type, and render date.
- Move the complete library, videos, favorites, and callout zones by copying the `exports/` folder.
- Share an individual gallery by sending its complete export folder; its `index.html` can be opened in a browser to view the clips. Some library management features require the running local application.

## Runtime requirements and configuration

- Python 3.10 or later on Windows is required to run the local application. The default address is `http://127.0.0.1:38473`.
- Clip generation requires CS Demo Manager, CS2, Steam, HLAE, and FFmpeg; 7-Zip is needed for RAR archives. The optional demo parser enables detected throw inputs and console commands.
- Command-line options allow a custom CS Demo Manager location, output directory, and server port.
- The application can be stopped using **Quitter** in the interface or Ctrl+C in the terminal.
