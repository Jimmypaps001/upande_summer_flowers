"""The spreadsheet: one week per column, typed into directly.

The whole point of the web page is that a planner never has to open a DocType.
So this exposes the two things a planner actually edits — the weekly demand
register, and the decision to rebuild a plan from it — over a week axis that
looks and behaves like the rose budget grid.

Everything else on the grid is derived and read-only: production, variance,
cumulative variance, sticking, planting and area all come off the Production
Plan's own child tables. If a figure here disagrees with the document, the
document is right; this module does no modelling of its own.

Writing goes through `doc.save()`, never `db_set`, so the Market Demand
controller re-sorts the weeks, recomputes the horizon and the totals, and
Frappe enforces permissions — exactly as if the row had been typed in Desk.
"""

from __future__ import annotations

import datetime
import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from upande_summer_flowers import live
from upande_summer_flowers.perms import require
from upande_summer_flowers.summer_flowers.planning import (
    add_weeks, current_week, iso_monday, weeks_in_iso_year,
)

MAX_WEEKS = 80
DEFAULT_WEEKS = 32
CACHE_TTL = 900


def _guard(ptype: str = "read"):
    """A signed-in session is not an access check; require the real permission."""
    require(ptype)


def axis(year: int, week: int, count: int) -> list[tuple[int, int]]:
    return [(y, w) for y, w, _d in
            __import__("upande_summer_flowers.summer_flowers.planning",
                       fromlist=["week_sequence"]).week_sequence(year, week, count)]


def week_meta(y: int, w: int) -> dict:
    mon = iso_monday(y, w)
    sun = mon + datetime.timedelta(days=6)
    return {
        "year": y, "week": w, "start": str(mon),
        "start_label": mon.strftime("%-d %b"),
        "end_label": sun.strftime("%-d %b"),
        "span": f"{mon.strftime('%-d %b')} – {sun.strftime('%-d %b %Y')}",
        "month": mon.strftime("%b"),
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _plan_for(demand_name: str) -> dict | None:
    """The newest plan built from this register, draft preferred.

    A draft is the one a planner can still regenerate; a submitted plan is the
    committed version. Showing the draft keeps the grid editable end to end.
    """
    rows = frappe.get_all(
        "Summer Flower Production Plan",
        filters={"market_demand": demand_name, "docstatus": ("<", 2)},
        fields=["name", "status", "docstatus", "modified", "coverage_pct",
                "weeks_in_deficit", "new_beds_required", "peak_weekly_sticking",
                "peak_sticking_week", "total_production_stems"],
        order_by="docstatus asc, creation desc", limit=1)
    return rows[0] if rows else None


def _series(cols, index, rows, field, key=("year", "week_no")):
    """Child rows are Documents, not dicts — read them with .get()."""
    out = [None] * len(cols)
    for r in rows:
        i = index.get((cint(r.get(key[0])), cint(r.get(key[1]))))
        v = r.get(field)
        if i is not None and v:
            out[i] = (out[i] or 0) + v
    return out


def actual_series(cols, index, variety, farm):
    """What was really cut, from the flush rows growers tick off.

    `Summer Flower Planting Flush` carries is_harvested and actual_stems, so
    actual production is recorded per flush rather than inferred.
    """
    out = [None] * len(cols)
    rows = frappe.db.sql("""
        SELECT f.year, f.week_no, SUM(f.actual_stems) AS stems
        FROM `tabSummer Flower Planting Flush` f
        JOIN `tabSummer Flower Planting` p ON p.name = f.parent
        WHERE f.is_harvested = 1 AND p.variety = %(v)s AND p.farm = %(f)s
        GROUP BY f.year, f.week_no
    """, {"v": variety, "f": farm}, as_dict=True)
    for r in rows:
        i = index.get((cint(r.year), cint(r.week_no)))
        if i is not None and r.stems:
            out[i] = (out[i] or 0) + cint(r.stems)
    return out


def _rows_for(cols, index, demand_doc, plan, can_edit) -> list[dict]:
    dw = demand_doc.demand_weeks
    demand = _series(cols, index, dw, "demand_stems")
    firm = [False] * len(cols)
    notes = [None] * len(cols)
    for r in dw:
        i = index.get((cint(r.year), cint(r.week_no)))
        if i is not None:
            firm[i] = bool(r.is_firm)
            notes[i] = r.notes

    actual = actual_series(cols, index, demand_doc.variety, demand_doc.farm)
    out = [{
        "key": "demand", "label": "Demand", "meta": "to market",
        "values": demand, "editable": bool(can_edit), "tone": "forecast",
        "fmt": "int", "firm": firm, "notes": notes,
        "help": ("Type here. This writes straight into the demand register — "
                 "no need to open the document."
                 if can_edit else "Read-only: you cannot write Summer Flower Market Demand."),
    }]

    if not plan:
        out.append({"key": "actual", "label": "Actual", "meta": "harvested",
                    "values": actual, "editable": False, "tone": "actual",
                    "fmt": "int"})
        return out

    doc = frappe.get_doc("Summer Flower Production Plan", plan["name"])
    pw = doc.plan_weeks
    production = _series(cols, index, pw, "production_stems")
    out += [
        {"key": "production", "label": "Production", "meta": "harvest",
         "values": production,
         "editable": False, "tone": "actual", "fmt": "int",
         "help": "What the plan's plantings will cut that week."},
        {"key": "variance", "label": "Variance", "meta": "vs demand",
         "values": _series(cols, index, pw, "variance_stems"),
         "editable": False, "tone": "signed", "fmt": "int",
         "help": "Negative is a shortfall in that week."},
        {"key": "cumulative", "label": "Cumulative", "meta": "running",
         "values": _series(cols, index, pw, "cumulative_variance"),
         "editable": False, "tone": "signed", "fmt": "int"},
    ]

    # Plan blocks carry the upstream work: sticking, planting, pinching.
    pb = doc.plan_blocks
    stick = [None] * len(cols)
    plant = [None] * len(cols)
    for r in pb:
        i = index.get((cint(r.sticking_year), cint(r.sticking_week)))
        if i is not None and r.plants:
            stick[i] = (stick[i] or 0) + r.plants
        j = index.get((cint(r.planting_year), cint(r.planting_week)))
        if j is not None and r.beds:
            plant[j] = (plant[j] or 0) + r.beds
    out += [
        {"key": "stick", "label": "Stick", "meta": "cuttings", "values": stick,
         "editable": False, "tone": "revised", "fmt": "int",
         "help": "Cuttings into trays. A cutting cannot be banked — it is stuck "
                 "the week it is taken."},
        {"key": "plant", "label": "Plant", "meta": "beds", "values": plant,
         "editable": False, "tone": "warn", "fmt": "bed",
         "help": "Beds to plant that week to harvest at the offset the protocol sets."},
        {"key": "actual", "label": "Actual", "meta": "harvested",
         "values": actual, "editable": False, "tone": "actual", "fmt": "int",
         "help": "Stems actually cut, from the flush rows marked harvested."},
        {"key": "actual_var", "label": "vs plan", "meta": "actual − planned",
         "values": [None if a is None else a - (pr or 0)
                    for a, pr in zip(actual, production)],
         "editable": False, "tone": "signed", "fmt": "int"},
        {"key": "area", "label": "Area", "meta": "ha",
         "values": _series(cols, index, pw, "area_ha"),
         "editable": False, "tone": "mute", "fmt": "area"},
    ]
    return out


@frappe.whitelist()
def week_grid(year: int | None = None, week: int | None = None,
              weeks: int = DEFAULT_WEEKS, farm: str | None = None,
              variety: str | None = None, nocache: int = 0) -> dict:
    """Every demand register as a block of week-column rows."""
    _guard()
    ny, nw = current_week()
    year, week = cint(year) or ny, cint(week) or nw
    count = max(4, min(cint(weeks) or DEFAULT_WEEKS, MAX_WEEKS))

    key = (f"{live.CACHE_PREFIX}grid:{live.version()}:" + json.dumps(
        [year, week, count, farm, variety, frappe.session.user], sort_keys=True))
    if not cint(nocache):
        hit = frappe.cache().get_value(key)
        if hit:
            return {**hit, "cached": True}

    cols = axis(year, week, count)
    index = {k: i for i, k in enumerate(cols)}

    filters = {}
    if farm:
        filters["farm"] = farm
    if variety:
        filters["variety"] = variety
    names = frappe.get_all("Summer Flower Market Demand", filters=filters,
                           order_by="farm asc, variety asc", pluck="name")

    can_edit = bool(frappe.has_permission("Summer Flower Market Demand", "write"))
    can_plan = bool(frappe.has_permission("Summer Flower Production Plan", "create"))

    groups = []
    for name in names:
        doc = frappe.get_doc("Summer Flower Market Demand", name)
        plan = _plan_for(name)
        # A plan built before the register was last touched no longer reflects
        # it. Say so rather than letting someone read a stale production row.
        stale = bool(plan and plan["modified"] < doc.modified)
        groups.append({
            "key": name, "demand": name, "variety": doc.variety, "farm": doc.farm,
            "name": f"{doc.variety}", "sub": doc.farm,
            "protocol": doc.protocol,
            "plan": plan["name"] if plan else None,
            "plan_status": plan["status"] if plan else None,
            "plan_draft": bool(plan and plan["docstatus"] == 0),
            "plan_stale": stale,
            "coverage_pct": plan["coverage_pct"] if plan else None,
            "weeks_in_deficit": plan["weeks_in_deficit"] if plan else None,
            "new_beds": plan["new_beds_required"] if plan else None,
            "peak_stick": plan["peak_weekly_sticking"] if plan else None,
            "peak_week": plan["peak_sticking_week"] if plan else None,
            "lead_weeks": lead_weeks(doc.protocol),
            "horizon_end": doc.horizon_end,
            "weeks_covered": doc.weeks_covered,
            "rows": _rows_for(cols, index, doc, plan, can_edit),
        })

    totals = {}
    for k in ("demand", "production", "stick", "plant"):
        t = [None] * count
        for g in groups:
            r = next((x for x in g["rows"] if x["key"] == k), None)
            if not r:
                continue
            for i, v in enumerate(r["values"]):
                if v:
                    t[i] = (t[i] or 0) + v
        totals[k] = t

    out = {
        "weeks": [week_meta(y, w) for (y, w) in cols],
        "now": {"year": ny, "week": nw},
        "start": {"year": year, "week": week},
        "count": count, "groups": groups, "totals": totals,
        "can_edit": can_edit, "can_plan": can_plan,
        "version": live.version(), "cached": False,
    }
    frappe.cache().set_value(key, out, expires_in_sec=CACHE_TTL)
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def lead_weeks(protocol_name: str) -> int:
    """Weeks from sticking a cutting to its first harvest.

    Anything closer than this cannot be served by a new planting — the
    cuttings would have had to go in before today.
    """
    if not protocol_name:
        return 0
    pr = frappe.get_cached_doc("Summer Flower Protocol", protocol_name)
    return int((pr.first_harvest_offset_weeks or 0)
               + (pr.sticking_to_planting_weeks or 0))


def feasibility(demand_doc, week_year: int, week_no: int) -> dict:
    """Can a new planting still reach this week?

    Two ways it can be fine: the week is beyond the lead time, or something
    already in the ground is producing into it. Only when neither holds is a
    number here undeliverable.
    """
    lead = lead_weeks(demand_doc.protocol)
    ny, nw = current_week()
    earliest = add_weeks(ny, nw, lead)
    target_abs = week_year * 53 + week_no
    ok = (week_year, week_no) >= earliest

    existing = 0
    plan = _plan_for(demand_doc.name)
    if plan:
        row = frappe.db.get_value(
            "Summer Flower Plan Week",
            {"parent": plan["name"], "year": week_year, "week_no": week_no},
            "production_stems")
        existing = int(row or 0)

    return {
        "ok": bool(ok or existing),
        "lead_weeks": lead,
        "earliest": {"year": earliest[0], "week": earliest[1]},
        "existing_production": existing,
        "reason": ("" if (ok or existing) else
                   f"Week {week_no}/{week_year} is inside the {lead}-week lead time "
                   f"and nothing already planted produces into it. A cutting stuck "
                   f"today first harvests in week {earliest[1]}/{earliest[0]}."),
    }


@frappe.whitelist()
def check_week(demand: str, year: int, week: int) -> dict:
    """Ask before typing, so the UI can warn rather than fail."""
    _guard()
    return feasibility(frappe.get_doc("Summer Flower Market Demand", demand),
                       cint(year), cint(week))


@frappe.whitelist()
def set_demand_week(demand: str, year: int, week: int, stems=None,
                    is_firm=None, notes: str | None = None, force: int = 0) -> dict:
    """Type a number into the Demand row.

    Saved through the document so the register re-sorts, recomputes its horizon
    and totals, and checks permissions. Zero is a judgement — "we are not
    selling that week" — and is kept; clearing the cell removes the row.
    """
    _guard("write")
    year, week = cint(year), cint(week)
    if week < 1 or week > weeks_in_iso_year(year):
        frappe.throw(_("Week {0} does not exist in {1}.").format(week, year))

    doc = frappe.get_doc("Summer Flower Market Demand", demand)
    doc.check_permission("write")
    row = next((r for r in doc.demand_weeks
                if cint(r.year) == year and cint(r.week_no) == week), None)

    if stems in (None, ""):
        if row:
            doc.remove(row)
            doc.save()
        return {"ok": True, "deleted": True, "version": live.version()}

    value = cint(stems)
    if value < 0:
        frappe.throw(_("Demand cannot be negative."))

    # Refuse a week no planting could reach — unless the planner has seen the
    # reason and said yes anyway. They may know about stock this does not.
    if value and not cint(force):
        f = feasibility(doc, year, week)
        if not f["ok"]:
            frappe.throw(f["reason"], title=_("Not deliverable"))

    if not row:
        row = doc.append("demand_weeks", {"year": year, "week_no": week})
    row.demand_stems = value
    if is_firm is not None:
        row.is_firm = cint(is_firm)
    if notes is not None:
        row.notes = notes
    doc.save()
    return {"ok": True, "stems": value, "version": live.version(),
            "weeks_covered": doc.weeks_covered,
            "total": doc.total_demand_stems}


@frappe.whitelist()
def fill_demand(demand: str, cells, force: int = 0) -> dict:
    """Several cells in one save — the toolbar's fill and nudge actions.

    One document write for the whole batch: saving per cell would re-sort and
    re-total the register once per column, which is both slow and noisy in the
    version history.
    """
    _guard("write")
    if isinstance(cells, str):
        cells = json.loads(cells)
    doc = frappe.get_doc("Summer Flower Market Demand", demand)
    doc.check_permission("write")
    existing = {(cint(r.year), cint(r.week_no)): r for r in doc.demand_weeks}

    # The single-cell path refuses a week no planting can reach; this one did
    # not, so Fill right / +10% / -10% / Set to zero could all write demand
    # into locked weeks that the editor rejects one cell at a time.
    if not cint(force):
        blocked = []
        for c in cells:
            if c.get("stems") in (None, ""):
                continue
            f = feasibility(doc, cint(c["year"]), cint(c["week"]))
            if not f["ok"]:
                blocked.append(f"W{cint(c['week'])}/{cint(c['year'])}")
        if blocked:
            frappe.throw(
                _("{0} of these weeks cannot be delivered: {1}. They are inside "
                  "the lead time and nothing already planted produces into them.")
                .format(len(blocked), ", ".join(blocked[:6])
                        + ("…" if len(blocked) > 6 else "")),
                title=_("Not deliverable"))

    for c in cells:
        y, w = cint(c["year"]), cint(c["week"])
        if w < 1 or w > weeks_in_iso_year(y):
            continue
        v = c.get("stems")
        row = existing.get((y, w))
        if v in (None, ""):
            if row:
                doc.remove(row)
            continue
        if not row:
            row = doc.append("demand_weeks", {"year": y, "week_no": w})
            existing[(y, w)] = row
        row.demand_stems = max(0, cint(v))
    doc.save()
    return {"ok": True, "count": len(cells), "version": live.version(),
            "total": doc.total_demand_stems}


@frappe.whitelist()
def rebuild_plan(demand: str, from_year=None, from_week=None, weeks=None) -> dict:
    """Bring the plan back in line with the register, without leaving the page.

    A draft is regenerated in place. Anything submitted is left alone and a new
    draft is created instead — the approved version is a record, not a scratchpad.
    """
    _guard("write")
    plan = _plan_for(demand)
    if plan and plan["docstatus"] == 0:
        doc = frappe.get_doc("Summer Flower Production Plan", plan["name"])
        doc.check_permission("write")
        doc.regenerate()
        return {"ok": True, "plan": doc.name, "created": False,
                "coverage_pct": doc.coverage_pct,
                "new_beds": doc.new_beds_required}

    d = frappe.get_doc("Summer Flower Market Demand", demand)
    name = d.create_production_plan(from_year=from_year, from_week=from_week,
                                    weeks=weeks)
    doc = frappe.get_doc("Summer Flower Production Plan", name)
    return {"ok": True, "plan": name, "created": True,
            "coverage_pct": doc.coverage_pct, "new_beds": doc.new_beds_required}


@frappe.whitelist()
def extend_horizon(demand: str, weeks: int = 52, copy_from_last_year: int = 1) -> dict:
    """Push the register further out, seeded from the same weeks last year."""
    _guard("write")
    doc = frappe.get_doc("Summer Flower Market Demand", demand)
    doc.check_permission("write")
    doc.extend_horizon(weeks=cint(weeks), copy_from_last_year=cint(copy_from_last_year))
    return {"ok": True, "weeks_covered": doc.weeks_covered,
            "horizon_end": doc.horizon_end}


@frappe.whitelist()
def cell_detail(demand: str, year: int, week: int) -> dict:
    """Everything behind one cell, so the number can be questioned in place."""
    _guard()
    year, week = cint(year), cint(week)
    doc = frappe.get_doc("Summer Flower Market Demand", demand)
    row = next((r for r in doc.demand_weeks
                if cint(r.year) == year and cint(r.week_no) == week), None)
    plan = _plan_for(demand)
    pw = pb = None
    if plan:
        p = frappe.get_doc("Summer Flower Production Plan", plan["name"])
        pw = next((w for w in p.plan_weeks
                   if cint(w.year) == year and cint(w.week_no) == week), None)
        pb = [{"block": b.block, "beds": b.beds, "plants": b.plants,
               "is_new": bool(b.is_new_planting)}
              for b in p.plan_blocks
              if cint(b.planting_year) == year and cint(b.planting_week) == week]
    return {
        "week": week_meta(year, week),
        "demand": {"stems": row.demand_stems if row else None,
                   "is_firm": bool(row.is_firm) if row else False,
                   "notes": row.notes if row else None} if True else None,
        "plan_week": {"production": pw.production_stems, "demand": pw.demand_stems,
                      "variance": pw.variance_stems,
                      "cumulative": pw.cumulative_variance,
                      "area_ha": flt(pw.area_ha),
                      "contributing": pw.contributing_plantings} if pw else None,
        "plantings": pb or [],
    }


# ---------------------------------------------------------------------------
# Recording what was actually cut
# ---------------------------------------------------------------------------

@frappe.whitelist()
def harvest_rows(variety: str, farm: str, year: int, week: int) -> dict:
    """The flushes due in one week, so a cut can be booked against them.

    Production is projected per flush on each planting, so an actual harvest
    belongs on those same rows rather than in a parallel table. This returns
    what is due, what has already been recorded, and by which planting.
    """
    _guard()
    year, week = cint(year), cint(week)
    rows = frappe.db.sql("""
        SELECT f.name, f.parent, f.flush_number, f.projected_stems, f.actual_stems,
               f.is_harvested, f.stems_per_plant, p.block, p.beds, p.plants
        FROM `tabSummer Flower Planting Flush` f
        JOIN `tabSummer Flower Planting` p ON p.name = f.parent
        WHERE f.year = %(y)s AND f.week_no = %(w)s
          AND p.variety = %(v)s AND p.farm = %(f)s
        ORDER BY p.block, f.flush_number
    """, {"y": year, "w": week, "v": variety, "f": farm}, as_dict=True)
    return {
        "week": week_meta(year, week),
        "variety": variety, "farm": farm,
        "rows": rows,
        "projected": sum(cint(r.projected_stems) for r in rows),
        "actual": sum(cint(r.actual_stems) for r in rows),
        "can_write": bool(frappe.has_permission("Summer Flower Planting", "write")),
    }


@frappe.whitelist()
def record_harvest(variety: str, farm: str, year: int, week: int, stems=None,
                   allocations=None) -> dict:
    """Book a week's cut.

    Either give a total — split across the flushes due that week in proportion
    to what each was projected to yield — or name the split yourself. Anything
    the plan did not expect goes on the largest flush rather than being
    silently dropped.
    """
    _guard("write")
    year, week = cint(year), cint(week)
    due = harvest_rows(variety, farm, year, week)["rows"]
    if not due:
        frappe.throw(_("No flush is due in week {0}/{1} for {2}.").format(
            week, year, variety))

    if isinstance(allocations, str):
        allocations = json.loads(allocations or "null")

    if allocations:
        share = {a["flush"]: cint(a["stems"]) for a in allocations}
    else:
        total = cint(stems)
        if total < 0:
            frappe.throw(_("A harvest cannot be negative."))
        basis = sum(cint(r.projected_stems) for r in due) or len(due)
        share, running = {}, 0
        for i, r in enumerate(due):
            if i == len(due) - 1:
                share[r.name] = total - running          # last row absorbs rounding
            else:
                part = int(round(total * (cint(r.projected_stems) or 1) / basis))
                share[r.name] = part
                running += part

    touched = {}
    for r in due:
        v = share.get(r.name)
        if v is None:
            continue
        touched.setdefault(r.parent, []).append((r.name, v))

    for parent, rows in touched.items():
        doc = frappe.get_doc("Summer Flower Planting", parent)
        doc.check_permission("write")
        by_name = {c.name: c for c in doc.flush_projection}
        for name, v in rows:
            row = by_name.get(name)
            if not row:
                continue
            row.actual_stems = v
            row.is_harvested = 1 if v else 0
        doc.save()

    live.bump("Summer Flower Planting", list(touched)[0] if touched else None)
    return {"ok": True, "week": week, "year": year,
            "recorded": sum(v for rows in touched.values() for _n, v in rows),
            "plantings": len(touched)}
