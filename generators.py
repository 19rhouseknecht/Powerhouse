"""Generator sizing, the way GenSizer (gensizer.assurancepower.com) and the inspector do it: the NEC 220.82 optional load
calculation for a dwelling, then a generator big enough for that load (NEC 702.4(B): a standby generator with automatic
transfer must carry the load calculated under Article 220, less what an automatic load management system keeps off it).
Pure functions (no Flask, no database) so every number can be tested and shown step by step.

    1  general lighting and receptacles     3 VA per square foot                                220.82(B)(1)
    2  small-appliance and laundry circuits  1,500 VA each (at least two small-appliance)        220.82(B)(2)
    3  appliances fastened in place          their nameplate VA (motors at their full-load amps)  220.82(B)(3), (4)
    4  general load                          first 10,000 VA at 100 %, the rest at 40 %           220.82(B)
    5  heating or cooling                    the largest of the 220.82(C) choices, at its demand  220.82(C)
    6  total                                 4 + 5, in VA and amps at 240 V
    7  generator                             total less managed loads, in kW (power factor 1.0)  702.4(B)

Model ratings are kW on LP / natural gas from Kohler spec sheets. Check the current spec sheet before ordering.
"""

# (model, kW on LP, kW on natural gas, description): Kohler's air-cooled home standby line
KOHLER_MODELS = [
    ("14RCA", 14.0, 12.0, "Kohler 14 kW air-cooled home standby"),
    ("20RCA", 20.0, 18.0, "Kohler 20 kW air-cooled home standby"),
    ("26RCA", 26.0, 24.0, "Kohler 26 kW air-cooled home standby"),
]
VOLTS = 240.0
GENERAL_FULL = 10000          # 220.82(B): the first 10 kVA of the general load at 100 %
GENERAL_REST = 0.40           # the remainder at 40 %
CIRCUIT_VA = 1500             # each small-appliance and laundry circuit
AC_VA_PER_TON = 1300          # a typical condenser's compressor (about 5.4 A per ton at 240 V); type nameplate amps for the real figure
CONDENSER_FAN_VA = 240        # its outdoor fan motor (about 1 A at 240 V)
SOFT_START_FACTOR = 0.4       # a soft starter cuts the inrush to about 40 %

# Appliances fastened in place (key: label, default count, VA each, where the VA comes from). Every VA can be changed on the page.
APPLIANCES = {
    "dishwasher": ("Dishwasher", 1, 1200, "typical nameplate"),
    "disposal": ("Garbage disposal", 1, 900, "1/2 HP, 120 V, 7.2 A (typical nameplate)"),
    "microwave": ("Built-in microwave / hood", 1, 1500, "typical nameplate"),
    "range": ("Electric range", 0, 12000, "nameplate (12 kW typical)"),
    "cooktop": ("Electric cooktop", 0, 7600, "typical nameplate"),
    "oven": ("Wall oven", 0, 4800, "typical nameplate"),
    "dryer": ("Electric dryer", 1, 5000, "nameplate (5 kW typical)"),
    "water_heater": ("Electric water heater", 1, 4500, "4,500 W element"),
    "tankless": ("Electric tankless water heater", 0, 27000, "nameplate (27 kW typical)"),
    "furnace_blower": ("Furnace / air-handler blower", 1, 1176, "1/2 HP, 120 V, 9.8 A (NEC Table 430.248)"),
    "well_pump": ("Well pump", 0, 1920, "1 HP, 240 V, 8 A (NEC Table 430.248)"),
    "sump_pump": ("Sump pump", 1, 1176, "1/2 HP, 120 V, 9.8 A (NEC Table 430.248)"),
    "ev_charger": ("EV charger", 0, 7680, "32 A charger at 240 V"),
    "hot_tub": ("Hot tub", 0, 7500, "typical nameplate"),
    "pool_pump": ("Pool pump", 0, 1920, "1 HP, 240 V, 8 A (NEC Table 430.248)"),
    "freezer": ("Extra refrigerator / freezer", 0, 600, "typical nameplate"),
    "central_vac": ("Central vacuum", 0, 1500, "typical nameplate"),
    "garage_door": ("Garage door opener", 0, 696, "1/3 HP, 120 V, 5.8 A (NEC Table 430.248)"),
}
FIELDS = {
    # name: (label, default, minimum, maximum)
    "square_feet": ("Living area (sq ft)", 2000, 0, 20000),
    "small_appliance_circuits": ("Small-appliance circuits (kitchen, dining: 2 or more)", 2, 0, 12),
    "laundry_circuits": ("Laundry circuits", 1, 0, 4),
    **{key: (label, count, 0, 12) for key, (label, count, va, note) in APPLIANCES.items()},
    "other_va": ("Other loads fastened in place (VA total)", 0, 0, 100000),
    "ac_count": ("Central A/C units", 1, 0, 4),
    "ac_tons": ("Tons per A/C unit", 3.0, 0, 6),
    "ac_amps": ("A/C nameplate amps per unit (RLA + fan; blank = typical)", 0, 0, 80),
    "ac_lra": ("Largest A/C nameplate LRA (for the starting check)", 0, 0, 300),
    "heat_pump_count": ("Heat pumps (count; their compressors are the A/C above)", 0, 0, 4),
    "heat_strip_kw": ("Electric heat / heat strips (kW total)", 0, 0, 60),
    "heat_units": ("Separately controlled electric heating units", 1, 1, 20),
}
CHOICES = {
    "fuel": (("ng", "Natural gas"), ("lp", "Propane (LP)")),
    "service_amps": (("100", "100 A"), ("150", "150 A"), ("200", "200 A"), ("320", "320 A"), ("400", "400 A")),
    "soft_start": (("no", "No"), ("yes", "Yes")),
    "heat_interlock": (("no", "No"), ("yes", "Yes")),
}
DEFAULT_CHOICES = {"fuel": "ng", "service_amps": "200", "soft_start": "no", "heat_interlock": "no"}
MANAGEABLE = list(APPLIANCES) + ["other_va", "hvac"]           # loads a load-management system can keep off the generator


def _num(raw, default, low, high):
    try:
        number = float(str(raw).replace(",", "")) if str(raw).strip() != "" else float(default)
    except ValueError:
        number = float(default)
    return min(max(number, low), high)


def read_inputs(values):
    """Clean form values: counts, each appliance's VA (va_<key>), managed loads (managed_<key>), choices."""
    inputs = {name: _num(values.get(name, ""), default, low, high) for name, (label, default, low, high) in FIELDS.items()}
    for key, (label, count, va, note) in APPLIANCES.items():
        inputs["va_" + key] = _num(values.get("va_" + key, ""), va, 0, 60000)
    for name, options in CHOICES.items():
        allowed = {value for value, _ in options}
        inputs[name] = values.get(name) if values.get(name) in allowed else DEFAULT_CHOICES[name]
    inputs["managed"] = sorted(key for key in MANAGEABLE if str(values.get("managed_" + key, "")).lower() in ("1", "on", "yes", "true"))
    return inputs


def ac_unit_va(inputs):
    """One A/C condenser: its nameplate amps x 240 V when typed, else tons x 1,300 VA + its fan."""
    if inputs["ac_amps"]:
        return inputs["ac_amps"] * VOLTS, f"{inputs['ac_amps']:g} A nameplate x 240 V"
    return inputs["ac_tons"] * AC_VA_PER_TON + CONDENSER_FAN_VA, f"{inputs['ac_tons']:g} ton x {AC_VA_PER_TON:,} VA + {CONDENSER_FAN_VA} VA fan (typical)"


def calculate(inputs, leave_out=()):
    """The 220.82 calculation. leave_out: loads kept off the generator by load management. Returns (steps, totals)."""
    steps = []
    lighting = 3 * inputs["square_feet"]
    steps.append(("1", "General lighting and receptacles", f"{inputs['square_feet']:,.0f} sq ft x 3 VA", lighting, "220.82(B)(1)"))
    circuits = int(inputs["small_appliance_circuits"]) + int(inputs["laundry_circuits"])
    steps.append(("2", "Small-appliance and laundry circuits", f"{circuits} x {CIRCUIT_VA:,} VA", circuits * CIRCUIT_VA, "220.82(B)(2)"))
    appliances = 0.0
    for key, (label, count, va, note) in APPLIANCES.items():
        qty = int(inputs[key])
        if qty and key not in leave_out:
            value = qty * inputs["va_" + key]
            appliances += value
            steps.append(("3", label, f"{qty} x {inputs['va_' + key]:,.0f} VA ({note})", value, "220.82(B)(3)"))
    if inputs["other_va"] and "other_va" not in leave_out:
        appliances += inputs["other_va"]
        steps.append(("3", "Other loads fastened in place", "as entered", inputs["other_va"], "220.82(B)(3)"))
    general = lighting + circuits * CIRCUIT_VA + appliances
    steps.append(("4", "General load", "steps 1 to 3", general, "220.82(B)"))
    first, rest = min(general, GENERAL_FULL), max(general - GENERAL_FULL, 0)
    general_demand = first + rest * GENERAL_REST
    steps.append(("4", "General load after demand", f"first {first:,.0f} VA at 100% + {rest:,.0f} VA x 40%", general_demand, "220.82(B)"))

    unit_va, unit_how = ac_unit_va(inputs)
    cooling = inputs["ac_count"] * unit_va
    strips = inputs["heat_strip_kw"] * 1000
    compressors = inputs["heat_pump_count"] * unit_va
    choices = [("A/C at 100%", f"{inputs['ac_count']:g} x {unit_how}", cooling, "220.82(C)(1)")]
    if compressors and not strips:
        choices.append(("Heat pump at 100% (no electric heat)", f"{inputs['heat_pump_count']:g} x {unit_how}", compressors, "220.82(C)(2)"))
    if compressors and strips:
        if inputs["heat_interlock"] == "yes":
            choices.append(("Heat strips at 65% (compressor locked out while they run)", f"{strips:,.0f} VA x 65%", strips * 0.65, "220.82(C)(3)"))
        else:
            choices.append(("Heat pump at 100% + heat strips at 65%", f"{compressors:,.0f} VA + {strips:,.0f} VA x 65%", compressors + strips * 0.65, "220.82(C)(3)"))
    if strips and not compressors:
        if inputs["heat_units"] >= 4:
            choices.append(("Electric heat at 40% (4 or more separately controlled units)", f"{strips:,.0f} VA x 40%", strips * 0.40, "220.82(C)(5)"))
        else:
            choices.append(("Electric heat at 65% (fewer than 4 separately controlled units)", f"{strips:,.0f} VA x 65%", strips * 0.65, "220.82(C)(4)"))
    hvac_choice = max(choices, key=lambda c: c[2])
    hvac = 0.0 if "hvac" in leave_out else hvac_choice[2]
    for label, how, value, ref in choices:
        steps.append(("5", label + (" (the largest: used)" if (label, how, value, ref) == hvac_choice and "hvac" not in leave_out else ""), how, value, ref))
    if "hvac" in leave_out:
        steps.append(("5", "Heating and cooling kept off the generator by load management", "", 0.0, "702.4(B)"))
    total = general_demand + hvac
    steps.append(("6", "Total calculated load", "step 4 after demand + step 5", total, "220.82"))
    return steps, {"general": general, "general_demand": general_demand, "hvac": hvac, "hvac_choice": hvac_choice, "total": total,
                   "amps": total / VOLTS, "cooling_unit_va": unit_va}


def pick_model(required_kw, fuel):
    for model, lp, ng, description in KOHLER_MODELS:
        rated = lp if fuel == "lp" else ng
        if rated >= required_kw:
            return {"model": model, "rated_kw": rated, "description": description}
    return None


def size(values):
    """Everything the sizing page shows, from the form's values."""
    inputs = read_inputs(values)
    steps, totals = calculate(inputs)
    managed = inputs["managed"]
    gen_steps, gen_totals = calculate(inputs, leave_out=managed) if managed else (steps, totals)
    required_kw = round(gen_totals["total"] / 1000, 2)
    model = pick_model(required_kw, inputs["fuel"])
    # the starting check: the largest motor starting (from its LRA when typed)
    unit_va = totals["cooling_unit_va"]
    lra_kva = inputs["ac_lra"] * VOLTS / 1000 if inputs["ac_lra"] else unit_va * 3 / 1000
    soft = SOFT_START_FACTOR if inputs["soft_start"] == "yes" else 1.0
    starting_kva = round(lra_kva * soft, 2) if inputs["ac_count"] or inputs["heat_pump_count"] else 0.0
    notes = []
    if model is None:
        notes.append("More than the largest air-cooled model covers. Use load management (tick Managed on the big loads) or size a "
                     "Kohler liquid-cooled generator (the 24RCLA and up) with Kohler's own tool or your Kohler distributor.")
    if starting_kva:
        notes.append(f"Starting check: the A/C compressor draws about {starting_kva:g} kVA for a moment as it starts"
                     + (f" (its LRA {inputs['ac_lra']:g} A x 240 V" if inputs["ac_lra"] else " (3 x its running load; type the nameplate LRA for the real figure")
                     + (", cut to 40% by the soft starter)." if soft < 1 else ").")
                     + " Confirm the generator's motor-starting rating on its spec sheet, or add a soft starter.")
    if int(inputs["service_amps"]) >= 300:
        notes.append(f"A {inputs['service_amps']} A service needs a transfer switch rated for it (or two 200 A switches).")
    if managed:
        notes.append("Load management: the loads marked Managed are kept off the generator (NEC 702.4(B)(2)), so the generator is sized for "
                     "the calculation without them. Install a load manager or a transfer switch that sheds them.")
    return {
        "inputs": inputs, "steps": steps, "totals": totals, "generator_steps": gen_steps if managed else [],
        "total_va": round(totals["total"]), "total_amps": round(totals["amps"], 1),
        "generator_va": round(gen_totals["total"]), "required_kw": required_kw, "running_kw": round(totals["total"] / 1000, 2),
        "starting_kw": starting_kva, "sets_start": "the A/C compressor" if starting_kva else "", "model": model,
        "with_load_management": {"required_kw": required_kw, "model": model, "shed": [APPLIANCES[k][0] if k in APPLIANCES else
                                 ("Heating and cooling" if k == "hvac" else "Other loads") for k in managed]},
        "transfer_switch_amps": int(inputs["service_amps"]), "notes": notes,
        "rows": [(label, value / 1000, 0.0, False) for number, label, how, value, ref in steps],
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
    # Generac Guardian, earlier units you will find in the field (smallest first)
    {"key": "generac-7-5240", "brand": "Generac", "model": "Guardian 7 kW (05240/05280, about 2008–2012; 6 kW on NG)", "kw_lp": 7, "kw_ng": 6, "amps": 29, "breaker": 30,
     "ng_cfh": 119, "lp_cfh": 54, "inlet": "1/2 in", "ng_wc": (5, 7), "lp_wc": (10, 12), "lb": 336, "cooling": "air"},
    {"key": "generac-8-6237", "brand": "Generac", "model": "Guardian 8 kW (6237/6245, earlier)", "kw_lp": 8, "kw_ng": 7, "amps": 33, "breaker": 35,
     "ng_cfh": 121, "lp_cfh": 52, "inlet": "1/2 in", "ng_wc": (5, 7), "lp_wc": (10, 12), "lb": 360, "cooling": "air"},
    {"key": "generac-9-7030", "brand": "Generac", "model": "Guardian 9 kW (7029/7030, earlier)", "kw_lp": 9, "kw_ng": 8, "amps": 38, "breaker": 40,
     "ng_cfh": 133, "lp_cfh": 54, "inlet": "3/4 in", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 340, "cooling": "air"},
    {"key": "generac-10-5241", "brand": "Generac", "model": "Guardian 10 kW (05241/05281, about 2008–2012; 9 kW on NG)", "kw_lp": 10, "kw_ng": 9, "amps": 42, "breaker": 45,
     "ng_cfh": 156, "lp_cfh": 70, "inlet": "1/2 in", "ng_wc": (5, 7), "lp_wc": (10, 12), "lb": 375, "cooling": "air"},
    {"key": "generac-11-6439", "brand": "Generac", "model": "Guardian 11 kW (6437/6438/6439/6720, earlier)", "kw_lp": 11, "kw_ng": 10, "amps": 46, "breaker": 50,
     "ng_cfh": 195, "lp_cfh": 70, "inlet": "3/4 in", "ng_wc": (5, 7), "lp_wc": (10, 12), "lb": 407, "cooling": "air"},
    {"key": "generac-11-7032", "brand": "Generac", "model": "Guardian 11 kW (7031/7032/7033, earlier)", "kw_lp": 11, "kw_ng": 10, "amps": 46, "breaker": 50,
     "ng_cfh": 199, "lp_cfh": 73, "inlet": "3/4 in", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 348, "cooling": "air"},
    {"key": "generac-13-5242", "brand": "Generac", "model": "Guardian 13 kW (05242/05282, about 2008–2012)", "kw_lp": 13, "kw_ng": 13, "amps": 54, "breaker": 55,
     "ng_cfh": 220, "lp_cfh": 80, "inlet": "3/4 in", "ng_wc": (5, 7), "lp_wc": (10, 12), "lb": 426, "cooling": "air"},
    {"key": "generac-14-6247", "brand": "Generac", "model": "Guardian 14 kW (6240/6241/6247, earlier)", "kw_lp": 14, "kw_ng": 14, "amps": 58, "breaker": 60,
     "ng_cfh": 279, "lp_cfh": 112, "inlet": "3/4 in", "ng_wc": (5, 7), "lp_wc": (10, 12), "lb": 435, "cooling": "air"},
    {"key": "generac-15-7163", "brand": "Generac", "model": "Guardian 15 kW (7163, earlier)", "kw_lp": 15, "kw_ng": 15, "amps": 63, "breaker": 70,
     "ng_cfh": 278, "lp_cfh": 100, "inlet": "3/4 in", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 460, "cooling": "air"},
    {"key": "generac-16-5243", "brand": "Generac", "model": "Guardian 16 kW (05243/05244/05283/05284, about 2008–2012; 15 kW on NG)", "kw_lp": 16, "kw_ng": 15, "amps": 67, "breaker": 65,
     "ng_cfh": 245, "lp_cfh": 92, "inlet": "3/4 in", "ng_wc": (5, 7), "lp_wc": (10, 12), "lb": 445, "cooling": "air"},
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
     "ng_cfh": 290, "lp_cfh": 129, "inlet": "1/2 in NPT", "ng_wc": (3.5, 11), "lp_wc": (7, 11), "lb": 625, "cooling": "air"},
    {"key": "kohler-24rcla", "brand": "Kohler", "model": "24RCLA (24 kW, liquid-cooled)", "kw_lp": 24, "kw_ng": 21, "amps": 100, "breaker": 125,
     "breaker_checked": False, "ng_cfh": 301, "lp_cfh": 113, "inlet": "1 in NPT", "ng_wc": (5, 11), "lp_wc": (5, 11), "lb": 1260, "cooling": "liquid"},
]
MODEL_BY_KEY = {row["key"]: row for row in UPGRADE_MODELS}
OTHER = "other"                                      # "Not on the list": the person types it from the nameplate
OTHER_FIELDS = {
    # suffix: (label, minimum, maximum)
    "kw": ("Rated kW (on the fuel used)", 1, 150),
    "breaker": ("Generator breaker (A)", 15, 600),
    "cfh": ("Full-load fuel use (cfh, blank = estimate)", 0, 3000),
}
STANDARD_BREAKER_LIST = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 110, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500, 600]
NG_CFH_PER_KW, LP_CFH_PER_KW = 14.0, 5.5           # typical air-cooled full-load use, only when the nameplate figure is not typed


def other_model(values, side, fuel):
    """A generator typed in by hand. Fuel use is estimated from kW when it is left blank (and the page says so)."""
    def number(suffix):
        label, low, high = OTHER_FIELDS[suffix]
        try:
            value = float(str(values.get(f"{side}_{suffix}", "")).replace(",", "").strip())
        except ValueError:
            return None
        return min(max(value, low), high)
    kw = number("kw") or 10.0
    amps = round(kw * 1000 / 240, 1)
    breaker = int(number("breaker") or next(b for b in STANDARD_BREAKER_LIST if b >= amps))
    cfh = number("cfh")
    estimated = not cfh
    cfh = cfh or round(kw * (LP_CFH_PER_KW if fuel == "lp" else NG_CFH_PER_KW))
    return {"key": OTHER, "brand": "Other", "model": f"generator, {kw:g} kW (typed in)", "kw_lp": kw, "kw_ng": kw, "amps": amps, "breaker": breaker,
            "ng_cfh": cfh, "lp_cfh": cfh, "inlet": "see the nameplate", "ng_wc": (3.5, 7), "lp_wc": (10, 12), "lb": 0, "cooling": "air",
            "typed": True, "fuel_estimated": estimated}
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

# The makers' own minimum gas pipe sizes (in. NPT), by total pipe length in feet, from the pressure regulator to the generator's
# fuel inlet with 8 ft added for every 90-degree elbow. Kohler 14/20/26RCA(L) installation manual, Figure 10. Where a maker's
# chart covers the run it is the requirement; past its last row (or for a model without one) the fuel-gas code tables are used.
ELBOW_FEET = 8
MAKER_PIPE_LENGTHS = [25, 50, 100, 150, 200]
MAKER_PIPE = {
    "kohler-14rca": {"ng": ["3/4", "1", "1", "1-1/4", "1-1/4"], "lp": ["3/4", "3/4", "1", "1", "1"]},
    "kohler-20rca": {"ng": ["1", "1", "1-1/4", "1-1/4", "1-1/4"], "lp": ["3/4", "1", "1", "1-1/4", "1-1/4"]},
    "kohler-26rca": {"ng": ["1", "1-1/4", "1-1/4", "1-1/4", "1-1/2"], "lp": ["3/4", "1", "1", "1", "1-1/4"]},
}
MAKER_PIPE_SOURCE = {"kohler-14rca": "Kohler's installation manual", "kohler-20rca": "Kohler's installation manual", "kohler-26rca": "Kohler's installation manual"}


def maker_pipe(model, fuel, feet):
    """The maker's minimum pipe size for this run, or None when its chart does not cover it."""
    chart = MAKER_PIPE.get(model.get("key"), {}).get(fuel)
    if not chart or feet > MAKER_PIPE_LENGTHS[-1]:
        return None
    return chart[next(i for i, length in enumerate(MAKER_PIPE_LENGTHS) if length >= feet)]


UPGRADE_FIELDS = {
    # name: (label, default, minimum, maximum)
    "wire_feet": ("Wire run: generator to transfer switch (ft, one way)", 30, 1, 500),
    "gas_feet": ("Gas run: pressure regulator to the generator's fuel inlet (ft)", 30, 1, 400),
    "gas_elbows": ("90-degree elbows in the gas run (8 ft each)", 0, 0, 40),
    "other_btu": ("Other gas appliances on the same meter (Btu/h, total)", 0, 0, 2000000),
    "meter_cfh": ("Gas meter rating (cfh; 0 = not known)", 0, 0, 5000),
    "tank_gallons": ("Propane tank (gallons)", 500, 0, 5000),
    "existing_pressure": ("Measured inlet pressure under load (in. w.c.; 0 = not measured)", 0, 0, 30),
}
UPGRADE_CHOICES = {
    "fuel": (("ng", "Natural gas"), ("lp", "Propane (LP)")),
    "material": (("copper", "Copper"), ("aluminum", "Aluminum")),
    "conduit": (("PVC 40", "PVC Sch 40"), ("EMT", "EMT")),
    "existing_material": (("", "Not known"), ("copper", "Copper"), ("aluminum", "Aluminum")),
    "existing_wire": tuple([("", "Not known")] + [(size, f"{size} AWG") for size in WIRE_SIZES[2:]]),
    "existing_pipe": tuple([("", "Not known")] + [(size, f"{size} in") for size in PIPE_SIZES]),
    "existing_ground": tuple([("", "Not known")] + [(size, f"{size} AWG") for size in WIRE_SIZES[1:]]),
    "existing_conduit": tuple([("", "Not known")] + [(trade, f"{trade} in") for trade, _ in CONDUIT_FILL["PVC 40"]]),
    "existing_switch": (("", "Not known"), ("100", "100 A"), ("150", "150 A"), ("200", "200 A"), ("400", "400 A")),
}
UPGRADE_DEFAULTS = {"old": "generac-20-7039", "new": "generac-26", "fuel": "ng", "material": "copper", "conduit": "PVC 40",
                    "existing_material": "", "existing_wire": "", "existing_pipe": "", "existing_ground": "", "existing_conduit": "",
                    "existing_switch": ""}


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
        inputs[name] = values.get(name) if values.get(name) in MODEL_BY_KEY or values.get(name) == OTHER else UPGRADE_DEFAULTS[name]
        for suffix in OTHER_FIELDS:
            inputs[f"{name}_{suffix}"] = str(values.get(f"{name}_{suffix}", "")).strip()[:12]
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
    """Full-load fuel, and the pipe the run needs: the maker's chart when it covers the run, else the code tables."""
    btu, cfh = gas_demand(model, fuel)
    maker = maker_pipe(model, fuel, feet)
    size = maker or pipe_for(btu, fuel, feet)
    source = MAKER_PIPE_SOURCE.get(model.get("key")) if maker else "fuel-gas code tables"
    return {"btu": btu, "cfh": cfh, "size": size, "gph": round(model["lp_cfh"] / LP_CF_PER_GAL, 2), "source": source,
            "capacity": round(pipe_capacity(size, fuel, feet)) if size else 0,
            "text": (f"{size} in for {feet:g} ft ({source})" if size else "more than 2 in pipe: use elevated pressure (2 psi) or a closer source")}


def pipe_ok(existing, model, fuel, feet, btu):
    """Whether a pipe already there is big enough: against the maker's chart where it covers the run, else by capacity."""
    maker = maker_pipe(model, fuel, feet)
    if maker:
        return PIPE_SIZES.index(existing) >= PIPE_SIZES.index(maker)
    return pipe_capacity(existing, fuel, feet) >= btu


def upgrade(values):
    """Everything the Upgrading page shows."""
    inputs = read_upgrade(values)
    fuel, feet_wire = inputs["fuel"], inputs["wire_feet"]
    feet_gas = inputs["gas_feet"] + ELBOW_FEET * int(inputs["gas_elbows"])        # the total length the charts are read at
    old, new = (other_model(values, side, fuel) if inputs[side] == OTHER else MODEL_BY_KEY[inputs[side]] for side in ("old", "new"))
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
        keep["pipe"] = {"ok": pipe_ok(inputs["existing_pipe"], new, fuel, feet_gas, gas_new["btu"]), "size": inputs["existing_pipe"], "capacity": round(capacity), "share": round(share),
                        "why": ("it carries %d%% of what the new unit burns at full load over %g ft" % (round(share), feet_gas))}

    # the rest an installer has to think about
    for side, unit in (("there now", old), ("going in", new)):
        if unit.get("typed"):
            notes.append(("warn", f"The unit {side} was typed in: check its breaker and fuel use on the nameplate"
                                  + (f" (fuel use is estimated at {unit['ng_cfh']:g} cfh from its kW)." if unit["fuel_estimated"] else ".")))
    if "Other" in (old["brand"], new["brand"]):
        notes.append(("info", "Check that the transfer switch works with the new unit's controller (same maker, compatible control wiring) before reusing it."))
    elif old["brand"] != new["brand"]:
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
    if old["lb"] and new["lb"] and (new["cooling"] != old["cooling"] or new["lb"] - old["lb"] > 100):
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
    rows = upgrade_table(result)
    work = [f"{row['label']}: now {row['now']}, needs {row['needs']} ({row['why']})" for row in rows if row["status"] == "bad"]
    fine = [row["label"] for row in rows if row["status"] == "ok"]
    if work:
        lines.append("Needs work: " + "; ".join(work) + ".")
    if fine:
        lines.append("Can stay as it is: " + ", ".join(fine) + ".")
    return "\n".join(lines)


def _ground_ok(existing, material, breaker):
    need = ground_for(breaker, material)
    return CIRCULAR_MILS[existing] >= CIRCULAR_MILS[need], need


def upgrade_table(result):
    """The Upgrading page's table: one row per thing on the job. Each row: what is there now (typed, or assumed to be what the
    old unit needed), what the new unit needs, and ok (green) / bad (red, work to do) / info. Built from upgrade()'s result."""
    i, old, new = result["inputs"], result["old"], result["new"]
    fuel = i["fuel"]
    rows = []

    def row(key, label, now, needs, status="info", why="", inputs=()):
        rows.append({"key": key, "label": label, "now": now, "needs": needs, "status": status, "why": why, "inputs": list(inputs)})

    row("fuel", "Fuel", "Propane (LP)" if fuel == "lp" else "Natural gas", "Propane (LP)" if fuel == "lp" else "Natural gas", inputs=["fuel"])
    row("output", "Output (LP / NG)", f"{old['kw_lp']:g} / {old['kw_ng']:g} kW", f"{new['kw_lp']:g} / {new['kw_ng']:g} kW")
    row("breaker", "Generator breaker", f"{old['breaker']} A", f"{new['breaker']} A" + (" (check the nameplate)" if new.get("breaker_checked") is False else ""))
    row("wire_feet", "Wire run, generator to transfer switch", f"{i['wire_feet']:g} ft", f"{i['wire_feet']:g} ft", inputs=["wire_feet"])

    material = i["existing_material"] or i["material"]
    wire_now = i["existing_wire"] or result["wire_old"]["size"]
    assumed = "" if i["existing_wire"] else " (assumed: what the old unit needed)"
    fits = wire_ok(wire_now, material, new["breaker"]) if wire_now in AMPACITY[material] else False
    drop_now = voltage_drop(wire_now, material, new["amps"], i["wire_feet"]) if wire_now in CIRCULAR_MILS else 99
    ok = fits and drop_now <= VOLTAGE_DROP_LIMIT
    row("feeder", "Feeder wire (x 3)", f"{wire_now} AWG {material}{assumed}", f"{result['wire_new']['size']} AWG {result['wire_new']['material']}",
        "ok" if ok else "bad",
        "Good for the new breaker" if ok else (f"{AMPACITY[material].get(wire_now, 0)} A wire on a {new['breaker']} A breaker (NEC 240.4): replace it" if not fits
                                               else f"{drop_now:.1f}% voltage drop at full load: upsize it"),
        inputs=["existing_wire", "existing_material"])

    ground_now = i["existing_ground"] or result["wire_old"]["ground"]
    ground_good, ground_need = _ground_ok(ground_now, material, new["breaker"])
    if ok and not i["existing_wire"] or not ok:
        ground_need = result["wire_new"]["ground"] if not ok else ground_need
    row("ground", "Ground wire", f"{ground_now} AWG" + ("" if i["existing_ground"] else " (assumed)"), f"{ground_need} AWG",
        "ok" if ground_good else "bad", "Big enough (NEC 250.122)" if ground_good else "Too small for the new breaker (NEC 250.122)", inputs=["existing_ground"])

    conduit_now = i["existing_conduit"] or result["wire_old"]["conduit"]
    sizes = [trade for trade, _ in CONDUIT_FILL[i["conduit"]]]
    feeder_in = wire_now if ok else result["wire_new"]["size"]
    conduit_need = conduit_for([feeder_in] * 3 + [ground_need], i["conduit"])
    conduit_good = conduit_now in sizes and conduit_need in sizes and sizes.index(conduit_now) >= sizes.index(conduit_need)
    row("conduit", f"Conduit ({i['conduit']})", f"{conduit_now} in" + ("" if i["existing_conduit"] else " (assumed)"), f"{conduit_need} in",
        "ok" if conduit_good else "bad", "Room for the wires (NEC Chapter 9, 40% fill)" if conduit_good else "Too small for the new wires: replace it",
        inputs=["existing_conduit", "conduit"])

    in_place = wire_now if ok else result["wire_new"]["size"]
    drop = voltage_drop(in_place, material if ok else result["wire_new"]["material"], new["amps"], i["wire_feet"])
    row("drop", "Voltage drop at full load", f"{result['wire_old']['drop_pct']}%", f"{drop:.2f}%", "ok" if drop <= VOLTAGE_DROP_LIMIT else "bad",
        f"Within {VOLTAGE_DROP_LIMIT:g}%" if drop <= VOLTAGE_DROP_LIMIT else f"Over {VOLTAGE_DROP_LIMIT:g}%")

    elbows = int(i["gas_elbows"])
    total_gas = i["gas_feet"] + ELBOW_FEET * elbows
    row("gas_feet", "Gas run, regulator to fuel inlet", f"{i['gas_feet']:g} ft" + (f" + {elbows} elbow{'s' if elbows != 1 else ''}" if elbows else ""),
        f"{total_gas:g} ft total", why=(f"{i['gas_feet']:g} ft + {elbows} x {ELBOW_FEET} ft for the 90-degree elbows" if elbows else "Add 8 ft for every 90-degree elbow"),
        inputs=["gas_feet", "gas_elbows"])
    row("fuel_use", "Fuel flow at full load (maximum)", f"{result['gas_old']['cfh']:g} cfh · {result['gas_old']['btu']:,.0f} Btu/h",
        f"{result['gas_new']['cfh']:g} cfh · {result['gas_new']['btu']:,.0f} Btu/h")
    pipe_now = i["existing_pipe"] or result["gas_old"]["size"] or "2"
    capacity = pipe_capacity(pipe_now, fuel, total_gas)
    pipe_good = pipe_ok(pipe_now, new, fuel, total_gas, result["gas_new"]["btu"])
    source = result["gas_new"].get("source", "fuel-gas code tables")
    row("pipe", "Gas pipe", f"{pipe_now} in" + ("" if i["existing_pipe"] else " (assumed)"), f"{result['gas_new']['size'] or 'over 2'} in minimum",
        "ok" if pipe_good else "bad",
        (f"Meets {source} for {total_gas:g} ft" if pipe_good else f"Under {source} for {total_gas:g} ft"
         + (f" (it carries about {capacity / result['gas_new']['btu'] * 100:.0f}% of full-load fuel): see Food for thought" if capacity < result["gas_new"]["btu"] else "")),
        inputs=["existing_pipe"])
    if fuel == "ng":
        total_cfh = result["gas_new"]["cfh"] + i["other_btu"] / NG_BTU_PER_CF
        row("other_btu", "Other gas appliances on the meter", f"{i['other_btu']:,.0f} Btu/h", f"{i['other_btu']:,.0f} Btu/h", inputs=["other_btu"])
        if i["meter_cfh"]:
            row("meter", "Gas meter", f"{i['meter_cfh']:g} cfh", f"{total_cfh:,.0f} cfh with everything running",
                "ok" if total_cfh <= i["meter_cfh"] else "bad", "Big enough" if total_cfh <= i["meter_cfh"] else "Too small: call the gas utility", inputs=["meter_cfh"])
        else:
            row("meter", "Gas meter", "not known", f"{total_cfh:,.0f} cfh with everything running", "info",
                "Check the meter's flow rating with the gas utility (many are 250 cfh); they upgrade it if needed", inputs=["meter_cfh"])
    else:
        big = result["gas_new"]["btu"] > 250000
        good = i["tank_gallons"] >= 500 or not big
        row("tank", "Propane tank", f"{i['tank_gallons']:g} gal" if i["tank_gallons"] else "not known", f"{result['gas_new']['gph']} gal/h at full load",
            "ok" if good and i["tank_gallons"] else "bad" if not good else "info",
            ("Big enough to vaporize in cold weather; confirm the regulator output with the LPG provider" if good and i["tank_gallons"]
             else "May not vaporize enough in cold weather: contact the local LPG provider"),
            inputs=["tank_gallons"])
    low, high = new["lp_wc"] if fuel == "lp" else new["ng_wc"]
    if i["existing_pressure"]:
        good = low <= i["existing_pressure"] <= high
        row("pressure", "Inlet pressure under load", f"{i['existing_pressure']:g} in. w.c.", f"{low:g}–{high:g} in. w.c.", "ok" if good else "bad",
            "Within the new unit's range" if good else "Outside the new unit's range: adjust the regulator", inputs=["existing_pressure"])
    else:
        row("pressure", "Inlet pressure under load", "not measured", f"{low:g}–{high:g} in. w.c.", "info", "Measure it at full load after the install", inputs=["existing_pressure"])
    row("inlet", "Fuel inlet", old["inlet"], new["inlet"], "info")
    same_brand = old["brand"] == new["brand"] and "Other" not in (old["brand"], new["brand"])
    switch = int(i["existing_switch"] or 0)
    if not same_brand:
        row("switch", "Transfer switch", f"{old['brand']}" + (f", {switch} A" if switch else ""), f"{new['brand']}, at least {new['breaker']} A", "bad",
            "Different maker: the controls don't talk to each other. New transfer switch.", inputs=["existing_switch"])
    else:
        good = not switch or switch >= new["breaker"]
        row("switch", "Transfer switch", f"{old['brand']}" + (f", {switch} A" if switch else ", amps not known"), f"{new['brand']}, at least {new['breaker']} A",
            "ok" if good and switch else "bad" if not good else "info",
            "Same maker and rated for it: check the new controller works with it" if good and switch else
            "Too small for the new generator" if not good else "Same maker: check its rating and that the new controller works with it", inputs=["existing_switch"])
    heavier = new["lb"] and old["lb"] and (new["lb"] - old["lb"] > 100 or new["cooling"] != old["cooling"])
    row("pad", "Weight / pad", f"{old['lb']:g} lb" if old["lb"] else "see the nameplate", (f"{new['lb']:g} lb" if new["lb"] else "see the nameplate")
        + (", liquid-cooled" if new["cooling"] == "liquid" else ""), "bad" if heavier else "ok" if new["lb"] and old["lb"] else "info",
        "Much heavier or a different kind of unit: plan a new pad" if heavier else "The old pad should do (check its size)" if new["lb"] and old["lb"] else "")
    return rows

