"""Demo data for Summer Flowers.

Drives the app's own document flow rather than writing rows directly, so what
comes out is what a real user would get: protocols compute their own geometry,
the plan is generated from a demand register, plantings come from the plan and
the motherstock batch is sized on the plan's peak sticking week.

    bench --site <site> execute upande_summer_flowers.demo.run
    bench --site <site> execute upande_summer_flowers.demo.wipe
"""

import datetime

import frappe

from upande_summer_flowers.summer_flowers.planning import add_weeks, current_week

FARMS = [
    ("Mona Flowers", "MONA"),
    ("Timaflor", "TIMA"),
]

# Two farms grow the same Aster, at different altitudes — the whole point of
# protocol_comparison() is that the timings differ.
PROTOCOLS = [
    # variety, farm, plants/m2 net, weeks_to_pinch, interval, flushes, curve
    ("Aster Pink Flash", "Mona Flowers", 64, 7, 13, [2.7, 2.6, 2.4, 2.3, 2.2, 2.2, 2.2, 2.2]),
    ("Aster Pink Flash", "Timaflor", 64, 8, 13, [2.5, 2.4, 2.3, 2.2, 2.1, 2.1, 2.1, 2.1]),
    ("Solidago Tara", "Mona Flowers", 40, 5, 26, [4.0, 3.5, 3.0, 2.5]),
    ("Craspedia Billy Buttons", "Mona Flowers", 72, 6, 13, [3.2, 3.0, 2.8, 2.6, 2.4, 2.4]),
]

GRADES = [("60cm", 45.0), ("50cm", 35.0), ("40cm", 20.0)]

# block code, farm, gross ha, beds
BLOCKS = [
    ("9B", "Mona Flowers", 0.25, 20),
    ("10", "Mona Flowers", 0.19, 15),
    ("11A", "Mona Flowers", 0.50, 40),
    ("7C", "Mona Flowers", 0.13, 10),
    ("3A", "Mona Flowers", 0.25, 20),
    ("T1", "Timaflor", 0.38, 30),
    ("T2", "Timaflor", 0.25, 20),
]

# Weekly demand shape over the European season, peaking around midsummer.
SEASON = [
    5, 5, 6, 8, 10, 12, 14, 18, 24, 30, 38, 46, 54, 62, 70, 78, 86, 94, 100, 96,
    90, 86, 84, 84, 86, 88, 90, 92, 94, 92, 88, 84, 80, 76, 70, 64, 58, 52, 46,
    40, 34, 28, 24, 20, 16, 13, 11, 9, 8, 12, 16, 10, 6,
]

# variety, farm, peak stems in the busiest week
DEMAND = [
    ("Aster Pink Flash", "Mona Flowers", 14000),
    ("Solidago Tara", "Mona Flowers", 6000),
    ("Craspedia Billy Buttons", "Mona Flowers", 7500),
    ("Aster Pink Flash", "Timaflor", 9000),
]

TC_BANDS = [
    (1, 24, 5.70, 7.00), (25, 49, 2.66, 3.55), (50, 99, 2.66, 3.40),
    (100, 249, 1.76, 2.29), (250, 749, 1.22, 1.63), (750, 2000, 0.98, 1.33),
    (2001, 6000, 0.53, 0.85), (6001, 20000, 0.43, 0.74),
    (20001, 60000, 0.40, 0.70), (60001, 200000, 0.39, 0.69),
]


def _company():
    c = frappe.db.get_single_value("Global Defaults", "default_company")
    return c or frappe.db.get_value("Company", {}, "name")


def _item_group(name):
    """The protocol defaults crop_type to this group, so it has to exist."""
    if not frappe.db.exists("Item Group", name):
        frappe.get_doc({
            "doctype": "Item Group", "item_group_name": name, "is_group": 0,
            "parent_item_group": frappe.db.get_value(
                "Item Group", {"is_group": 1}, "name"),
        }).insert(ignore_permissions=True)
    return name


def _farm_type():
    if not frappe.db.exists("Farm Type", "Flowers"):
        frappe.get_doc({"doctype": "Farm Type", "farm_type_name": "Flowers"}).insert(
            ignore_permissions=True)
    return "Flowers"


def _farm(name, abbr, company):
    if frappe.db.exists("Farm", name):
        return name
    frappe.get_doc({
        "doctype": "Farm", "farm_name": name, "abbreviation": abbr,
        "company": company, "farm_code": abbr,
        "farm_type": [{"farm_type": _farm_type()}],
    }).insert(ignore_permissions=True)
    return name


def _item(code, group="Summer Flowers"):
    if frappe.db.exists("Item", code):
        return code
    if not frappe.db.exists("Item Group", group):
        group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
    frappe.get_doc({
        "doctype": "Item", "item_code": code, "item_name": code,
        "item_group": group, "stock_uom": "Nos", "is_stock_item": 0,
    }).insert(ignore_permissions=True)
    return code


def _settings():
    s = frappe.get_single("Summer Flower Settings")
    s.default_price_per_stem = 0.35
    s.pot_holding_rate_per_month = 0.211
    s.hardening_rate = 0.05
    s.lab_turnaround_weeks = 4
    year = frappe.utils.nowdate()[:4]
    s.tc_price_bands = []
    for lo, hi, s3, s4 in TC_BANDS:
        s.append("tc_price_bands", {
            "price_year": int(year), "from_qty": lo, "to_qty": hi,
            "stage_3_rate": s3, "stage_4_rate": s4})
    s.save(ignore_permissions=True)


def _protocol(variety, farm, per_sqm, pinch, interval, curve, company):
    name = frappe.db.get_value("Summer Flower Protocol",
                               {"variety": variety, "farm": farm})
    doc = (frappe.get_doc("Summer Flower Protocol", name) if name
           else frappe.new_doc("Summer Flower Protocol"))
    doc.update({
        "variety": variety, "farm": farm, "company": company,
        "protocol_status": "Active",
        "plants_per_sqm_net": per_sqm, "net_gross_ratio": 0.8,
        "plants_per_bed": 1000, "min_planting_beds": 4,
        "weeks_to_pinch": pinch, "flush_interval_weeks": interval,
        "sticking_to_planting_weeks": 3, "calendar_rounding_weeks": 1,
        "weeks_on_tray": 3, "weeks_on_pot": 8, "weeks_to_max_pc": 4,
        "hardening_weeks": 3,
        "cuttings_per_plant_per_week": 1, "plants_per_pot": 4, "pots_per_sqm": 31,
        "max_multiplication_cycles": 4, "multiplication_factor_per_cycle": 1,
        "cycle_time_weeks": 1, "motherstock_life_weeks": 52,
        "rooting_success_pct": 90, "field_establishment_pct": 95,
        "climate_note": f"{farm} standard.",
    })
    doc.flush_schedule = []
    for i, stems in enumerate(curve, start=1):
        doc.append("flush_schedule", {
            "flush_number": i, "weeks_from_pinch": interval * i,
            "stems_per_plant": stems})
    doc.grade_allocation = []
    for g, pct in GRADES:
        doc.append("grade_allocation", {"grade": g, "allocation_pct": pct,
                                        "price_per_stem": 0.35})
    doc.save(ignore_permissions=True)
    return doc.name


def _block(code, farm, ha, beds, company):
    name = frappe.db.get_value("Summer Flower Block",
                               {"block_code": code, "farm": farm})
    if name:
        return name
    return frappe.get_doc({
        "doctype": "Summer Flower Block", "block_code": code, "farm": farm,
        "company": company, "block_status": "Active",
        "gross_area_ha": ha, "total_beds": beds,
    }).insert(ignore_permissions=True).name


def _demand(variety, farm, peak, protocol, company):
    """A two-year weekly register starting next ISO week."""
    name = frappe.db.get_value("Summer Flower Market Demand",
                               {"variety": variety, "farm": farm})
    doc = (frappe.get_doc("Summer Flower Market Demand", name) if name
           else frappe.new_doc("Summer Flower Market Demand"))
    doc.update({"variety": variety, "farm": farm, "protocol": protocol,
                "company": company, "price_per_stem": 0.35,
                "target_years_ahead": 2})
    # Demand starts far enough ahead that the plan it drives is still
    # actionable: planting leads the first harvest by ~21 weeks, so a register
    # beginning next week can only ever propose plantings in the past.
    y, w = add_weeks(*current_week(), 26)
    doc.demand_weeks = []
    for i in range(104):
        yy, ww = add_weeks(y, w, i + 1)
        stems = round(peak * SEASON[(ww - 1) % 53] / 100.0 / 250) * 250
        if not stems:
            continue
        doc.append("demand_weeks", {
            "year": yy, "week_no": ww, "demand_stems": stems,
            "is_firm": 1 if i < 26 else 0})
    doc.demand_grades = []
    for g, pct in GRADES:
        doc.append("demand_grades", {"grade": g, "allocation_pct": pct})
    doc.save(ignore_permissions=True)
    return doc.name


def run():
    company = _company()
    made = {}
    _item_group("Summer Flowers")
    for name, abbr in FARMS:
        _farm(name, abbr, company)
    _settings()

    protocols = {}
    for variety, farm, per_sqm, pinch, interval, curve in PROTOCOLS:
        _item(variety)
        protocols[(variety, farm)] = _protocol(
            variety, farm, per_sqm, pinch, interval, curve, company)
    made["protocols"] = len(protocols)

    for code, farm, ha, beds in BLOCKS:
        _block(code, farm, ha, beds, company)
    made["blocks"] = frappe.db.count("Summer Flower Block")

    plans = []
    for variety, farm, peak in DEMAND:
        proto = protocols[(variety, farm)]
        md = _demand(variety, farm, peak, proto, company)
        frappe.db.commit()
        if frappe.db.exists("Summer Flower Production Plan",
                            {"market_demand": md, "docstatus": ("<", 2)}):
            continue
        from upande_summer_flowers.summer_flowers.doctype \
            .summer_flower_production_plan.summer_flower_production_plan \
            import build_from_demand
        plans.append(build_from_demand(md, weeks=104))
        frappe.db.commit()
    made["demands"] = frappe.db.count("Summer Flower Market Demand")
    made["plans"] = frappe.db.count("Summer Flower Production Plan")
    # Re-running skips plans that already exist, so pick them up here or the
    # commissioning step below would silently have nothing to do.
    if not plans:
        plans = frappe.get_all("Summer Flower Production Plan",
                               filters={"docstatus": ("<", 2)},
                               order_by="creation", pluck="name")

    # Walk two plans all the way through: allocate blocks, approve, plant,
    # size the motherstock and budget it. That exercises the real chain rather
    # than leaving four draft plans that prove nothing.
    for p in plans[:2]:
        try:
            _commission(p)
        except Exception as e:
            print(f"  commissioning {p}: {e}")
        frappe.db.commit()

    made["plantings"] = frappe.db.count("Summer Flower Planting")
    made["motherstock"] = frappe.db.count("Summer Flower Motherstock Batch")
    made["budgets"] = frappe.db.count("Summer Flower Budget")
    print(made)
    return made


def _commission(plan_name):
    """Allocate blocks, approve, and create the downstream documents."""
    plan = frappe.get_doc("Summer Flower Production Plan", plan_name)

    # The generator proposes plantings without saying where they go; a planner
    # allocates a block by hand. Round-robin over the farm's blocks so the demo
    # has something to look at.
    blocks = frappe.get_all("Summer Flower Block",
                            filters={"farm": plan.farm, "block_status": "Active"},
                            order_by="block_code", pluck="name")
    if blocks:
        i = 0
        for row in plan.plan_blocks:
            if row.is_new_planting and not row.block and not row.existing_planting:
                row.block = blocks[i % len(blocks)]
                i += 1
        plan.save(ignore_permissions=True)

    if plan.docstatus == 0:
        plan.submit()
    plan.reload()

    plan.create_plantings()
    frappe.db.commit()

    from upande_summer_flowers.summer_flowers.doctype.summer_flower_budget \
        .summer_flower_budget import build_from_plan
    if not frappe.db.exists("Summer Flower Budget", {"production_plan": plan.name}):
        build_from_plan(plan.name)

    # Motherstock is sized on the plan's peak sticking week.
    if plan.peak_weekly_sticking and not frappe.db.exists(
            "Summer Flower Motherstock Batch", {"production_plan": plan.name}):
        first = min((r.sticking_year * 100 + r.sticking_week, r)
                    for r in plan.plan_blocks if r.sticking_year)[1]
        from upande_summer_flowers.summer_flowers.planning import iso_monday
        frappe.get_doc({
            "doctype": "Summer Flower Motherstock Batch",
            "variety": plan.variety, "farm": plan.farm, "protocol": plan.protocol,
            "production_plan": plan.name, "company": plan.company,
            "batch_status": "Planned", "generation": 1,
            "peak_weekly_cuttings": plan.peak_weekly_sticking,
            "sticking_spread_weeks": 1, "apply_losses": 1,
            "build_up_cycles": 4, "tc_stage": "Stage 4",
            "first_sticking_date": iso_monday(first.sticking_year,
                                              first.sticking_week),
        }).insert(ignore_permissions=True)


def wipe():
    """Remove every demo document, children first."""
    for dt in ("Summer Flower Budget", "Summer Flower Motherstock Batch",
               "Summer Flower Planting", "Summer Flower Production Plan",
               "Summer Flower Market Demand", "Summer Flower Block",
               "Summer Flower Protocol"):
        for name in frappe.get_all(dt, pluck="name"):
            doc = frappe.get_doc(dt, name)
            # A submitted plan has to be cancelled before it will delete.
            if doc.meta.is_submittable and doc.docstatus == 1:
                doc.flags.ignore_permissions = True
                doc.cancel()
            frappe.delete_doc(dt, name, force=1, ignore_permissions=True,
                              delete_permanently=True)
    frappe.db.commit()
    print("wiped")


def nudge():
    """Simulate a colleague editing a protocol."""
    name = frappe.get_all("Summer Flower Protocol", limit=1, pluck="name")[0]
    doc = frappe.get_doc("Summer Flower Protocol", name)
    doc.climate_note = f"touched {frappe.utils.now()}"
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    print("touched", name)
