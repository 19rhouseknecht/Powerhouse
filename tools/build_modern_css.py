"""Builds static/css/modern-colors.css: every color in static/css/style.css (the Dark look) mapped onto the Modern palette.

The Dark look is style.css as it always was. Modern is style.css + modern-colors.css (this file's output: the same rules,
only their colors, scoped to html[data-theme="modern"] with no extra specificity) + modern.css (written by hand: fonts,
shapes, shadows, the sidebar). Run this after any change to style.css:

    python tools/build_modern_css.py

tests/test_theme.py fails while modern-colors.css is out of date.
"""
import colorsys
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "static", "css", "style.css")
TARGET = os.path.join(ROOT, "static", "css", "modern-colors.css")
SCOPE = ':where(html[data-theme="modern"])'
COLOR_PROPS = re.compile(r"^(color|background(-color|-image)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?|box-shadow|text-shadow|"
                         r"fill|stroke|accent-color|caret-color|text-decoration(-color)?|column-rule(-color)?|scrollbar-color|color-scheme|"
                         r"--[\w-]+)$")
HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")
NAMED = re.compile(r"(?<![\w-])(white|black)(?![\w-])")
ACCENT_HUE = 258 / 360          # violet
NEUTRAL_HUE = 250 / 360         # a cool, faintly violet grey


def light_rgb(r, g, b, alpha=None):
    """One color of the Dark look as the Modern look's color."""
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if alpha is not None and r == g == b and r < 60:                       # a dark shadow or scrim: a soft violet-grey one
        return f"rgba(30, 22, 70, {round(min(float(alpha), 1) * 0.35, 3):g})"
    if alpha is not None and r > 200 and g > 200 and b > 200:              # a white veil on dark: a dark veil on light
        return f"rgba(30, 22, 70, {round(min(float(alpha), 1) * 0.6, 3):g})"
    if 20 / 360 <= h <= 40 / 360 and s > 0.45 and l > 0.06:                 # the orange accent family -> violet
        if alpha is not None:
            return f"rgba(108, 78, 255, {alpha})"
        if l < 0.28:
            h2, l2, s2 = ACCENT_HUE, 0.95 - l * 0.4, 0.9                   # its dark tints: pale violet backgrounds
        elif l < 0.62:
            h2, l2, s2 = ACCENT_HUE, 0.6, 0.95                             # the accent itself
        else:
            h2, l2, s2 = ACCENT_HUE, 0.42, 0.75                            # light orange text: deep violet text
    elif s < 0.2 or (l < 0.2 and s < 0.5):                                 # neutrals: light and dark trade places
        h2, s2 = NEUTRAL_HUE, 0.3
        if l < 0.05:
            l2 = 0.99                                                      # the page
        elif l < 0.32:
            l2 = 1 - l * 0.33                                              # panels, raised panels, borders: near-white
        elif l <= 0.6:
            l2, s2 = 0.74 - (l - 0.32) * 1.2, 0.16                         # mid greys (lines, faint text): still readable on white
        elif l <= 0.85:
            l2, s2 = max(0.3, 1 - l), 0.16                                 # muted text stays readable
        else:
            l2, s2 = 0.12, 0.2                                             # light text: near-black ink
        if r == g == b == 255:
            l2 = 0.1
    else:                                                                  # green, red, blue, yellow: same meaning, flipped lightness
        h2, s2 = h, min(1, s * 1.05)
        l2 = 1 - l
        if l < 0.36:
            l2 = 0.92 + (0.36 - l) * 0.18                                  # their dark tints: pale backgrounds
        elif l > 0.6:
            l2 = max(0.26, 1 - l - 0.05)                                   # their light text: deep text
    r2, g2, b2 = (round(c * 255) for c in colorsys.hls_to_rgb(h2, min(l2, 0.995), s2))
    if alpha is not None:
        return f"rgba({r2}, {g2}, {b2}, {alpha})"
    return f"#{r2:02x}{g2:02x}{b2:02x}"


def map_value(value):
    def hex_sub(m):
        digits = m.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        alpha = round(int(digits[6:8], 16) / 255, 3) if len(digits) == 8 else None
        return light_rgb(r, g, b, alpha)
    value = HEX.sub(hex_sub, value)
    value = RGB.sub(lambda m: light_rgb(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)), value)
    value = NAMED.sub(lambda m: "#1a1530" if m.group(1) == "white" else "#ffffff", value)
    return value


def has_color(value):
    return bool(HEX.search(value) or RGB.search(value) or NAMED.search(value))


def scope(selector):
    parts = []
    for part in split_top(selector, ","):
        part = part.strip()
        if not part:
            continue
        if part == ":root" or part == "html":
            parts.append(SCOPE)
        elif part.startswith(":root") or part.startswith("html"):
            parts.append(SCOPE + part[len(":root" if part.startswith(":root") else "html"):])
        else:
            parts.append(f"{SCOPE} {part}")
    return ", ".join(parts)


def split_top(text, sep):
    """Split on sep outside brackets and parentheses."""
    out, depth, buf = [], 0, ""
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(buf); buf = ""
        else:
            buf += ch
    out.append(buf)
    return out


def blocks(css):
    """(prelude, body) for each top-level block; bodies of @media / @supports are blocks too."""
    i, n = 0, len(css)
    while i < n:
        start = css.find("{", i)
        if start < 0:
            return
        prelude = css[i:start].strip()
        depth, j = 1, start + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        yield prelude, css[start + 1:j - 1]
        i = j


def declarations(body):
    for decl in split_top(body, ";"):
        if ":" not in decl:
            continue
        prop, value = decl.split(":", 1)
        yield prop.strip(), value.strip()


def convert(css, indent=""):
    out = []
    for prelude, body in blocks(css):
        if prelude.startswith("@keyframes") or prelude.startswith("@font-face") or prelude.startswith("@import"):
            continue
        if prelude.startswith("@media") or prelude.startswith("@supports"):
            inner = convert(body, indent + "  ")
            if inner:
                out.append(f"{indent}{prelude} {{\n{inner}\n{indent}}}")
            continue
        kept = [f"{prop}: {'light' if prop.lower() == 'color-scheme' else map_value(value)}" for prop, value in declarations(body)
                if COLOR_PROPS.match(prop.lower()) and (has_color(value) or prop.lower() == "color-scheme")]
        if kept and not prelude.startswith("@"):
            out.append(f"{indent}{scope(prelude)} {{ " + "; ".join(kept) + "; }")
    return "\n".join(out)


def build():
    css = re.sub(r"/\*.*?\*/", "", open(SOURCE, encoding="utf-8").read(), flags=re.S)
    return ("/* GENERATED by tools/build_modern_css.py from style.css: do not edit by hand (edit style.css, then run it). */\n"
            + convert(css) + "\n")


if __name__ == "__main__":
    text = build()
    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"wrote {os.path.relpath(TARGET, ROOT)}: {text.count(chr(10))} lines")
    sys.exit(0)
