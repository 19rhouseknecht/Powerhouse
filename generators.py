"""Generator sizing: what a home needs, worked out the way installers do, and which Kohler air-cooled
model covers it. Pure functions (no Flask, no database) so the numbers can be tested on their own.

The method:
    running load   = everything chosen, running at once (general lighting and receptacles from square footage,
                     NEC 220.12 at 3 VA/sq ft with the 220.42 demand factors: first 3,000 VA at 100 %, the rest at 35 %)
    starting load  = the largest motor starting (from the nameplate LRA when given) + everything else running
    required       = the larger of running x 1.1 (10 % headroom) and the starting load
    recommendation = the smallest model in KOHLER_MODELS whose rating on the chosen fuel covers "required"

Model ratings are kW on LP / natural gas from Kohler spec sheets and dealer listings. Check the current spec sheet
before ordering; edit KOHLER_MODELS when Kohler changes its lineup.
"""

# (model, kW on LP, kW on natural gas, description)
KOHLER_MODELS = [
    ("14RCA", 14.0, 12.0, "Kohler 14 kW air-cooled home standby"),
    ("20RCA", 20.0, 18.0, "Kohler 20 kW air-cooled home standby"),
    ("26RCA", 26.0, 24.0, "Kohler 26 kW air-cooled home standby"),
]
HEADROOM = 1.10
VOLTS = 240.0

# typical figures used when the nameplate is not entered (kW running, starting multiple)
AC_KW_PER_TON = 1.15          # a modern split system, running
AC_START_MULTIPLE = 3.0       # without a soft starter
SOFT_START_FACTOR = 0.4       # a soft starter cuts the inrush to about 40 %
MOTOR_KW_PER_HP = 1.0         # small single-phase motors (pumps), running
MOTOR_START_MULTIPLE = 3.0

FIELDS = {
    # name: (label, default, minimum, maximum)
    "square_feet": ("Heated square footage", 2000, 0, 20000),
    "ac_count": ("Central A/C units", 1, 0, 4),
    "ac_tons": ("Tons per A/C unit", 3.0, 0, 6),
    "ac_lra": ("A/C nameplate LRA (largest unit)", 0, 0, 300),
    "heat_pump_count": ("Heat pumps", 0, 0, 4),
    "heat_pump_tons": ("Tons per heat pump", 3.0, 0, 6),
    "heat_strip_kw": ("Electric heat / heat strips (kW total)", 0, 0, 30),
    "furnace_blowers": ("Gas furnace blowers", 1, 0, 4),
    "water_heater_kw": ("Electric water heater (kW)", 4.5, 0, 18),
    "well_pump_hp": ("Well pump (HP)", 0, 0, 5),
    "sump_pump_hp": ("Sump pump (HP)", 0.5, 0, 2),
    "range_kw": ("Electric range / oven (nameplate kW)", 0, 0, 20),
    "dryer_kw": ("Electric dryer (kW)", 5.0, 0, 8),
    "refrigerators": ("Refrigerators and freezers", 2, 0, 6),
    "ev_kw": ("EV charger (kW)", 0, 0, 19.2),
    "pool_pump_hp": ("Pool pump (HP)", 0, 0, 3),
    "hot_tub_kw": ("Hot tub (kW)", 0, 0, 12),
    "other_kw": ("Other loads, running (kW)", 0, 0, 50),
}
CHOICES = {
    "coverage": (("whole", "Whole home"), ("essentials", "Essential circuits only")),
    "fuel": (("ng", "Natural gas"), ("lp", "Propane (LP)")),
    "service_amps": (("100", "100 A"), ("150", "150 A"), ("200", "200 A"), ("320", "320 A"), ("400", "400 A")),
    "soft_start": (("no", "No"), ("yes", "Yes")),
}
DEFAULT_CHOICES = {"coverage": "whole", "fuel": "ng", "service_amps": "200", "soft_start": "no"}
SHEDDABLE = ("water_heater_kw", "dryer_kw", "ev_kw", "hot_tub_kw", "pool_pump_hp", "heat_strip_kw", "range_kw")


def read_inputs(values):
    """Clean form values into numbers and choices (anything odd becomes its default, then is clamped)."""
    inputs = {}
    for name, (label, default, low, high) in FIELDS.items():
        raw = values.get(name, "")
        try:
            number = float(str(raw).replace(",", "")) if str(raw).strip() != "" else float(default)
        except ValueError:
            number = float(default)
        inputs[name] = min(max(number, low), high)
    for name, options in CHOICES.items():
        allowed = {value for value, _ in options}
        inputs[name] = values.get(name) if values.get(name) in allowed else DEFAULT_CHOICES[name]
    return inputs


def general_lighting_kw(square_feet):
    """NEC 220.12 (3 VA/sq ft) with the 220.42 dwelling demand factors, in kW."""
    va = 3.0 * square_feet
    return (min(va, 3000) + max(va - 3000, 0) * 0.35) / 1000


def loads(inputs):
    """[(name, running kW, starting kW, sheddable)] for everything chosen."""
    rows = []
    soft = SOFT_START_FACTOR if inputs["soft_start"] == "yes" else 1.0
    if inputs["coverage"] == "whole":
        rows.append(("Lighting and receptacles (%d sq ft)" % inputs["square_feet"], general_lighting_kw(inputs["square_feet"]), 0.0, False))
    else:
        rows.append(("Essential lighting and receptacles", 2.0, 0.0, False))
    for index in range(int(inputs["ac_count"])):
        running = inputs["ac_tons"] * AC_KW_PER_TON
        lra_start = inputs["ac_lra"] * VOLTS / 1000 if inputs["ac_lra"] and index == 0 else 0
        start = (lra_start or running * AC_START_MULTIPLE) * soft
        rows.append(("Central A/C %d (%g ton%s)" % (index + 1, inputs["ac_tons"], "" if inputs["ac_tons"] == 1 else "s"), running, start, False))
    for index in range(int(inputs["heat_pump_count"])):
        running = inputs["heat_pump_tons"] * AC_KW_PER_TON
        rows.append(("Heat pump %d (%g tons)" % (index + 1, inputs["heat_pump_tons"]), running, running * AC_START_MULTIPLE * soft, False))
    if inputs["heat_strip_kw"]:
        rows.append(("Electric heat / heat strips", inputs["heat_strip_kw"], 0.0, True))
    for index in range(int(inputs["furnace_blowers"])):
        rows.append(("Furnace blower %d" % (index + 1), 0.8, 2.4, False))
    if inputs["water_heater_kw"]:
        rows.append(("Electric water heater", inputs["water_heater_kw"], 0.0, True))
    for name, label in (("well_pump_hp", "Well pump"), ("sump_pump_hp", "Sump pump"), ("pool_pump_hp", "Pool pump")):
        if inputs[name]:
            running = inputs[name] * MOTOR_KW_PER_HP
            rows.append(("%s (%g HP)" % (label, inputs[name]), running, running * MOTOR_START_MULTIPLE, name in SHEDDABLE))
    if inputs["range_kw"]:
        rows.append(("Electric range (demand)", min(inputs["range_kw"], 8.0), 0.0, True))      # NEC 220.55 Column C: 8 kW for one range up to 12 kW
    if inputs["dryer_kw"]:
        rows.append(("Electric dryer", max(inputs["dryer_kw"], 5.0), 0.0, True))
    if inputs["refrigerators"]:
        rows.append(("Refrigerators / freezers (%d)" % inputs["refrigerators"], 0.2 * inputs["refrigerators"], 1.2, False))
    if inputs["ev_kw"]:
        rows.append(("EV charger", inputs["ev_kw"], 0.0, True))
    if inputs["hot_tub_kw"]:
        rows.append(("Hot tub", inputs["hot_tub_kw"], 0.0, True))
    if inputs["other_kw"]:
        rows.append(("Other loads", inputs["other_kw"], 0.0, False))
    return rows


def requirement(rows):
    """(running kW, starting kW, required kW, the motor that sets the start) for a list of loads."""
    running = sum(row[1] for row in rows)
    motors = [row for row in rows if row[2] > row[1]]
    largest = max(motors, key=lambda row: row[2] - row[1], default=None)
    starting = running - largest[1] + largest[2] if largest else running
    return round(running, 2), round(starting, 2), round(max(running * HEADROOM, starting), 2), (largest[0] if largest else "")


def pick_model(required_kw, fuel):
    for model, lp, ng, description in KOHLER_MODELS:
        rated = lp if fuel == "lp" else ng
        if rated >= required_kw:
            return {"model": model, "rated_kw": rated, "description": description}
    return None


def size(values):
    """Everything the sizing page shows, from the form's values."""
    inputs = read_inputs(values)
    rows = loads(inputs)
    running, starting, required, sets_start = requirement(rows)
    shed_rows = [row for row in rows if not row[3]]
    shed_running, shed_starting, shed_required, _ = requirement(shed_rows)
    model = pick_model(required, inputs["fuel"])
    shed_model = pick_model(shed_required, inputs["fuel"])
    notes = []
    if model is None:
        notes.append("More than the largest air-cooled model covers. Use load management (see below) or size one of Kohler's "
                     "liquid-cooled generators (the 24RCLA and up) with Kohler's own tool or your Kohler distributor.")
    if inputs["ac_count"] and not inputs["ac_lra"]:
        notes.append("A/C starting load is estimated (3 x running%s). Enter the nameplate LRA for the real figure." % (", reduced by the soft starter" if inputs["soft_start"] == "yes" else ""))
    if int(inputs["service_amps"]) >= 300:
        notes.append("A %s A service needs a transfer switch rated for it (or two 200 A switches)." % inputs["service_amps"])
    return {
        "inputs": inputs, "rows": rows, "running_kw": running, "starting_kw": starting, "required_kw": required, "sets_start": sets_start,
        "model": model, "with_load_management": {"required_kw": shed_required, "model": shed_model, "shed": [row[0] for row in rows if row[3]]},
        "transfer_switch_amps": int(inputs["service_amps"]), "notes": notes,
    }


# ======================================================================================================
# Upgrading: what is there now, what is going in, and what the wiring and gas line have to be for it.
# ======================================================================================================
# Every figure is from the maker's spec sheet (single phase, 120/240 V, 60 Hz):
#   kW LP / NG, rated current at 240 V on LP (the larger), the generator's own line breaker, full-load fuel use
#   (natural gas cfh, LP cfh), the fuel inlet, the inlet pressure range (in. water column), the weight in lb.
# A model whose breaker is not on its spec sheet carries breaker_checked=False and the page says to read the nameplate.
UPGRADE_MODELS = [
    # Generac Guardian, current (2024 on)
    {"key": "generac-10", "brand": "Generac", "model": "Guardian 10 kW (7171/7321)", "kw_lp": 10, "kw_ng": 9, "amps": 42, "breaker": 45,
     "ng_cfh": 132, "lp_cfh": 58, "inlet": "3/4 in (flexible connector)", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 334, "cooling": "air"},
    {"key": "generac-14", "brand": "Generac", "model": "Guardian 14 kW (7223/7323)", "kw_lp": 14, "kw_ng": 14, "amps": 58, "breaker": 60,
     "ng_cfh": 243, "lp_cfh": 106, "inlet": "3/4 in (flexible connector)", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 391, "cooling": "air"},
    {"key": "generac-18", "brand": "Generac", "model": "Guardian 18 kW (7226/7324)", "kw_lp": 18, "kw_ng": 17, "amps": 75, "breaker": 80,
     "ng_cfh": 235, "lp_cfh": 105, "inlet": "3/4 in (flexible connector)", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 426, "cooling": "air"},
    {"key": "generac-22", "brand": "Generac", "model": "Guardian 22 kW (7260/7325)", "kw_lp": 22, "kw_ng": 21, "amps": 92, "breaker": 100,
     "ng_cfh": 311, "lp_cfh": 135, "inlet": "3/4 in (14 in flexible connector)", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 451, "cooling": "air"},
    {"key": "generac-24", "brand": "Generac", "model": "Guardian 24 kW (7261/7326)", "kw_lp": 24, "kw_ng": 22.5, "amps": 100, "breaker": 100,
     "ng_cfh": 291, "lp_cfh": 135, "inlet": "3/4 in (14 in flexible connector)", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 461, "cooling": "air"},
    {"key": "generac-26", "brand": "Generac", "model": "Guardian 26 kW (7327/7328)", "kw_lp": 26, "kw_ng": 24, "amps": 108, "breaker": 110,
     "ng_cfh": 316, "lp_cfh": 144, "inlet": "3/4 in (14 in flexible connector)", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 524, "cooling": "air"},
    {"key": "generac-28", "brand": "Generac", "model": "Guardian 28 kW (7282/7329)", "kw_lp": 28, "kw_ng": 25, "amps": 116, "breaker": 125,
     "ng_cfh": 297, "lp_cfh": 144, "inlet": "3/4 in (14 in flexible connector)", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 524, "cooling": "air"},
    # Generac Guardian, earlier units you will find in the field
    {"key": "generac-16-7036", "brand": "Generac", "model": "Guardian 16 kW (7035/7036/7037, earlier)", "kw_lp": 16, "kw_ng": 16, "amps": 67, "breaker": 70,
     "ng_cfh": 309, "lp_cfh": 107, "inlet": "3/4 in", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 409, "cooling": "air"},
    {"key": "generac-20-7039", "brand": "Generac", "model": "Guardian 20 kW (7038/7039, earlier)", "kw_lp": 20, "kw_ng": 18, "amps": 83, "breaker": 90,
     "ng_cfh": 301, "lp_cfh": 130, "inlet": "3/4 in", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 448, "cooling": "air"},
    {"key": "generac-20-7040", "brand": "Generac", "model": "Guardian 20 kW (7040/7041, earlier)", "kw_lp": 20, "kw_ng": 18, "amps": 83, "breaker": 100,
     "ng_cfh": 282, "lp_cfh": 126, "inlet": "3/4 in", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 471, "cooling": "air"},
    {"key": "generac-22-7043", "brand": "Generac", "model": "Guardian 22 kW (7042/7043, earlier)", "kw_lp": 22, "kw_ng": 19.5, "amps": 92, "breaker": 100,
     "ng_cfh": 327, "lp_cfh": 142, "inlet": "3/4 in", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 466, "cooling": "air"},
    # Kohler (Rehlko) home standby
    {"key": "kohler-14rca", "brand": "Kohler", "model": "14RCA (14 kW)", "kw_lp": 14, "kw_ng": 12, "amps": 58, "breaker": 70,
     "ng_cfh": 193, "lp_cfh": 81, "inlet": "1/2 in NPT", "ng_wc": (5, 11), "lp_wc": (7, 11), "lb": 440, "cooling": "air"},
    {"key": "kohler-20rca", "brand": "Kohler", "model": "20RCA (20 kW)", "kw_lp": 20, "kw_ng": 18, "amps": 84, "breaker": 100,
     "ng_cfh": 281, "lp_cfh": 136, "inlet": "1/2 in NPT", "ng_wc": (3.5, 11), "lp_wc": (7, 11), "lb": 555, "cooling": "air"},
    {"key": "kohler-26rca", "brand": "Kohler", "model": "26RCA (26 kW)", "kw_lp": 26, "kw_ng": 24, "amps": 109, "breaker": 125,
     "ng_cfh": 290, "lp_cfh": 129, "inlet": "3/4 in NPT", "ng_wc": (3.5, 11), "lp_wc": (7, 11), "lb": 625, "cooling": "air"},
    {"key": "kohler-24rcla", "brand": "Kohler", "model": "24RCLA (24 kW, liquid-cooled)", "kw_lp": 24, "kw_ng": 21, "amps": 100, "breaker": 125,
     "breaker_checked": False, "ng_cfh": 301, "lp_cfh": 113, "inlet": "1 in NPT", "ng_wc": (5, 11), "lp_wc": (5, 11), "lb": 1260, "cooling": "liquid"},
]
MODEL_BY_KEY = {row["key"]: row for row in UPGRADE_MODELS}
NG_BTU_PER_CF, LP_BTU_PER_CF, LP_CF_PER_GAL = 1000, 2500, 36.39

# NEC Table 310.16, 75 °C column (generator and transfer switch terminals are rated 75 °C): size -> ampacity
WIRE_SIZES = ["14", "12", "10", "8", "6", "4", "3", "2", "1", "1/0", "2/0", "3/0", "4/0"]
AMPACITY = {
    "copper": {"14": 20, "12": 25, "10": 35, "8": 50, "6": 65, "4": 85, "3": 100, "2": 115, "1": 130, "1/0": 150, "2/0": 175, "3/0": 200, "4/0": 230},
    "aluminum": {"12": 20, "10": 30, "8": 40, "6": 50, "4": 65, "3": 75, "2": 90, "1": 100, "1/0": 120, "2/0": 135, "3/0": 155, "4/0": 180},
}
CIRCULAR_MILS = {"14": 4110, "12": 6530, "10": 10380, "8": 16510, "6": 26240, "4": 41740, "3": 52620, "2": 66360, "1": 83690,
                 "1/0": 105600, "2/0": 133100, "3/0": 167800, "4/0": 211600}
K_OHMS = {"copper": 12.9, "aluminum": 21.2}          # ohm-cmil/ft, the usual voltage-drop constant
STANDARD_BREAKERS = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 110, 125, 150, 175, 200, 225, 250]
# NEC Table 250.122: equipment grounding conductor by the breaker ahead of it
GROUND_BY_BREAKER = [(15, "14", "12"), (20, "12", "10"), (60, "10", "8"), (100, "8", "6"), (200, "6", "4"), (300, "4", "2")]
# NEC Chapter 9 Table 5, THHN/THWN-2 area (sq in), and Table 4, 40 % fill for three or more wires
THHN_AREA = {"14": .0097, "12": .0133, "10": .0211, "8": .0366, "6": .0507, "4": .0824, "3": .0973, "2": .1158, "1": .1562,
             "1/0": .1855, "2/0": .2223, "3/0": .2679, "4/0": .3237}
CONDUIT_FILL = {"PVC 40": [("1/2", .114), ("3/4", .203), ("1", .333), ("1-1/4", .581), ("1-1/2", .794), ("2", 1.316)],
                "EMT": [("1/2", .122), ("3/4", .213), ("1", .346), ("1-1/4", .598), ("1-1/2", .814), ("2", 1.342)]}
CONTROL_AREA = .02                                   # the control / communication cable that rides with the feeders
VOLTAGE_DROP_LIMIT = 3.0

# Gas pipe capacity, Schedule 40 steel (IFGC 402.4 / NFPA 54 6.2.1 tables): length (ft) -> capacity per size.
#   Natural gas: low pressure (under 2 psi), 0.5 in. w.c. drop, 0.60 specific gravity, in cubic feet per hour.
#   Propane (LP): undiluted, 11 in. w.c. inlet, 0.5 in. w.c. drop, in thousands of Btu per hour.
PIPE_SIZES = ["1/2", "3/4", "1", "1-1/4", "1-1/2", "2"]
NG_LENGTHS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175, 200]
NG_CFH = {
    "1/2": [172, 118, 95, 81, 72, 65, 60, 56, 52, 50, 44, 40, 37, 34],
    "3/4": [360, 247, 199, 170, 151, 137, 126, 117, 110, 104, 92, 83, 77, 71],
    "1": [678, 466, 374, 320, 284, 257, 237, 220, 207, 195, 173, 157, 144, 134],
    "1-1/4": [1390, 957, 768, 657, 583, 528, 486, 452, 424, 400, 355, 322, 296, 275],
    "1-1/2": [2090, 1430, 1150, 985, 873, 791, 728, 677, 635, 600, 532, 482, 443, 412],
    "2": [4020, 2760, 2220, 1900, 1680, 1520, 1400, 1300, 1220, 1160, 1020, 928, 854, 794],
}
LP_LENGTHS = [10, 20, 30, 40, 50, 60, 80, 100, 125, 150, 200]
LP_KBTU = {
    "1/2": [291, 200, 160, 137, 122, 110, 94, 84, 74, 67, 58],
    "3/4": [608, 418, 336, 287, 255, 231, 197, 175, 155, 140, 120],
    "1": [1150, 787, 632, 541, 480, 434, 372, 330, 292, 265, 227],
    "1-1/4": [2350, 1620, 1300, 1110, 985, 892, 763, 677, 600, 543, 465],
    "1-1/2": [3520, 2420, 1940, 1660, 1480, 1340, 1140, 1010, 899, 814, 697],
    "2": [6790, 4660, 3750, 3210, 2840, 2570, 2200, 1950, 1730, 1570, 1340],
}

UPGRADE_FIELDS = {
    # name: (label, default, minimum, maximum)
    "wire_feet": ("Wire run: generator to transfer switch (ft, one way)", 30, 1, 500),
    "gas_feet": ("Gas run: meter / regulator or tank to generator (ft, with fittings)", 30, 1, 400),
    "other_btu": ("Other gas appliances on the same meter (Btu/h, total)", 0, 0, 2000000),
    "meter_cfh": ("Gas meter rating (cfh; 0 = not known)", 0, 0, 5000),
    "tank_gallons": ("Propane tank (gallons)", 500, 0, 5000),
}
UPGRADE_CHOICES = {
    "fuel": (("ng", "Natural gas"), ("lp", "Propane (LP)")),
    "material": (("copper", "Copper"), ("aluminum", "Aluminum")),
    "conduit": (("PVC 40", "PVC Sch 40"), ("EMT", "EMT")),
    "existing_material": (("", "Not known"), ("copper", "Copper"), ("aluminum", "Aluminum")),
    "existing_wire": tuple([("", "Not known")] + [(size, f"{size} AWG") for size in WIRE_SIZES[2:]]),
    "existing_pipe": tuple([("", "Not known")] + [(size, f"{size} in") for size in PIPE_SIZES]),
}
UPGRADE_DEFAULTS = {"old": "generac-20-7039", "new": "generac-26", "fuel": "ng", "material": "copper", "conduit": "PVC 40",
                    "existing_material": "", "existing_wire": "", "existing_pipe": ""}


def _number(values, name):
    label, default, low, high = UPGRADE_FIELDS[name]
    raw = str(values.get(name, "")).replace(",", "").strip()
    try:
        number = float(raw) if raw else float(default)
    except ValueError:
        number = float(default)
    return min(max(number, low), high)


def read_upgrade(values):
    inputs = {name: _number(values, name) for name in UPGRADE_FIELDS}
    for name in ("old", "new"):
        inputs[name] = values.get(name) if values.get(name) in MODEL_BY_KEY else UPGRADE_DEFAULTS[name]
    for name, options in UPGRADE_CHOICES.items():
        allowed = {value for value, _ in options}
        inputs[name] = values.get(name) if values.get(name) in allowed else UPGRADE_DEFAULTS[name]
    return inputs


def wire_ok(size, material, breaker):
    """A conductor may be protected by the breaker when its ampacity reaches it, or (NEC 240.4(B)) when its ampacity is not
    a standard breaker size and the breaker is the next standard size up."""
    ampacity = AMPACITY[material].get(size)
    if ampacity is None:
        return False
    if ampacity >= breaker:
        return True
    next_up = min((b for b in STANDARD_BREAKERS if b >= ampacity), default=None)
    return ampacity not in STANDARD_BREAKERS and next_up == breaker


def voltage_drop(size, material, amps, feet):
    return 2 * K_OHMS[material] * amps * feet / CIRCULAR_MILS[size] / 240 * 100


def ground_for(breaker, material, upsized_ratio=1.0):
    for limit, copper, aluminum in GROUND_BY_BREAKER:
        if breaker <= limit:
            size = copper if material == "copper" else aluminum
            break
    if upsized_ratio > 1.0:                           # NEC 250.122(B): upsize the ground in proportion to the hots
        need = CIRCULAR_MILS[size] * upsized_ratio
        size = next((s for s in WIRE_SIZES if CIRCULAR_MILS[s] >= need), size)
    return size


def conduit_for(sizes, kind):
    area = sum(THHN_AREA[size] for size in sizes) + CONTROL_AREA
    return next((trade for trade, fill in CONDUIT_FILL[kind] if fill >= area), "larger than 2 in")


def wiring(model, material, feet, conduit_kind):
    """The feeder from the generator's breaker to the transfer switch: two hots, a neutral, a ground, and the control cable."""
    breaker = model["breaker"]
    by_ampacity = next(size for size in WIRE_SIZES if wire_ok(size, material, breaker))
    size = by_ampacity
    drop = voltage_drop(size, material, model["amps"], feet)
    while drop > VOLTAGE_DROP_LIMIT and size != WIRE_SIZES[-1]:
        size = WIRE_SIZES[WIRE_SIZES.index(size) + 1]
        drop = voltage_drop(size, material, model["amps"], feet)
    ratio = CIRCULAR_MILS[size] / CIRCULAR_MILS[by_ampacity]
    ground = ground_for(breaker, material, ratio)
    return {"breaker": breaker, "size": size, "by_ampacity": by_ampacity, "upsized": size != by_ampacity, "ground": ground,
            "material": material, "ampacity": AMPACITY[material][size], "drop_pct": round(drop, 2),
            "conduit": conduit_for([size, size, size, ground], conduit_kind), "conduit_kind": conduit_kind,
            "text": f"{size} AWG {material} x 3 (two hots and a neutral) + {ground} AWG ground, on the {breaker} A breaker"}


def gas_demand(model, fuel):
    """Full-load fuel use: (Btu/h, cfh of that fuel)."""
    cfh = model["lp_cfh"] if fuel == "lp" else model["ng_cfh"]
    return cfh * (LP_BTU_PER_CF if fuel == "lp" else NG_BTU_PER_CF), cfh


def pipe_capacity(size, fuel, feet):
    """What a Schedule 40 run of this size carries at this length, in Btu/h (the next longer table row: never optimistic)."""
    lengths, table = (LP_LENGTHS, LP_KBTU) if fuel == "lp" else (NG_LENGTHS, NG_CFH)
    index = next((i for i, length in enumerate(lengths) if length >= feet), len(lengths) - 1)
    capacity = table[size][index]
    if feet > lengths[-1]:                             # past the table: capacity falls with the square root of length
        capacity = capacity * (lengths[-1] / feet) ** 0.5
    return capacity * 1000 if fuel == "lp" else capacity * NG_BTU_PER_CF


def pipe_for(btu, fuel, feet):
    return next((size for size in PIPE_SIZES if pipe_capacity(size, fuel, feet) >= btu), None)


def gas(model, fuel, feet):
    btu, cfh = gas_demand(model, fuel)
    size = pipe_for(btu, fuel, feet)
    return {"btu": btu, "cfh": cfh, "size": size, "gph": round(model["lp_cfh"] / LP_CF_PER_GAL, 2),
            "capacity": round(pipe_capacity(size, fuel, feet)) if size else 0,
            "text": (f"{size} in Schedule 40 for {feet:g} ft" if size else "more than 2 in pipe: use elevated pressure (2 psi) or a closer source")}


def upgrade(values):
    """Everything the Upgrading page shows."""
    inputs = read_upgrade(values)
    old, new = MODEL_BY_KEY[inputs["old"]], MODEL_BY_KEY[inputs["new"]]
    fuel, feet_wire, feet_gas = inputs["fuel"], inputs["wire_feet"], inputs["gas_feet"]
    wire_old = wiring(old, inputs["material"], feet_wire, inputs["conduit"])
    wire_new = wiring(new, inputs["material"], feet_wire, inputs["conduit"])
    gas_old, gas_new = gas(old, fuel, feet_gas), gas(new, fuel, feet_gas)
    notes, keep = [], {}

    # the wire that is there now
    existing_wire, existing_material = inputs["existing_wire"], inputs["existing_material"] or inputs["material"]
    if existing_wire:
        fits = wire_ok(existing_wire, existing_material, new["breaker"])
        drop = voltage_drop(existing_wire, existing_material, new["amps"], feet_wire)
        keep["wire"] = {"ok": fits and drop <= VOLTAGE_DROP_LIMIT, "size": existing_wire, "material": existing_material,
                        "ampacity": AMPACITY[existing_material].get(existing_wire), "drop_pct": round(drop, 2),
                        "why": ("its ampacity (%s A) is under the new %d A breaker (NEC 240.4)" % (AMPACITY[existing_material].get(existing_wire), new["breaker"]) if not fits
                                else "voltage drop would be %.1f%% (over %g%%) at full load" % (drop, VOLTAGE_DROP_LIMIT) if drop > VOLTAGE_DROP_LIMIT
                                else "it is rated for the new breaker and drop is %.1f%%" % drop)}
    # the gas pipe that is there now
    if inputs["existing_pipe"]:
        capacity = pipe_capacity(inputs["existing_pipe"], fuel, feet_gas)
        share = capacity / gas_new["btu"] * 100
        keep["pipe"] = {"ok": capacity >= gas_new["btu"], "size": inputs["existing_pipe"], "capacity": round(capacity), "share": round(share),
                        "why": ("it carries %d%% of what the new unit burns at full load over %g ft" % (round(share), feet_gas))}

    # the rest an installer has to think about
    if old["brand"] != new["brand"]:
        notes.append(("warn", f"Brand change ({old['brand']} to {new['brand']}): the transfer switch and its control wiring are not interchangeable. "
                              f"Plan a new {new['brand']} transfer switch rated for the service (and at least the {new['breaker']} A generator breaker)."))
    elif new["breaker"] > old["breaker"]:
        notes.append(("info", f"Same brand: the transfer switch can often stay. Check that it is rated for the {new['breaker']} A breaker and is "
                              f"compatible with the new controller (control wires / communication) before reusing it."))
    if new["breaker"] > old["breaker"] and not (existing_wire and keep["wire"]["ok"]):
        notes.append(("warn", f"The breaker goes from {old['breaker']} A to {new['breaker']} A: the feeder must be rated for it (NEC 240.4). "
                              f"Plan on {wire_new['text']}."))
    if wire_new["upsized"]:
        notes.append(("info", f"Upsized from {wire_new['by_ampacity']} to {wire_new['size']} AWG for voltage drop over {feet_wire:g} ft "
                              f"({wire_new['drop_pct']}% at full load); the ground goes up in proportion (NEC 250.122(B))."))
    if new.get("breaker_checked") is False:
        notes.append(("warn", f"The {new['model']} spec sheet does not list its line breaker; {new['breaker']} A is assumed. Read the nameplate before buying wire."))
    if gas_new["size"] and gas_old["size"] and PIPE_SIZES.index(gas_new["size"]) > PIPE_SIZES.index(gas_old["size"]):
        notes.append(("warn", f"The gas line goes up from {gas_old['size']} in to {gas_new['size']} in for {feet_gas:g} ft "
                              f"({gas_new['btu']:,.0f} Btu/h at full load versus {gas_old['btu']:,.0f})."))
    if "pipe" in keep and not keep["pipe"]["ok"]:
        notes.append(("warn", f"Keeping the {keep['pipe']['size']} in line: it would carry about {keep['pipe']['share']}% of full-load fuel. "
                              "Under heavy load the inlet pressure sags: the unit cannot reach its rated output, big motor starts (A/C) can bog it "
                              "down or trip it on underspeed / low frequency, and it may run rough. It can work for a lightly loaded home; it is not "
                              "a full-rated install and the inspector may not pass it."))
    low, high = new["lp_wc"] if fuel == "lp" else new["ng_wc"]
    notes.append(("info", f"Inlet pressure for the {new['model']}: {low:g}–{high:g} in. w.c. at every load ({'LP' if fuel == 'lp' else 'natural gas'}). "
                          f"Fuel inlet: {new['inlet']}. Measure it at full load after the install."))
    if fuel == "ng":
        total_cfh = gas_new["cfh"] + inputs["other_btu"] / NG_BTU_PER_CF
        if inputs["meter_cfh"] and total_cfh > inputs["meter_cfh"]:
            notes.append(("warn", f"The meter: generator {gas_new['cfh']:g} cfh + other appliances {inputs['other_btu'] / 1000:,.0f} cfh = "
                                  f"{total_cfh:,.0f} cfh, over the {inputs['meter_cfh']:g} cfh meter. Call the gas utility about a larger meter/regulator."))
        else:
            notes.append(("info", f"The meter: {total_cfh:,.0f} cfh with every appliance running" + (f", within the {inputs['meter_cfh']:g} cfh meter." if inputs["meter_cfh"]
                                  else ". Check the meter's rating (many homes have a 250 cfh meter); the utility upgrades it when it is too small.")))
    else:
        gph_half = new["lp_cfh"] / LP_CF_PER_GAL * 0.6
        if inputs["tank_gallons"]:
            hours = inputs["tank_gallons"] * 0.8 / gph_half
            notes.append(("info", f"Propane: {gas_new['gph']} gal/h at full load. A {inputs['tank_gallons']:g} gal tank filled to 80% runs about "
                                  f"{hours:,.0f} hours (about {hours / 24:.1f} days) at half load."))
        if inputs["tank_gallons"] and inputs["tank_gallons"] < 500 and gas_new["btu"] > 250000:
            notes.append(("warn", f"A {inputs['tank_gallons']:g} gal tank may not vaporize {gas_new['btu']:,.0f} Btu/h in cold weather. "
                                  "Both makers size the tank for full load in the coldest weather: confirm with the propane supplier."))
        notes.append(("info", "LP needs a second-stage regulator set to the generator's inlet range; check the first-stage and tank regulators can deliver full-load Btu."))
    if new["cooling"] != old["cooling"] or new["lb"] - old["lb"] > 100:
        notes.append(("warn", f"Weight goes from {old['lb']} lb to {new['lb']} lb" + (" and it is liquid-cooled" if new["cooling"] == "liquid" else "") +
                              ": check the pad and the clearances in the new unit's install manual; a new pad is likely."))
    notes.append(("info", "Placement: keep the maker's clearances (Generac air-cooled: 18 in from the wall and 5 ft from operable windows, doors and "
                          "openings; Kohler: see 'Location' in its install manual) and any township setbacks."))
    notes.append(("info", "Permit and inspection: a generator swap needs an electrical permit and, with gas work, a gas/plumbing permit in most PA townships."))

    return {"inputs": inputs, "old": old, "new": new, "wire_old": wire_old, "wire_new": wire_new, "gas_old": gas_old, "gas_new": gas_new,
            "keep": keep, "notes": notes,
            "needs_new_wire": not (existing_wire and keep["wire"]["ok"]) if existing_wire else wire_new["size"] != wire_old["size"] or new["breaker"] > old["breaker"],
            "needs_new_pipe": not keep["pipe"]["ok"] if "pipe" in keep else PIPE_SIZES.index(gas_new["size"] or "2") > PIPE_SIZES.index(gas_old["size"] or "2"),
            "needs_new_switch": old["brand"] != new["brand"]}


def upgrade_brief(result, estimate=None):
    """The upgrade in words, for Caleb and for Caden's quote."""
    i, old, new = result["inputs"], result["old"], result["new"]
    fuel = "propane" if i["fuel"] == "lp" else "natural gas"
    lines = [f"Generator upgrade on {fuel}: remove the existing {old['brand']} {old['model']} ({old['kw_lp']:g} kW LP / {old['kw_ng']:g} kW NG, "
             f"{old['breaker']} A breaker) and install a {new['brand']} {new['model']} ({new['kw_lp']:g} kW LP / {new['kw_ng']:g} kW NG, {new['breaker']} A breaker).",
             f"Wiring for the existing unit: {result['wire_old']['text']}. Needed for the new unit over {i['wire_feet']:g} ft: {result['wire_new']['text']}, "
             f"{result['wire_new']['conduit']} in {result['wire_new']['conduit_kind']}, {result['wire_new']['drop_pct']}% voltage drop.",
             f"Gas for the existing unit: {result['gas_old']['text']} ({result['gas_old']['btu']:,.0f} Btu/h). Needed for the new unit: "
             f"{result['gas_new']['text']} ({result['gas_new']['btu']:,.0f} Btu/h at full load)."]
    if "wire" in result["keep"]:
        k = result["keep"]["wire"]
        lines.append(f"Existing wire is {k['size']} AWG {k['material']}: {'can stay' if k['ok'] else 'must be replaced'} ({k['why']}).")
    if "pipe" in result["keep"]:
        k = result["keep"]["pipe"]
        lines.append(f"Existing gas pipe is {k['size']} in: {'can stay' if k['ok'] else 'undersized'} ({k['why']}).")
    lines += [f"- {text}" for level, text in result["notes"] if level == "warn"]
    if estimate:
        lines.append("Scope to quote: " + "; ".join(f"{line['label']} ({line['qty']:g} {line['unit']})" for line in estimate["lines"]) + ".")
    return "\n".join(lines)
