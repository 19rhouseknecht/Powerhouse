"""Lighting layouts for a room, the way lighting designers size them: the lumen method (IESNA zonal cavity), then an even
grid. Pure functions (no Flask, no database) so the numbers can be tested on their own.

    fixtures = target foot-candles x floor area / (lumens per fixture x coefficient of utilization x light loss factor)

The coefficient of utilization (CU) is how much of a fixture's light reaches the work plane: it falls as the room cavity ratio
RCR = 5 x (ceiling height - work plane) x (length + width) / (length x width) rises (tall, narrow rooms lose more light to the
walls). CU_TABLE is for recessed / surface LED downlights and flat panels with typical ceiling 80 %, walls 50 %, floor 20 %
reflectance. The light loss factor allows for LED depreciation and dirt over time.
Targets are IES recommended average maintained illuminance for the space's general lighting (foot-candles, horizontal);
counters, desks and benches get their own task lighting on top (under-cabinet, pendants, task lights).
"""
import math
import re

ROOM_TYPES = {
    # key: (label, target foot-candles, work plane height in ft)
    "living": ("Living / family room", 20, 2.5),
    "kitchen": ("Kitchen", 30, 3.0),
    "dining": ("Dining room", 20, 2.5),
    "bedroom": ("Bedroom", 15, 2.5),
    "bathroom": ("Bathroom", 20, 2.5),
    "hallway": ("Hallway / stairs", 10, 0.0),
    "laundry": ("Laundry / utility", 30, 3.0),
    "office": ("Home office", 30, 2.5),
    "basement": ("Finished basement", 20, 2.5),
    "garage": ("Garage", 30, 2.5),
    "workshop": ("Workshop", 50, 3.0),
    "commercial_office": ("Commercial office", 30, 2.5),
    "retail": ("Retail / showroom", 50, 3.0),
    "classroom": ("Classroom", 40, 2.5),
    "warehouse": ("Warehouse / storage", 20, 0.0),
    "exterior": ("Covered porch / exterior", 5, 0.0),
}
CU_TABLE = [(0, 0.95), (1, 0.86), (2, 0.78), (3, 0.71), (4, 0.65), (5, 0.59), (6, 0.54), (7, 0.50), (8, 0.46), (9, 0.42), (10, 0.39)]
LIGHT_LOSS_FACTOR = 0.85          # LED lumen depreciation (0.90) x dirt (0.95), rounded down
SPACING_CRITERION = 1.5           # most downlights and panels: spacing no more than 1.5 x mounting height above the work plane
SHAPES = {"rect": "Rectangle", "circle": "Round"}
FIELDS = {
    # name: (label, default, minimum, maximum)
    "length": ("Length, or diameter for a round room (ft)", 14, 2, 400),
    "width": ("Width (ft, rectangle)", 12, 2, 400),
    "height": ("Ceiling height (ft)", 9, 6, 60),
}
LUMENS_IN_NAME = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:lm|lumens?)\b", re.IGNORECASE)


def lumens_from_name(name):
    """Lumens stated in an item's name ("6in LED wafer 1200 lm", "4ft panel 5,000 lumens"), else None."""
    match = LUMENS_IN_NAME.search(str(name or ""))
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value if 50 <= value <= 200000 else None


def read_room(values):
    room = {}
    for name, (label, default, low, high) in FIELDS.items():
        try:
            number = float(str(values.get(name, "")).strip() or default)
        except ValueError:
            number = float(default)
        room[name] = min(max(number, low), high)
    room["shape"] = values.get("shape") if values.get("shape") in SHAPES else "rect"
    if room["shape"] == "circle":
        room["width"] = room["length"]                  # a round room: length is its diameter
    kind = values.get("type") if values.get("type") in ROOM_TYPES else "living"
    label, target, plane = ROOM_TYPES[kind]
    try:
        typed = float(str(values.get("target", "")).strip())
        target = min(max(typed, 1), 300)
    except ValueError:
        pass
    room.update({"type": kind, "type_label": label, "target": target, "work_plane": min(plane, room["height"] - 1),
                 "name": " ".join(str(values.get("name") or label).split())[:80]})
    return room


def floor_area(room):
    if room.get("shape") == "circle":
        return math.pi * (room["length"] / 2) ** 2
    return room["length"] * room["width"]


def perimeter(room):
    if room.get("shape") == "circle":
        return math.pi * room["length"]
    return 2 * (room["length"] + room["width"])


def room_cavity_ratio(room):
    """RCR = 2.5 x cavity height x perimeter / floor area (for a rectangle: 5 x h x (L + W) / (L x W))."""
    cavity = max(room["height"] - room["work_plane"], 0.5)
    return 2.5 * cavity * perimeter(room) / floor_area(room)


def coefficient_of_utilization(rcr):
    if rcr >= CU_TABLE[-1][0]:
        return CU_TABLE[-1][1]
    for (r0, c0), (r1, c1) in zip(CU_TABLE, CU_TABLE[1:]):
        if r0 <= rcr <= r1:
            return c0 + (c1 - c0) * (rcr - r0) / (r1 - r0)
    return CU_TABLE[0][1]


def grid(count, room):
    """Rows x columns for at least `count` fixtures, spaced as evenly as the room's shape allows, never wider than the
    spacing criterion (more fixtures are added when needed so the light is even)."""
    length, width = room["length"], room["width"]
    max_spacing = SPACING_CRITERION * max(room["height"] - room["work_plane"], 1)
    best = None
    for rows in range(1, count + 1):
        cols = math.ceil(count / rows)
        along, across = length / cols, width / rows
        score = (rows * cols - count) / count * 2 + abs(math.log(along / across))      # even spacing first, few extras
        if best is None or score < best[0]:
            best = (score, rows, cols)
    _, rows, cols = best
    while length / cols > max_spacing:
        cols += 1
    while width / rows > max_spacing:
        rows += 1
    return rows, cols


def rings(count, room):
    """A round room: one light in the middle and rings around it, each ring's share growing with its circumference; a small
    count is one ring. Never spaced wider than the spacing criterion (lights are added until they are not).
    Returns (positions, spacing)."""
    radius = room["length"] / 2
    max_spacing = SPACING_CRITERION * max(room["height"] - room["work_plane"], 1)

    def place(n):
        if n == 1:
            return [[radius, radius]], radius
        if n <= 6:                                              # one ring, halfway out
            r = radius / 2
            return [[radius + r * math.cos(2 * math.pi * k / n), radius + r * math.sin(2 * math.pi * k / n)] for k in range(n)], \
                max(r, 2 * math.pi * r / n)
        spacing = math.sqrt(math.pi * radius ** 2 / n)
        ring_count = max(1, round(radius / spacing))
        step = radius / (ring_count + 0.5)
        weights = sum(range(1, ring_count + 1))
        points, widest = [[radius, radius]], step
        for k in range(1, ring_count + 1):
            around = max(3, round((n - 1) * k / weights))
            r, turn = k * step, (k % 2) * math.pi / max(3, round((n - 1) * k / weights))
            points += [[radius + r * math.cos(turn + 2 * math.pi * j / around), radius + r * math.sin(turn + 2 * math.pi * j / around)] for j in range(around)]
            widest = max(widest, 2 * math.pi * r / around)
        return points, widest

    n = count
    while True:
        points, spacing = place(n)
        if spacing <= max_spacing or n > count * 4:
            return [[round(x, 2), round(y, 2)] for x, y in points], spacing
        n += 1


def layout(lumens, room):
    """How many of a fixture the room needs, where they go, and the light they give."""
    area = floor_area(room)
    rcr = room_cavity_ratio(room)
    cu = coefficient_of_utilization(rcr)
    needed = max(1, math.ceil(room["target"] * area / (lumens * cu * LIGHT_LOSS_FACTOR)))
    if room.get("shape") == "circle":
        positions, spacing = rings(needed, room)
        rows, cols, count = 0, 0, len(positions)
        gap = [round(spacing, 2), round(spacing, 2)]
    else:
        rows, cols = grid(needed, room)
        count = rows * cols
        positions = [[round(room["length"] / cols * (c + 0.5), 2), round(room["width"] / rows * (r + 0.5), 2)] for r in range(rows) for c in range(cols)]
        gap = [round(room["length"] / cols, 2), round(room["width"] / rows, 2)]
    achieved = count * lumens * cu * LIGHT_LOSS_FACTOR / area
    return {"needed": needed, "count": count, "rows": rows, "cols": cols, "positions": positions, "shape": room.get("shape", "rect"),
            "spacing": gap,
            "footcandles": round(achieved, 1), "target": room["target"], "rcr": round(rcr, 2), "cu": round(cu, 3),
            "added_for_spacing": count - needed if count > needed else 0}


def plans(room, fixtures):
    """One plan per fixture: fixtures are dicts with id, name, lumens, price, labor (hours each), unit, image."""
    result = []
    for fixture in fixtures:
        if not fixture.get("lumens") or fixture["lumens"] <= 0:
            continue
        plan = layout(float(fixture["lumens"]), room)
        plan.update({"id": fixture["id"], "name": fixture["name"], "lumens": fixture["lumens"], "price": fixture.get("price", 0),
                     "material": round(plan["count"] * (fixture.get("price") or 0), 2), "labor_hours": round(plan["count"] * (fixture.get("labor") or 0), 2),
                     "image": fixture.get("image", ""), "unconfirmed": fixture.get("unconfirmed", False)})
        result.append(plan)
    result.sort(key=lambda plan: (plan["material"], plan["count"]))
    return result


# ---------------- the room, read from the words of a quote request ----------------
# "Lighting layout" ticked on a quote: the room comes from what was typed ("kitchen 14x12 with 9 ft ceilings",
# "18 ft round sunroom", "garage 24 by 24, 10' ceiling, 50 fc"). Plain pattern reading, no AI. Without a length and width
# there is no room, and the person is asked for it.
FT = r"(?:'|’|\s*ft\.?|\s*feet|\s*foot)?"
NUMBER = r"(\d+(?:\.\d+)?)"
ROOM_WORDS = [
    ("commercial_office", r"commercial office|open office"), ("kitchen", r"kitchen"), ("dining", r"dining"),
    ("bedroom", r"bed ?room|master suite"), ("bathroom", r"bath ?room|bath\b|powder room"), ("hallway", r"hallway|hall\b|stairs?|stairway|corridor"),
    ("laundry", r"laundry|utility room|mud ?room"), ("office", r"office|study|den\b"), ("basement", r"basement"),
    ("garage", r"garage"), ("workshop", r"workshop|shop\b|work ?bench"), ("retail", r"retail|store\b|showroom"),
    ("classroom", r"classroom"), ("warehouse", r"warehouse|storage"), ("exterior", r"porch|patio|exterior|outside|pavilion"),
    ("living", r"living|family room|great room|den|sunroom|bonus room|rec room"),
]


def parse_room(text):
    """The room (values for read_room) from free text, or None when no length and width (or diameter) are given.
    The second value says which parts were assumed."""
    words = " ".join(str(text or "").lower().split())
    values, assumed = {}, []
    round_match = re.search(NUMBER + FT + r"\s*(?:round|circular|diameter|across)", words) or re.search(r"(?:round|circular)[a-z ]{0,20}?" + NUMBER + FT, words)
    rect = re.search(NUMBER + FT + r"\s*(?:x|by|×|\*)\s*" + NUMBER + FT + r"(?:\s*(?:x|by|×|\*)\s*" + NUMBER + FT + r")?", words)
    if rect:
        values.update(shape="rect", length=rect.group(1), width=rect.group(2))
        if rect.group(3):
            values["height"] = rect.group(3)
    elif round_match:
        values.update(shape="circle", length=round_match.group(1))
    else:
        return None, []
    height = re.search(NUMBER + FT + r"\s*(?:high |tall |vaulted |flat )?(?:ceilings?|high\b|tall\b)", words) or \
        re.search(r"ceilings?(?: height| heights)?(?: is| are| of| at|:)?\s*" + NUMBER, words)
    if "height" not in values:
        if height:
            values["height"] = height.group(1)
        else:
            values["height"] = "8"; assumed.append("an 8 ft ceiling")
    target = re.search(NUMBER + r"\s*(?:fc\b|foot[- ]?candles?)", words)
    if target:
        values["target"] = target.group(1)
    kind = next((key for key, pattern in ROOM_WORDS if re.search(r"\b(?:" + pattern + r")", words)), None)
    if kind is None:
        kind = "living"; assumed.append("general living space")
    values["type"] = kind
    named = re.search(r"\b(" + "|".join(pattern for _, pattern in ROOM_WORDS) + r")", words)
    values["name"] = named.group(1).title() if named else ROOM_TYPES[kind][0]
    return values, assumed
