"""One payload for the Summer Flowers dashboard: state, exceptions, deadlines.

Nothing here recomputes the model. The doctypes already work out coverage,
deficits, sticking peaks, TC dates and their own schedule warnings on save;
this reads those fields and turns them into two things a planner can act on:

  exceptions   why the plan as it stands cannot be trusted, each pointing at
               the document that would fix it
  act by       what has to happen next, soonest first, overdue at the top

If a number here disagrees with the document it came from, the document wins —
that is the whole reason this module does no arithmetic of its own beyond
counting days.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from upande_summer_flowers import live
from upande_summer_flowers.perms import require
from upande_summer_flowers.summer_flowers.planning import current_week, iso_monday

CACHE_TTL = 900


def _guard(ptype: str = "read"):
    """A signed-in session is not an access check; require the real permission."""
    require(ptype)


def _perms(doctype: str) -> dict:
    if not frappe.db.exists("DocType", doctype):
        return {"exists": False}
    return {
        "exists": True,
        "read": bool(frappe.has_permission(doctype, "read")),
        "write": bool(frappe.has_permission(doctype, "write")),
        "create": bool(frappe.has_permission(doctype, "create")),
        "submit": bool(frappe.has_permission(doctype, "submit")),
    }


def _route(doctype: str, name: str | None = None) -> str:
    slug = doctype.lower().replace(" ", "-")
    return f"/app/{slug}/{name}" if name else f"/app/{slug}"


def select_options(doctype: str, fieldname: str) -> list[str]:
    df = frappe.get_meta(doctype).get_field(fieldname)
    if not df or not df.options:
        return []
    return [o.strip() for o in str(df.options).split("\n") if o.strip()]


@frappe.whitelist()
def bootstrap() -> dict:
    """Labels, options, permissions and currency — read off the DocTypes.

    The page must not restate any of this: a Select gaining an option or a role
    losing write access has to show up without anyone editing the template.
    """
    _guard()
    # Never fall back to Global Defaults.default_currency: on a site where
    # nobody set it, Frappe leaves it as INR, and a Kenyan grower buying
    # tissue culture in euros sees rupees on the front page. The company is
    # the authority; the supplier's price list decides what TC is quoted in.
    company = (frappe.db.get_single_value("Global Defaults", "default_company")
               or frappe.db.get_value("Company", {}, "name"))
    currency = (frappe.db.get_value("Company", company, "default_currency")
                if company else None) or "EUR"
    return {
        "version": live.version(),
        "user": frappe.session.user,
        "full_name": frappe.utils.get_fullname(frappe.session.user),
        "currency": currency,
        "company": company,
        "perms": {dt: _perms(dt) for dt in (
            "Summer Flower Protocol", "Summer Flower Market Demand",
            "Summer Flower Production Plan", "Summer Flower Planting",
            "Summer Flower Block", "Summer Flower Motherstock Batch",
            "Summer Flower Budget", "Summer Flower Settings")},
        "status_options": {
            "plan": select_options("Summer Flower Production Plan", "status"),
            "planting": select_options("Summer Flower Planting", "planting_status"),
            "block": select_options("Summer Flower Block", "block_status"),
            "batch": select_options("Summer Flower Motherstock Batch", "batch_status"),
            "protocol": select_options("Summer Flower Protocol", "protocol_status"),
        },
        "routes": {dt: _route(dt) for dt in (
            "Summer Flower Protocol", "Summer Flower Market Demand",
            "Summer Flower Production Plan", "Summer Flower Planting",
            "Summer Flower Block", "Summer Flower Motherstock Batch",
            "Summer Flower Budget", "Summer Flower Settings")},
    }


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

def exceptions(ov: dict, blocks: dict, protocols: list, plantings: list) -> list[dict]:
    """Everything standing between this plan and someone acting on it."""
    out = []
    today = getdate(nowdate())

    def add(level, code, title, text, doctype=None, name=None):
        out.append({"level": level, "code": code, "title": title, "text": text,
                    "doctype": doctype, "name": name,
                    "href": _route(doctype, name) if doctype else None})

    # --- demand registers -------------------------------------------------
    for d in ov["demands"]:
        label = f"{d.variety} · {d.farm}"
        if not d.weeks_covered:
            add("blocker", "demand_empty", f"{label}: demand register is empty",
                "There is nothing to plan against until weekly demand is entered.",
                "Summer Flower Market Demand", d.name)
            continue
        # horizon_status is the doctype's own verdict; trust it rather than
        # re-deriving "are we far enough ahead".
        if (d.horizon_gap_weeks or 0) > 0:
            add("warn", "horizon_short",
                f"{label}: demand runs out {d.horizon_gap_weeks} weeks early",
                f"The register covers {d.weeks_covered} weeks but the target is "
                f"{d.target_years_ahead} year(s) ahead. Anything planted for the "
                "tail of the horizon is being planned blind.",
                "Summer Flower Market Demand", d.name)

    # --- plans ------------------------------------------------------------
    approved = {p.name for p in ov["plans"] if p.docstatus == 1}
    for p in ov["plans"]:
        label = f"{p.variety} · {p.farm}"
        if p.docstatus == 0:
            add("warn", "plan_draft", f"{label}: plan {p.name} is not approved",
                f"Nothing downstream — plantings, motherstock, budget — can be "
                f"created from a draft. Currently {p.status or 'Draft'}.",
                "Summer Flower Production Plan", p.name)
        if (p.weeks_in_deficit or 0) > 0:
            add("blocker", "plan_deficit",
                f"{label}: {p.weeks_in_deficit} weeks short of demand",
                f"Worst single week is {int(p.worst_weekly_deficit or 0):,} stems "
                f"under. Coverage is {flt(p.coverage_pct, 1)}%.",
                "Summer Flower Production Plan", p.name)
        if p.docstatus == 1 and not p.budget:
            add("info", "plan_unbudgeted", f"{label}: plan {p.name} has no budget",
                "An approved plan can be costed and posted to the accounts.",
                "Summer Flower Production Plan", p.name)

    # A planting week that has already gone by cannot be actioned.
    rows = frappe.get_all(
        "Summer Flower Plan Block",
        filters={"parenttype": "Summer Flower Production Plan"},
        fields=["parent", "block", "beds", "planting_in_past", "below_minimum",
                "planting_year", "planting_week", "is_new_planting"])
    late = [r for r in rows if r.planting_in_past and r.is_new_planting
            and r.parent in approved]
    if late:
        by_plan: dict = {}
        for r in late:
            by_plan.setdefault(r.parent, []).append(r)
        for plan_name, rs in by_plan.items():
            add("blocker", "planting_in_past",
                f"{plan_name}: {len(rs)} planting(s) dated in the past",
                "The plan asks for beds to have been planted on a date that has "
                "gone. Those harvest weeks cannot be hit — re-run the plan from "
                "this week or move the delivery.",
                "Summer Flower Production Plan", plan_name)
    small = [r for r in rows if r.below_minimum and r.is_new_planting]
    if small:
        add("warn", "below_minimum",
            f"{len(small)} planting(s) below the minimum block size",
            "Plantings under the protocol's minimum are rarely worth the labour. "
            "Fold them into a larger planting or accept the cost.",
            "Summer Flower Production Plan", small[0].parent)
    unallocated = [r for r in rows if r.is_new_planting and not r.block]
    if unallocated:
        add("warn", "no_block",
            f"{len(unallocated)} proposed planting(s) have no block",
            "A planting with no block allocated cannot be created. Allocate the "
            "beds on the plan before approving it.",
            "Summer Flower Production Plan", unallocated[0].parent)

    # --- motherstock ------------------------------------------------------
    for b in ov["motherstock"]:
        label = f"{b.variety} · {b.farm}"
        # The batch computes this itself on save — surface it, do not re-derive.
        if b.schedule_warning:
            add("blocker", "tc_schedule", f"{label}: motherstock schedule will not work",
                b.schedule_warning, "Summer Flower Motherstock Batch", b.name)
        if b.get("renewal_overdue"):
            add("blocker", "tc_renewal", f"{label}: renewal TC order is overdue",
                f"The replacement generation had to be ordered by "
                f"{frappe.utils.formatdate(b.renewal_tc_order_date)}. Motherstock "
                f"expires {frappe.utils.formatdate(b.expiry_date)}.",
                "Summer Flower Motherstock Batch", b.name)
        elif b.get("days_to_renewal") is not None and b["days_to_renewal"] <= 42:
            add("warn", "tc_renewal_soon", f"{label}: renewal TC order due soon",
                f"{b['days_to_renewal']} days until the replacement generation "
                "must be ordered.", "Summer Flower Motherstock Batch", b.name)

    plans_needing_stock = [p for p in ov["plans"]
                           if p.docstatus == 1 and (p.peak_weekly_sticking or 0)]
    # api.overview() does not return the batch's plan link, so ask for it
    # rather than reading a key that is never there — that read silently made
    # every approved plan look unsupplied.
    covered = set(frappe.get_all(
        "Summer Flower Motherstock Batch",
        filters={"production_plan": ("is", "set")},
        pluck="production_plan"))
    for p in plans_needing_stock:
        if p.name not in covered:
            add("blocker", "no_motherstock",
                f"{p.variety} · {p.farm}: no motherstock for plan {p.name}",
                f"The plan peaks at {int(p.peak_weekly_sticking or 0):,} cuttings in "
                f"{p.peak_sticking_week}. Nothing has been sized or ordered to "
                "supply them.", "Summer Flower Production Plan", p.name)

    # --- protocols --------------------------------------------------------
    for pr in protocols:
        label = f"{pr.variety} · {pr.farm}"
        if not pr.total_flushes:
            add("blocker", "protocol_no_flush", f"{label}: protocol has no flush curve",
                "Without a flush schedule the protocol yields nothing and every "
                "plan built on it is empty.", "Summer Flower Protocol", pr.name)
        if pr.get("grade_total_pct") is not None and pr.grade_total_pct \
                and abs(flt(pr.grade_total_pct) - 100) > 0.5:
            add("warn", "grade_split", f"{label}: grade split is {flt(pr.grade_total_pct,1)}%",
                "Grade allocations should total 100% or the stem mix will not "
                "reconcile against sales.", "Summer Flower Protocol", pr.name)

    # --- blocks -----------------------------------------------------------
    for b in blocks["blocks"]:
        if (b.beds_occupied or 0) > (b.total_beds or 0):
            add("blocker", "block_oversubscribed",
                f"Block {b.block_code}: more beds planted than it has",
                f"{b.beds_occupied} beds standing on {b.total_beds}. Something is "
                "planted twice, or the register is wrong.",
                "Summer Flower Block", b.name)

    if not frappe.db.get_all("Summer Flower TC Price Band", limit=1):
        add("warn", "no_tc_prices", "No tissue-culture price bands",
            "Motherstock batches cannot be costed until Summer Flower Settings "
            "carries the supplier's price bands.", "Summer Flower Settings")

    order = {"blocker": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: order.get(a["level"], 3))
    return out


# ---------------------------------------------------------------------------
# Act by
# ---------------------------------------------------------------------------

def act_by(ov: dict, horizon_weeks: int = 16) -> list[dict]:
    """Dated commitments, soonest first. Overdue counts as soonest."""
    today = getdate(nowdate())
    out = []

    def add(date, action, what, subject, doctype, name, qty=None, unit=None):
        if not date:
            return
        d = getdate(date)
        days = (d - today).days
        if days > horizon_weeks * 7:
            return
        y, w, _ = d.isocalendar()
        out.append({"date": str(d), "year": y, "week": w, "days": days,
                    "action": action, "what": what, "subject": subject,
                    "qty": qty, "unit": unit, "doctype": doctype, "name": name,
                    "href": _route(doctype, name), "overdue": days < 0})

    for b in ov["motherstock"]:
        subject = f"{b.variety} · {b.farm}"
        add(b.tc_order_date, "Order TC", "place the tissue-culture order", subject,
            "Summer Flower Motherstock Batch", b.name,
            b.tc_plants_required, "plantlets")
        add(b.tc_on_farm_date, "TC arrives", "plantlets must be on the farm", subject,
            "Summer Flower Motherstock Batch", b.name,
            b.tc_plants_required, "plantlets")
        add(b.max_pc_date, "Mothers ready", "motherstock at full production", subject,
            "Summer Flower Motherstock Batch", b.name, b.mother_plants, "plants")
        add(b.renewal_tc_order_date, "Renew TC", "order the next generation", subject,
            "Summer Flower Motherstock Batch", b.name,
            b.tc_plants_required, "plantlets")

    approved = [p.name for p in ov["plans"] if p.docstatus == 1]
    if approved:
        rows = frappe.get_all(
            "Summer Flower Plan Block",
            filters={"parent": ["in", approved], "is_new_planting": 1},
            fields=["parent", "block", "beds", "plants", "sticking_year",
                    "sticking_week", "planting_year", "planting_week",
                    "planting_date", "pinch_date"])
        heads = {p.name: f"{p.variety} · {p.farm}" for p in ov["plans"]}
        for r in rows:
            subject = heads.get(r.parent, r.parent)
            if r.sticking_year and r.sticking_week:
                add(iso_monday(r.sticking_year, r.sticking_week), "Stick",
                    f"cuttings for {r.block or 'unallocated'}", subject,
                    "Summer Flower Production Plan", r.parent, r.plants, "cuttings")
            add(r.planting_date, "Plant", f"beds into {r.block or 'unallocated'}",
                subject, "Summer Flower Production Plan", r.parent, r.beds, "beds")
            add(r.pinch_date, "Pinch", f"{r.block or 'unallocated'}", subject,
                "Summer Flower Production Plan", r.parent, r.plants, "plants")

    for p in frappe.get_all("Summer Flower Planting",
                            filters={"planting_status": ["!=", "Uprooted"]},
                            fields=["name", "block", "variety", "farm", "beds",
                                    "planned_uproot_date"]):
        add(p.planned_uproot_date, "Uproot", f"block {p.block}",
            f"{p.variety} · {p.farm}", "Summer Flower Planting", p.name,
            p.beds, "beds")

    out.sort(key=lambda a: a["date"])
    return out


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

@frappe.whitelist()
def state(farm: str | None = None, variety: str | None = None,
          nocache: int = 0) -> dict:
    """Everything the dashboard draws, in one call, cached until a doc moves."""
    _guard()
    key = (f"{live.CACHE_PREFIX}{live.version()}:"
           + json.dumps([farm, variety, frappe.session.user], sort_keys=True))
    if not cint(nocache):
        hit = frappe.cache().get_value(key)
        if hit:
            # Never hand back the cached object itself; Frappe keeps an
            # in-process reference and mutating it rewrites the next read.
            return {**hit, "cached": True}

    from upande_summer_flowers.summer_flowers import api

    ov = api.overview(farm=farm, variety=variety)

    # api.overview() does not return firm_demand_stems, and reading a key that
    # is never there quietly reported "0 firm" on every dashboard.
    firm = {r.name: r.firm_demand_stems for r in frappe.get_all(
        "Summer Flower Market Demand", fields=["name", "firm_demand_stems"])}
    for d in ov["demands"]:
        d["firm_demand_stems"] = firm.get(d.name) or 0

    # One register can carry several plans — a submitted one plus the drafts
    # that followed it. Summing them all double-counts production and pushes
    # coverage past 200%. Keep the newest per register, draft first.
    newest = {}
    for pl in ov["plans"]:
        k = pl.get("market_demand") or pl.name
        cur = newest.get(k)
        if not cur or (pl.docstatus, pl.name) > (cur.docstatus, cur.name):
            newest[k] = pl
    ov["all_plans"] = ov["plans"]
    ov["plans"] = sorted(newest.values(), key=lambda r: (r.farm or "", r.variety or ""))
    blocks = api.block_coverage(farm=farm)
    plantings = api.on_ground(farm=farm, variety=variety)
    protocols = api.protocol_comparison(variety=variety)
    months = api.monthly_series(farm=farm, variety=variety)
    y, w = current_week()

    totals = {
        "demand_stems": sum(d.total_demand_stems or 0 for d in ov["demands"]),
        "firm_stems": sum(d.firm_demand_stems or 0 for d in ov["demands"]),
        "production_stems": sum(p.total_production_stems or 0 for p in ov["plans"]),
        "new_beds": sum(p.new_beds_required or 0 for p in ov["plans"]),
        "tc_cost": sum(flt(b.total_cost) for b in ov["motherstock"]),
        "mother_plants": sum(b.mother_plants or 0 for b in ov["motherstock"]),
        "plantings": len(plantings),
        "standing_plants": sum(p.plants or 0 for p in plantings),
        "area_ha": sum(flt(p.gross_area_ha) for p in plantings),
    }
    dem = totals["demand_stems"]
    totals["coverage_pct"] = (totals["production_stems"] / dem * 100) if dem else 0

    out = {
        "version": live.version(),
        "generated": frappe.utils.now(),
        "today": ov["today"],
        "week": {"year": y, "week": w, "monday": str(iso_monday(y, w))},
        "filters": {"farm": farm, "variety": variety},
        "demands": ov["demands"],
        "plans": ov["plans"],
        "motherstock": ov["motherstock"],
        "blocks": blocks,
        "plantings": plantings,
        "protocols": protocols,
        "months": months,
        "totals": totals,
        "exceptions": exceptions(ov, blocks, protocols, plantings),
        "actions": act_by(ov),
        "cached": False,
    }
    frappe.cache().set_value(key, out, expires_in_sec=CACHE_TTL)
    return out
