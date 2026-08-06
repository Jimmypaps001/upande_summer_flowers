# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Provenance for every derived number on the planning dashboard.

Each metric declares where it comes from: the formula, the inputs with the field
and record they were read from, the arithmetic, and any caveat that changes how it
should be read. Values are pulled live, so an explanation can never drift from the
figure it explains.

The point is that a planner can challenge a number without reading the code.
"""

import frappe
from frappe import _
from frappe.utils import flt

WEEKS_PER_YEAR = 52


def _link(doctype, name):
	return {"label": name, "doctype": doctype,
	        "route": f"/app/{frappe.scrub(doctype).replace('_', '-')}/{name}"}


def _step(label, value, source=None, unit=None):
	return {"label": label, "value": value, "source": source, "unit": unit}


# ---------------------------------------------------------------------------
# Protocol metrics
# ---------------------------------------------------------------------------

def _protocol(version):
	return frappe.get_cached_doc("Crop Protocol Version", version)


def _flush_source(v):
	return _("{0} → flush schedule, {1} rows").format(v.name, len(v.flush_schedule))


PROTOCOL_METRICS = {}


def protocol_metric(key):
	def deco(fn):
		PROTOCOL_METRICS[key] = fn
		return fn
	return deco


@protocol_metric("total_stems_per_plant_life")
def _m_spl(v):
	rows = sorted(v.flush_schedule, key=lambda r: r.flush_number or 0)
	return {
		"label": _("Stems per plant over its life"),
		"value": flt(v.total_stems_per_plant_life), "unit": _("stems/plant"),
		"formula": _("sum of stems per plant across every flush"),
		"steps": [
			_step(_("Flush {0}").format(r.flush_number), flt(r.stems_per_plant),
			      _("weeks {0} from pinch").format(r.weeks_from_pinch))
			for r in rows
		] + [_step(_("Total of {0} flushes").format(len(rows)),
		           flt(v.total_stems_per_plant_life))],
		"caveats": [_("Edit any flush row on the Protocol tab and this moves with it.")],
	}


@protocol_metric("plants_per_ha")
def _m_pph(v):
	return {
		"label": _("Plants per hectare"),
		"value": flt(v.plants_per_sqm_net) * 10_000, "unit": _("plants/ha"),
		"formula": _("plants per m² of bed × 10,000"),
		"steps": [
			_step(_("Plants per m² of bed (net)"), v.plants_per_sqm_net,
			      _("{0} → plants_per_sqm_net, the agronomic density").format(v.name)),
			_step(_("× 10,000 m² per hectare"), flt(v.plants_per_sqm_net) * 10_000),
		],
		"caveats": [
			_("A hectare of BED, not of block. A second, larger area and the ratio between them "
			  "ratio are no longer carried, so there is one area and it is stated."),
		],
	}


@protocol_metric("stems_per_ha_life")
def _m_sphl(v):
	pph = flt(v.plants_per_sqm_net) * 10_000
	return {
		"label": _("Life stems per hectare"),
		"value": flt(v.stems_per_ha_life), "unit": _("stems/ha"),
		"formula": _("stems per plant life × plants per ha"),
		"steps": [
			_step(_("Stems per plant life"), flt(v.total_stems_per_plant_life),
			      _flush_source(v)),
			_step(_("Plants per ha of bed"), pph,
			      _("{0}/m² of bed × 10,000").format(flt(v.plants_per_sqm_net))),
			_step(_("Product"), flt(v.stems_per_ha_life)),
		],
		"caveats": [_("Over the full {0}-week life, not per year.")
		            .format(v.total_weeks_in_ground)],
	}


@protocol_metric("stems_per_ha_year")
def _m_sphy(v):
	years = flt(v.life_expectancy_years)
	return {
		"label": _("Yield per hectare per year"),
		"value": flt(v.stems_per_ha_year), "unit": _("stems/ha/yr"),
		"formula": _("life stems per ha ÷ life in years"),
		"steps": [
			_step(_("Life stems per ha"), flt(v.stems_per_ha_life)),
			_step(_("Weeks in ground"), v.total_weeks_in_ground,
			      _("pinch {0} + last flush {1} weeks from pinch").format(
				      v.weeks_to_pinch,
				      max((r.weeks_from_pinch or 0) for r in v.flush_schedule)
				      if v.flush_schedule else 0)),
			_step(_("Life in years"), years, _("{0} ÷ 52").format(v.total_weeks_in_ground)),
			_step(_("Computed yield"), flt(v.stems_per_ha_year)),
			_step(_("Stated yield on the protocol"), flt(v.stated_yield_stems_per_ha),
			      _("entered by the agronomist")),
			_step(_("Variance"), flt(v.yield_variance_pct), _("computed vs stated"), "%"),
		],
		"caveats": [
			_("The computed and stated yields disagree by {0}%. Both are kept: either "
			  "the stated figure carries a wastage assumption the flush curve does not, "
			  "or one of them needs revising.").format(flt(v.yield_variance_pct, 1)),
		] if abs(flt(v.yield_variance_pct)) > 1 else [],
	}


@protocol_metric("harvest_weeks_per_year")
def _m_hwy(v):
	fam = v.harvest_week_family(1) if v.flush_interval_weeks else []
	return {
		"label": _("Harvest weeks per year"),
		"value": v.harvest_weeks_per_year, "unit": _("weeks"),
		"formula": _("52 ÷ flush interval"),
		"steps": [
			_step(_("Flush interval"), v.flush_interval_weeks, _("weeks between flushes")),
			_step(_("52 ÷ {0}").format(v.flush_interval_weeks), v.harvest_weeks_per_year),
		],
		"caveats": [
			_("Because the interval divides the year, a planting returns to the same "
			  "few weeks every year until it is uprooted — {0} of them. That is why a "
			  "block is idle most weeks and why you cannot fine-tune a single week by "
			  "planting date alone.").format(v.harvest_weeks_per_year),
			_("Planted in week 1 it would harvest in weeks {0}.").format(
				", ".join(str(w) for w in fam)) if fam else "",
		],
	}


@protocol_metric("first_harvest_offset_weeks")
def _m_fho(v):
	first = min((r.weeks_from_pinch or 0) for r in v.flush_schedule) \
		if v.flush_schedule else 0
	return {
		"label": _("Weeks from planting to first harvest"),
		"value": v.first_harvest_offset_weeks, "unit": _("weeks"),
		"formula": _("weeks to pinch + first flush from pinch"),
		"steps": [
			_step(_("Weeks to pinch"), v.weeks_to_pinch),
			_step(_("First flush, weeks from pinch"), first),
			_step(_("Total"), v.first_harvest_offset_weeks),
		],
		"caveats": [],
	}


@protocol_metric("ms_establishment_weeks")
def _m_mse(v):
	return {
		"label": _("Motherstock establishment"),
		"value": v.ms_establishment_weeks, "unit": _("weeks"),
		"formula": _("tray + pot + ramp"),
		"steps": [
			_step(_("Weeks on tray"), v.weeks_on_tray),
			_step(_("Weeks on pot"), v.weeks_on_pot),
			_step(_("Ramp to full capacity"), v.ramp_weeks),
			_step(_("Total"), v.ms_establishment_weeks),
		],
		"caveats": [
			_("Hardening ({0}w) is deliberately NOT in here. A cutting bound for the "
			  "field needs hardening; a cutting becoming a mother does not. Hardening "
			  "belongs to the cutting-to-harvest path instead.").format(v.hardening_weeks),
		],
	}


@protocol_metric("cutting_to_harvest_weeks")
def _m_c2h(v):
	return {
		"label": _("Cutting to harvest"),
		"value": v.cutting_to_harvest_weeks, "unit": _("weeks"),
		"formula": _("hardening + weeks to pinch + flush interval"),
		"steps": [
			_step(_("Hardening"), v.hardening_weeks, _("cutting to plantable")),
			_step(_("Weeks to pinch"), v.weeks_to_pinch, _("after planting")),
			_step(_("First flush after pinch"), v.flush_interval_weeks),
			_step(_("Total"), v.cutting_to_harvest_weeks),
		],
		"caveats": [],
	}


@protocol_metric("total_renewal_lead_weeks")
def _m_trl(v):
	total = (v.supplier_lead_weeks or 0) + (v.ms_establishment_weeks or 0)
	return {
		"label": _("Total renewal lead"),
		"value": total, "unit": _("weeks"),
		"formula": _("supplier lead + motherstock establishment"),
		"steps": [
			_step(_("Supplier lead"), v.supplier_lead_weeks, _("TC order to delivery")),
			_step(_("Establishment"), v.ms_establishment_weeks, _("tray + pot + ramp")),
			_step(_("Total"), total),
		],
		"caveats": [
			_("This is the order deadline: the next TC order must be placed {0} weeks "
			  "before the current motherstock expires, or the replacement is not "
			  "productive in time.").format(total),
		],
	}


@protocol_metric("cuttings_per_plant_required")
def _m_cpr(v):
	rooting = flt(v.rooting_success_pct or 100) / 100
	field = flt(v.field_establishment_pct or 100) / 100
	return {
		"label": _("Cuttings per standing plant"),
		"value": flt(v.cuttings_per_plant_required), "unit": _("cuttings"),
		"formula": _("1 ÷ (rooting success × field establishment)"),
		"steps": [
			_step(_("Rooting success"), v.rooting_success_pct, None, "%"),
			_step(_("Field establishment"), v.field_establishment_pct, None, "%"),
			_step(_("Combined survival"), round(rooting * field, 4)),
			_step(_("1 ÷ survival"), flt(v.cuttings_per_plant_required)),
		],
		"caveats": [
			_("A separate cutting reject rate of {0}% is applied on top when a plan "
			  "converts plants into cuttings, so the full uplift is higher than this.")
			.format(flt(v.cutting_reject_pct)),
		] if v.cutting_reject_pct else [],
	}


@protocol_metric("plants_per_sqm_bench")
def _m_ppb(v):
	return {
		"label": _("Plants per m² of bench"),
		"value": flt(v.plants_per_sqm_bench), "unit": _("plants/m²"),
		"formula": _("pots per m² × plants per pot"),
		"steps": [
			_step(_("Pots per m²"), v.pots_per_sqm),
			_step(_("Plants per pot"), v.plants_per_pot),
			_step(_("Product"), flt(v.plants_per_sqm_bench)),
		],
		"caveats": [_("This converts a motherstock plant count into bench area.")],
	}


@protocol_metric("plants_per_block")
def _m_ppblk(v):
	return {
		"label": _("Plants per block"),
		"value": v.plants_per_block, "unit": _("plants"),
		"formula": _("beds per block × plants per bed"),
		"steps": [
			_step(_("Beds per block"), v.beds_per_block),
			_step(_("Plants per bed"), v.plants_per_bed),
			_step(_("Product"), v.plants_per_block),
		],
		"caveats": [],
	}


@protocol_metric("sqm_net_per_bed")
def _m_snpb(v):
	return {
		"label": _("m² of bed per bed"),
		"value": flt(v.sqm_net_per_bed), "unit": "m²",
		"formula": _("plants per bed ÷ plants per m² of bed"),
		"steps": [
			_step(_("Plants per bed"), v.plants_per_bed),
			_step(_("Plants per m² of bed (net)"), v.plants_per_sqm_net),
			_step(_("m² per bed"), flt(v.sqm_net_per_bed)),
		],
		"caveats": [],
	}


@protocol_metric("life_expectancy_years")
def _m_life(v):
	return {
		"label": _("Life expectancy"),
		"value": flt(v.life_expectancy_years), "unit": _("years"),
		"formula": _("weeks in ground ÷ 52"),
		"steps": [
			_step(_("Weeks to pinch"), v.weeks_to_pinch),
			_step(_("Last flush, weeks from pinch"),
			      max((r.weeks_from_pinch or 0) for r in v.flush_schedule)
			      if v.flush_schedule else 0),
			_step(_("Weeks in ground"), v.total_weeks_in_ground),
			_step(_("÷ 52"), flt(v.life_expectancy_years)),
		],
		"caveats": [],
	}


@protocol_metric("order_to_first_harvest_weeks")
def _m_o2h(v):
	lead = v.supplier_lead_weeks or 0
	estab = v.ms_establishment_weeks or 0
	total = lead + estab + (v.cutting_to_harvest_weeks or 0)
	return {
		"label": _("TC order to first farm harvest"),
		"value": total, "unit": _("weeks"),
		"formula": _("supplier lead + establishment + cutting to harvest"),
		"steps": [
			_step(_("Place TC order"), 0),
			_step(_("TC arrives"), lead, _("{0}w supplier lead").format(lead)),
			_step(_("Motherstock productive"), lead + estab,
			      _("{0}w establishment").format(estab)),
			_step(_("First harvest"), total,
			      _("{0}w cutting to harvest").format(v.cutting_to_harvest_weeks)),
		],
		"caveats": [
			_("Nothing can be harvested inside {0} weeks of placing a TC order. This is "
			  "the single longest lead time in the plan.").format(total),
		],
	}


# ---------------------------------------------------------------------------
# Plan metrics
# ---------------------------------------------------------------------------

PLAN_METRICS = {}


def plan_metric(key):
	def deco(fn):
		PLAN_METRICS[key] = fn
		return fn
	return deco


@plan_metric("coverage_pct")
def _p_cov(p, v):
	return {
		"label": _("Demand coverage"),
		"value": flt(p.coverage_pct), "unit": "%",
		"formula": _("planned production ÷ demand × 100"),
		"steps": [
			_step(_("Planned production"), p.total_production_stems,
			      _("sum of {0} weekly rows").format(len(p.plan_weeks))),
			_step(_("Demand"), p.total_demand_stems,
			      _("from {0}").format(p.market_demand)),
			_step(_("Coverage"), flt(p.coverage_pct), None, "%"),
		],
		"caveats": [
			_("Coverage over the whole horizon can exceed 100% while individual weeks "
			  "are still short — {0} weeks are in deficit here. Whole-block flushing "
			  "means surplus in one week cannot fill a gap in another.")
			.format(p.weeks_in_deficit),
		] if p.weeks_in_deficit else [],
		"links": [_link("Summer Flower Market Demand", p.market_demand)],
	}


@plan_metric("peak_weekly_sticking")
def _p_peak(p, v):
	rows = [b for b in p.plan_blocks if b.is_new_planting and b.sticking_week]
	from collections import defaultdict
	agg = defaultdict(int)
	for b in rows:
		agg[(b.sticking_year, b.sticking_week)] += b.plants or 0
	top = sorted(agg.items(), key=lambda kv: -kv[1])[:5]
	return {
		"label": _("Peak weekly sticking"),
		"value": p.peak_weekly_sticking, "unit": _("plants"),
		"formula": _("largest single sticking week across all proposed plantings"),
		"steps": [
			_step(_("{0}-W{1:02d}").format(k[0], k[1]), qty)
			for k, qty in top
		] + [_step(_("Peak"), p.peak_weekly_sticking,
		           _("week {0}").format(p.peak_sticking_week))],
		"caveats": [
			_("This, not the annual total, sizes the motherstock. Cuttings cannot be "
			  "banked, so a mother must exist for every cutting stuck in the peak week. "
			  "Spreading a planting over more weeks divides the motherstock by the same "
			  "factor."),
		],
	}


@plan_metric("plants_required")
def _p_plants(p, v):
	rows = [b for b in p.plan_blocks if b.is_new_planting]
	return {
		"label": _("Plants required"),
		"value": sum((b.plants or 0) for b in rows), "unit": _("plants"),
		"formula": _("proposed plantings × plants per bed"),
		"steps": [
			_step(_("Plantings proposed"), len(rows),
			      _("one per week that was short of demand")),
			_step(_("Beds"), p.new_beds_required),
			_step(_("Plants per bed"), v.plants_per_bed, _("from {0}").format(v.name)),
			_step(_("Total plants"), p.new_plants_required),
			_step(_("Cuttings to stick"), v.cuttings_for_plants(p.new_plants_required or 0),
			      _("× {0} losses, then {1}% reject").format(
				      flt(v.cuttings_per_plant_required, 3), flt(v.cutting_reject_pct))),
		],
		"caveats": [],
	}


@plan_metric("weeks_in_deficit")
def _p_def(p, v):
	short = [w for w in p.plan_weeks
	         if (w.production_stems or 0) < (w.demand_stems or 0)]
	worst = sorted(short, key=lambda w: (w.production_stems or 0) - (w.demand_stems or 0))[:5]
	return {
		"label": _("Weeks in deficit"),
		"value": len(short), "unit": _("weeks"),
		"formula": _("weeks where production is below demand"),
		"steps": [
			_step(_("{0}-W{1:02d}").format(w.year, w.week_no),
			      (w.production_stems or 0) - (w.demand_stems or 0),
			      _("{0} produced vs {1} demanded").format(
				      w.production_stems or 0, w.demand_stems or 0))
			for w in worst
		] or [_step(_("No week is short"), 0)],
		"caveats": [
			_("Of {0} weeks in the horizon.").format(len(p.plan_weeks)),
		],
	}


# ---------------------------------------------------------------------------

@frappe.whitelist()
def explain(metric, version=None, plan=None, planting=None):
	"""Why a given number is what it is."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)

	out = None
	if metric in PROTOCOL_METRICS:
		if not version:
			rows = frappe.get_all("Crop Protocol Version",
			                      filters={"version_status": "Active"},
			                      pluck="name", limit=1)
			version = rows[0] if rows else None
		if not version:
			frappe.throw(_("No protocol version to explain against."))
		v = _protocol(version)
		out = PROTOCOL_METRICS[metric](v)
		out.setdefault("links", []).append(_link("Crop Protocol Version", v.name))
		out["scope"] = _("{0} at {1}, version {2} ({3})").format(
			v.variety, v.farm, v.version, v.version_status)

	elif metric in PLAN_METRICS:
		if not plan:
			rows = frappe.get_all("Summer Flower Production Plan",
			                      filters={"docstatus": ["<", 2]}, pluck="name",
			                      order_by="creation desc", limit=1)
			plan = rows[0] if rows else None
		if not plan:
			frappe.throw(_("No production plan to explain against."))
		p = frappe.get_doc("Summer Flower Production Plan", plan)
		v = _protocol(p.protocol)
		out = PLAN_METRICS[metric](p, v)
		out.setdefault("links", []).extend(
			[_link("Summer Flower Production Plan", p.name),
			 _link("Crop Protocol Version", v.name)])
		out["scope"] = _("{0} at {1}, plan {2}").format(p.variety, p.farm, p.name)

	if out is None:
		frappe.throw(_("No explanation is registered for {0} yet.").format(metric))

	out["metric"] = metric
	out.setdefault("caveats", [])
	out["caveats"] = [c for c in out["caveats"] if c]
	return out


@frappe.whitelist()
def explainable():
	"""Metrics that have an explanation registered."""
	return {"protocol": sorted(PROTOCOL_METRICS), "plan": sorted(PLAN_METRICS)}
