"""Animated HUD titles for compilation clips, drawn by FFmpeg's libass as ASS subtitles.

Each element is a function of time, sampled at every frame. Frames that render the same text share one subtitle
event, so a still title costs one line and an animation can follow any curve.
"""
import random

WIDTH, HEIGHT = 1080, 1920
FRAME_HEIGHT = WIDTH * 9 // 16
FRAME_TOP = (HEIGHT - FRAME_HEIGHT) // 2
FRAME_BOTTOM = FRAME_TOP + FRAME_HEIGHT
CENTER = WIDTH // 2

FONT = "Consolas"
# Advance of every Consolas glyph, in em. A monospace font keeps typed and decoding lines from moving.
ADVANCE = 0.55
WHITE, DARK, GREY, RED, VEIL, EFFECT = "#ffffff", "#10161c", "#9aa3ae", "#b3261e", "#0c1013", "#ff8a7a"
GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%/<>"
# A key stays lit at least this long, and two holds closer than the gap read as one.
KEY_MIN_SECONDS, KEY_GAP_SECONDS = 0.3, 0.1
HIDDEN = "{\\alpha&HFF&}"

BAR_Y, BAND_Y, HEAD_Y = 303, 366, 484
COUNTER_X, COUNTER_Y = WIDTH - 60, 106
NAME_Y, TEAM_Y, HOWTO_Y = FRAME_BOTTOM + 95, FRAME_BOTTOM + 168, FRAME_BOTTOM + 258
KEYS_TOP, BADGE_Y, EFFECT_Y = FRAME_BOTTOM + 296, FRAME_BOTTOM + 98, FRAME_BOTTOM + 182


# Timing curves

def clamp(x, low=0.0, high=1.0):
    return max(low, min(high, x))


def prog(t, start, duration):
    return clamp((t - start) / duration)


def out_cubic(p):
    return 1 - (1 - p) ** 3


def out_expo(p):
    return 1.0 if p >= 1 else 1 - 2 ** (-10 * p)


def pulse(t, at, rise, hold, fall):
    """0 → 1 just before a moment, held, then back to 0. A missing moment never pulses."""
    if at is None or t < at - rise:
        return 0.0
    if t < at:
        return out_cubic(prog(t, at - rise, rise))
    if t < at + hold:
        return 1.0
    return 1 - out_cubic(prog(t, at + hold, fall))


# ASS drawing

def rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, p):
    return "#" + "".join(f"{round(x + (y - x) * clamp(p)):02x}" for x, y in zip(rgb(a), rgb(b)))


def ass_color(value):
    r, g, b = rgb(value)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_alpha(opacity):
    return f"&H{round((1 - clamp(opacity)) * 255):02X}&"


def stamp(seconds):
    cs = max(0, int(seconds * 100 + 0.5))
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    secs, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def plain(text):
    """Player names and callouts must not open override blocks or escapes."""
    return str(text).replace("\\", "＼").replace("{", "(").replace("}", ")")


def fit(text, largest, width):
    return max(28, min(largest, int(width / max(1, len(text) * ADVANCE))))


def rounded_rect(w, h, r):
    w, h, r = round(w), round(h), round(min(r, w / 2, h / 2))
    k = round(r * 0.4477)
    return (f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
            f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0")


def node(x, y, body, *, size=40, color=WHITE, opacity=1.0, an=5, spacing=0.0, clip=None, extra=""):
    if body is None or opacity <= 0.004:
        return None
    tags = (f"\\an{an}\\pos({x:.1f},{y:.1f})\\fn{FONT}\\fs{size}\\b700\\1c{ass_color(color)}"
            f"\\alpha{ass_alpha(opacity)}")
    if spacing:
        tags += f"\\fsp{spacing:.1f}"
    if clip:
        tags += "\\clip({:.0f},{:.0f},{:.0f},{:.0f})".format(*clip)
    return "{" + tags + extra + "}" + body


def box(x, y, w, h, r, *, fill, stroke=None, stroke_width=0, opacity=1.0, fill_opacity=0.92, clip=None):
    extra = (f"\\p1\\bord{stroke_width}\\shad0\\3c{ass_color(stroke or fill)}"
             f"\\1a{ass_alpha(opacity * fill_opacity)}\\3a{ass_alpha(opacity)}")
    return node(x, y, rounded_rect(w, h, r), color=fill, opacity=opacity, clip=clip, extra=extra)


def typed(text, t, start, opacity, rate=0.03, cursor=True):
    """A typewriter line. Every glyph is always there, untyped ones transparent, so the layout never moves."""
    if t < start:
        return None
    text = plain(text)
    shown = "{\\alpha" + ass_alpha(opacity) + "}"
    count = min(len(text), int((t - start) / rate))
    done = start + len(text) * rate
    blink = cursor and (count < len(text) or (t < done + 0.75 and int((t - done) / 0.2) % 2 == 0))
    # Slots: a hidden lead glyph, the typed text, the cursor, the untyped rest and a trailing slot for the cursor.
    after = text[count + 1:] + "█" if count < len(text) else ""
    return HIDDEN + "█" + shown + text[:count] + (shown if blink else HIDDEN) + "█" + HIDDEN + after


def decoded(text, t, start, opacity, scrambled_color, step=0.045, settle=0.28):
    """Each letter flickers through random glyphs, then locks on its final value."""
    if t < start:
        return None
    text = plain(text)
    # Long callouts decode faster, so that every title is readable within about a second.
    step = min(step, 0.9 / max(1, len(text)))
    shown = "\\alpha" + ass_alpha(opacity)
    frame = int(t * 30)
    parts = []
    for index, char in enumerate(text):
        begin = start + step * index
        if char == " ":
            parts.append(" ")
        elif t < begin:
            parts.append(HIDDEN + char)
        elif t < begin + settle:
            glyph = random.Random(index * 7919 + frame).choice(GLYPHS)
            parts.append("{" + shown + "\\1c" + ass_color(scrambled_color) + "}" + glyph)
        else:
            parts.append("{" + shown + "\\1c" + ass_color(WHITE) + "}" + char)
    return "".join(parts)


class Script:
    def __init__(self, duration, fps):
        self.fps = fps
        self.frames = int(round(duration * fps))
        self.lines = []

    def add(self, layer, draw, start=0.0):
        """Sample draw(t) at every frame from start; frames that render the same text share one event."""
        current, since = None, 0
        for frame in range(max(0, int(start * self.fps)), self.frames + 1):
            text = draw(frame / self.fps) if frame < self.frames else None
            if text != current:
                if current is not None:
                    # Boundaries sit between two frames, so centisecond rounding never moves a change by a frame.
                    self.lines.append(f"Dialogue: {layer},{stamp((since - 0.5) / self.fps)},"
                                      f"{stamp((frame - 0.5) / self.fps)},Base,,0,0,0,,{current}")
                current, since = text, frame

    def text(self):
        return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {WIDTH}\nPlayResY: {HEIGHT}\nWrapStyle: 2\n"
                "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
                "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding\n"
                f"Style: Base,{FONT},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,"
                "0,0,0,1\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                + "\n".join(self.lines) + "\n")


def key_layout(keys, size=36, padding=26, height=72, gap=18):
    """Centred key boxes, wrapped on two rows when they are too wide: (key, x, y, width, height)."""
    widths = [round(len(key) * size * ADVANCE) + 2 * padding for key in keys]
    rows, row, row_width = [], [], 0
    for key, width in zip(keys, widths):
        if row and row_width + gap + width > WIDTH - 120:
            rows.append(row)
            row, row_width = [], 0
        row.append((key, width))
        row_width += width + (gap if len(row) > 1 else 0)
    if row:
        rows.append(row)
    placed = []
    for index, row in enumerate(rows):
        x = (WIDTH - sum(width for _, width in row) - gap * (len(row) - 1)) / 2
        y = KEYS_TOP + index * (height + 16) + height / 2
        for key, width in row:
            placed.append((key, x + width / 2, y, width, height))
            x += width + gap
    return placed


def titles(card, fps):
    """The ASS script of one clip's titles.

    card: duration, color, band, title, counter, player, team, team_color, keys (each with its label and the [from, to]
    spans in seconds where it is held, or None when they were not recorded), and the moments in seconds of the
    throw (launch), the detonation (pop) and the camera switches (povs, each with its label, effect and the moment the
    next one takes over), each possibly None. pov is the first switch.
    """
    script = Script(card["duration"], fps)
    color, keys, pov = card["color"], card["keys"], card.get("pov")
    launch, pop = card.get("launch"), card.get("pop")
    pov_at = pov["at"] if pov else None
    leave = card["duration"] - 0.45

    def shown(t):
        """Full until the end, then a short flicker that switches the titles off."""
        if t < leave:
            return 1.0
        return (1.0 if int((t - leave) / 0.05) % 2 == 0 else 0.25) if t < leave + 0.3 else 0.0

    def closing(t):
        """Half-width of the clip that closes on the thrower lines just before the camera switches, or None."""
        if pov_at is None or t < pov_at - 0.2:
            return None
        return 280 * (1 - out_cubic(prog(t, pov_at - 0.2, 0.14)))

    for y, height in ((0, FRAME_TOP), (FRAME_BOTTOM, HEIGHT - FRAME_BOTTOM)):
        # A dark veil keeps the text readable when a flash whitens the blurred background.
        veil = (f"{{\\an7\\pos(0,{y})\\p1\\bord0\\shad0\\1c{ass_color(VEIL)}\\alpha{ass_alpha(0.55)}}}"
                f"m 0 0 l {WIDTH} 0 {WIDTH} {height} 0 {height}")
        script.add(0, lambda t, veil=veil: veil)

    def corner(t, right, bottom):
        """An L bracket in a corner of the game frame. Its points span [0, arm] so its box starts at its origin."""
        p = out_cubic(prog(t, 0.05, 0.45))
        if p <= 0 or shown(t) <= 0:
            return None
        arm, inset, thick = round(18 + 30 * p), 22, 4
        points = [(0, 0), (arm, 0), (arm, thick), (thick, thick), (thick, arm), (0, arm)]
        points = [(arm - x if right else x, arm - y if bottom else y) for x, y in points]
        path = f"m {points[0][0]} {points[0][1]} l " + " ".join(f"{x} {y}" for x, y in points[1:])
        x = WIDTH - inset - arm if right else inset
        y = FRAME_BOTTOM - inset - arm if bottom else FRAME_TOP + inset
        return (f"{{\\an7\\pos({x},{y})\\p1\\bord0\\shad0\\1c{ass_color(color)}"
                f"\\alpha{ass_alpha(0.9 * p * shown(t))}}}{path}")

    for right in (False, True):
        for bottom in (False, True):
            script.add(1, lambda t, right=right, bottom=bottom: corner(t, right, bottom))

    def bar(t):
        p = out_cubic(prog(t, 0.1, 0.35))
        flash = pulse(t, pop, 0.02, 0.1, 0.4)
        return box(CENTER, BAR_Y, 200, 6, 1, fill=mix(color, WHITE, flash), fill_opacity=1, opacity=shown(t),
                   clip=(CENTER - 102, 0, CENTER - 102 + 204 * p, HEIGHT)) if p > 0 else None

    title = card["title"]
    script.add(1, bar)
    script.add(2, lambda t: node(CENTER, BAND_Y, typed(f"> {card['band']}", t, 0.15, shown(t)), size=38,
                                 color=color, spacing=2))
    script.add(2, lambda t: node(CENTER, HEAD_Y, decoded(title, t, 0.35, shown(t), color),
                                 size=fit(title, 104, WIDTH - 120)))
    if card.get("counter"):
        script.add(2, lambda t: node(COUNTER_X, COUNTER_Y, typed(card["counter"], t, 0.4, 0.8 * shown(t), 0.05, False),
                                     size=36, an=6))

    def thrower_line(y, text, start, rate, size, line_color, cursor, spacing=0):
        def draw(t):
            half = closing(t)
            if half is not None and half <= 1:
                return None
            return node(CENTER, y, typed(text, t, start, shown(t), rate, cursor), size=size, color=line_color,
                        spacing=spacing, clip=None if half is None else (CENTER - half, 0, CENTER + half, HEIGHT))
        return draw

    if card.get("player"):
        player = card["player"]
        script.add(2, thrower_line(NAME_Y, player, 0.5, 0.04, fit(player, 72, WIDTH - 160), WHITE, True))
    if card.get("team"):
        script.add(2, thrower_line(TEAM_Y, card["team"], 0.7, 0.03, 32, card["team_color"], False, 2))
    if keys:
        script.add(2, lambda t: node(CENTER, HOWTO_Y, typed("HOW TO THROW", t, 0.8, shown(t), 0.025, False), size=28,
                                     color=GREY, spacing=4))

    def heat(spans):
        """How lit a key is over time: while the thrower holds it, or once at the release when it was not recorded."""
        if not spans:
            spans = [[launch - 0.05, launch + 0.45]] if launch is not None else []
        # A tap such as a jump stays readable, and a key let go for an instant stays lit.
        merged = []
        for start, end in sorted(spans):
            end = max(end, start + KEY_MIN_SECONDS)
            if merged and start - merged[-1][1] < KEY_GAP_SECONDS:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        def level(t):
            value = 0.0
            for start, end in merged:
                if start - 0.04 <= t < end:
                    value = max(value, out_cubic(prog(t, start - 0.04, 0.04)))
                elif end <= t < end + 0.15:
                    value = max(value, 1 - prog(t, end, 0.15))
            return value
        return level

    for order, (key, (label, x, y, w, h)) in enumerate(zip(keys, key_layout([key["label"] for key in keys]))):
        start, lit = 1.05 + 0.12 * order, heat(key["held"])

        def keycap(t, x=x, y=y, w=w, h=h, start=start, lit=lit):
            p = out_cubic(prog(t, start, 0.25))
            return box(x, y, w, h, 6, fill=mix(DARK, color, lit(t)), stroke=color, stroke_width=3, opacity=shown(t),
                       clip=(x - w / 2 - 4, 0, x - w / 2 - 4 + (w + 8) * p, HEIGHT)) if p > 0 else None

        def keylabel(t, label=label, x=x, y=y, start=start, lit=lit):
            # The label switches at once so that it stays readable while the box fades.
            return node(x, y, typed(label, t, start + 0.15, shown(t), 0.03, False), size=36,
                        color=DARK if lit(t) >= 0.5 else WHITE)
        script.add(3, keycap)
        script.add(4, keylabel)

    for current in card.get("povs") or ([pov] if pov else []):
        at, until, label = current["at"], current.get("until"), current["label"]
        size = fit(label, 42, WIDTH - 220)
        width = min(WIDTH - 120, round(len(label) * size * ADVANCE) + 80)

        def present(t, until=until):
            """The badge of one enemy, until the camera moves to the next."""
            return shown(t) if until is None or t < until else 0.0

        def badge(t, at=at, width=width, present=present):
            half = (width / 2 + 4) * out_expo(prog(t, at, 0.22))
            return box(CENTER, BADGE_Y, width, 84, 8, fill=RED, opacity=present(t),
                       clip=(CENTER - half, 0, CENTER + half, HEIGHT)) if half > 1 and present(t) > 0 else None

        def name(t, at=at, label=label, size=size, present=present):
            return node(CENTER, BADGE_Y, decoded(label, t, at + 0.08, present(t), "#ffd2cc", 0.02, 0.18), size=size) \
                if present(t) > 0 else None
        script.add(3, badge, at)
        script.add(4, name, at)
        if current.get("effect"):
            def effect(t, at=at, text=current["effect"], present=present):
                return node(CENTER, EFFECT_Y, typed(text, t, at + 0.35, present(t), 0.03, False), size=32, color=EFFECT,
                            spacing=2) if present(t) > 0 else None
            script.add(4, effect, at)
    return script.text()
