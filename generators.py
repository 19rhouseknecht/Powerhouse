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
    ("24RCLA", 23.0, 21.0, "Kohler 24 kW air-cooled home standby"),
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
                     "liquid-cooled generators (30 kW and up) with Kohler's own tool or your Kohler distributor.")
    if inputs["ac_count"] and not inputs["ac_lra"]:
        notes.append("A/C starting load is estimated (3 x running%s). Enter the nameplate LRA for the real figure." % (", reduced by the soft starter" if inputs["soft_start"] == "yes" else ""))
    if int(inputs["service_amps"]) >= 300:
        notes.append("A %s A service needs a transfer switch rated for it (or two 200 A switches)." % inputs["service_amps"])
    return {
        "inputs": inputs, "rows": rows, "running_kw": running, "starting_kw": starting, "required_kw": required, "sets_start": sets_start,
        "model": model, "with_load_management": {"required_kw": shed_required, "model": shed_model, "shed": [row[0] for row in rows if row[3]]},
        "transfer_switch_amps": int(inputs["service_amps"]), "notes": notes,
    }
