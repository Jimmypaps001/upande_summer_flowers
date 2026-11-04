# Copyright (c) 2026, James Kiruga and contributors
"""The route as the user enters it: ways in, then each way's timeline to planting."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from upande_summer_flowers.summer_flowers.crop_protocol import (
	BOUGHT_STAGES,
	ROUTE_END,
	ROUTE_ENTRY,
	STANDING_STAGES,
	route_chains,
)
from upande_summer_flowers.summer_flowers.crop_routes import ROUTES

WAYS = "custom_sf_ways"
WAY_STAGES = "custom_sf_way_stages"
ROUTE_FIELD = "custom_sf_material_route"
# Stages that raise bought or taken material to something plantable. On a
from upande_summer_flowers.summer_flowers import stages as _stages

RAISING = _stages.StageSet((_stages.PROPAGATION, _stages.CONDITIONING))

# A motherstock's timeline: the plantlet on a tray, then a pot until its first
STANDING_STEPS = (
	("Tray", "custom_sf_weeks_on_tray"),
	("Pot", "custom_sf_weeks_on_pot"),
	("Rooting", "custom_sf_sticking_to_planting_weeks"),
)
HARDENING = "custom_sf_hardening_weeks"
LIFTS = "custom_sf_lifts"
# Stages where somebody raises the material -- our own propagation unit, or an
PROPAGATING = _stages.StageSet((_stages.MOTHERSTOCK, _stages.PROPAGATION))
class _LiftForms:
	"""{stage: lift form}, read from the Material Stage records."""

	def get(self, stage, default=None):
		return _stages.lift_forms().get(stage, default)

	def __contains__(self, stage):
		return stage in _stages.lift_forms()


LIFT_FORM = _LiftForms()
ROOTING = "custom_sf_sticking_to_planting_weeks"

CARRY = ("weeks", "loss_pct", "yields_per_unit", "returns_per_plant", "is_standing",
         "yields_per_week", "max_cycles", "item", "rate", "notes", "is_purchase",
         "lead_weeks", "share_pct")


def has_ways(doc):
	return bool(doc.get(WAYS))


def _route_for(stages):
	"""The Material Route with exactly these stages, made if there is none."""
	from upande_summer_flowers.summer_flowers.crop_routes import SEED_ROUTES

	names = [n for n in SEED_ROUTES if n in ROUTES] + [n for n in ROUTES if n not in SEED_ROUTES]
	for name in names:
		route_stages = ROUTES[name]
		if list(route_stages) == list(stages):
			return name
	name = " → ".join(stages)
	if not frappe.db.exists("Material Route", name):
		frappe.get_doc({"doctype": "Material Route", "route_name": name,
		                "stages": [{"stage": s} for s in stages]}).insert(ignore_permissions=True)
		frappe.local._sf_routes = None
	return name


def ways_from_route(doc):
	"""Fill the ways from the route a protocol already has; the timeline follows."""
	doc.set(WAYS, [])
	doc.set(WAY_STAGES, [])
	for chain in route_chains(doc):
		stages = [st.stage for st, _steps in chain]
		entry = chain[0][0]
		doc.append(WAYS, {
			"route": _route_for(stages),
			"share_pct": flt(entry.get("share_pct")),
			"lead_weeks": cint(entry.get("lead_weeks")),
			"plants_per_unit": flt(entry.get("returns_per_plant")) or 1.0,
			"rate": flt(entry.get("rate")),
		})


def check_ways(doc):
	ways = doc.get(WAYS) or []
	seen = set()
	for w in ways:
		if w.route in seen:
			frappe.throw(_("{0} is listed twice. One route is one line with one share.")
			             .format(frappe.bold(w.route)), title=_("The same route twice"))
		seen.add(w.route)
		if w.route not in ROUTES:
			frappe.throw(_("{0} is not a Material Route.").format(w.route))
	if len(ways) == 1:
		# One way in carries the whole order, whatever was typed.
		ways[0].share_pct = 100
	if len(ways) > 1:
		total = sum(flt(w.share_pct) for w in ways)
		if abs(total - 100) > 0.01:
			frappe.throw(
				_("The material comes in {0} ways, so their shares have to add up to "
				  "100%. They come to {1}%.").format(len(ways), round(total, 2)),
				title=_("The shares do not add up"))


def sync_route_from_ways(doc):
	"""Write custom_sf_material_route from the ways and their stage weeks."""
	check_ways(doc)
	stage_rows = doc.get(WAY_STAGES) or []
	for r in stage_rows:
		if r.get("field") and doc.meta.has_field(r.field):
			doc.set(r.field, cint(r.weeks))
	typed = {(r.route, r.stage): cint(r.weeks) for r in stage_rows
	         if r.get("kind") in ("Arrival", "Stage")}

	# Whatever the route rows already hold that the ways do not say (losses,
	old = {chain[0][0].stage: chain for chain in route_chains(doc)}

	rows = []
	for way in doc.get(WAYS) or []:
		stages = ROUTES[way.route]
		prev = old.get(stages[0]) or []
		prev_by_stage = {st.stage: (st, steps) for st, steps in prev}
		for n, stage in enumerate(stages):
			st, steps = prev_by_stage.get(stage, (None, []))
			row = {k: st.get(k) for k in CARRY} if st else {"yields_per_unit": 1}
			row.update(row_type="Stage", stage=stage)
			if n == 0:
				ppu = flt(way.plants_per_unit)
				# A lead time and a price belong to what is bought. Material the farm
				bought = stage in BOUGHT_STAGES
				way.bought = 1 if bought else 0
				if not bought:
					way.lead_weeks, way.rate = 0, 0
				else:
					# Costed at the bought item's valuation (the variety, or its Roots
					from upande_summer_flowers.summer_flowers.valuation import (
						bought_item,
						item_cost,
					)

					way.rate = item_cost(bought_item(doc.get("variety"), stage),
					                     doc.get("custom_sf_company"))
				row.update(share_pct=flt(way.share_pct), lead_weeks=cint(way.lead_weeks),
				           rate=flt(way.rate),
				           returns_per_plant=ppu if ppu and ppu != 1 else 0)
			else:
				row.update(share_pct=0, lead_weeks=0)
			if stage != ROUTE_END and (way.route, stage) in typed:
				row["weeks"] = typed[(way.route, stage)]
			rows.append(row)
			for s in steps:
				rows.append({k: s.get(k) for k in CARRY + ("row_type", "step", "stage")})

	doc.set(ROUTE_FIELD, [])
	for r in rows:
		doc.append(ROUTE_FIELD, r)
	doc.flags.sf_typed_weeks = True
	sync_lifts(doc)

	# The protocol's single supplier lead is the first bought way's, which is what
	first_bought = next((w for w in doc.get(WAYS) if w.bought), None)
	if first_bought and cint(first_bought.lead_weeks):
		doc.custom_sf_supplier_lead_weeks = cint(first_bought.lead_weeks)

	# One number used to say how long a cutting or a bought plantlet takes to root.
	all_stages = [s for w in doc.get(WAYS) for s in ROUTES[w.route]]
	if any(s in STANDING_STAGES for s in all_stages):
		doc.set(HARDENING, cint(doc.get(ROOTING)))
	else:
		for w in doc.get(WAYS):
			stages = ROUTES[w.route]
			# Rooting is what the single figure means; sprouting or cooling only
			raising = next((s for s in stages if _stages.kind(s) == _stages.PROPAGATION
			                and not _stages.info(s).get("multiplies")), None) or next(
				(s for s in stages if s in RAISING), None)
			if raising and typed.get((w.route, raising)):
				doc.custom_sf_sticking_to_planting_weeks = typed[(w.route, raising)]
				break


def propagates(route):
	return any(s in PROPAGATING for s in (ROUTES.get(route) or []))


def sync_lifts(doc):
	"""Tie each lift to its way and give it the form planning knows it by."""
	by_form = {}
	for w in doc.get(WAYS) or []:
		form = LIFT_FORM.get((ROUTES.get(w.route) or [None])[0])
		if form:
			by_form.setdefault(form, w.route)
	listed = {w.route for w in doc.get(WAYS) or []}
	kept = []
	for lift in doc.get(LIFTS) or []:
		if not lift.get("route") and lift.get("form"):
			lift.route = by_form.get(lift.form)
		form = LIFT_FORM.get((ROUTES.get(lift.get("route")) or [None])[0])
		if not form or lift.route not in listed:
			continue
		lift.form = form
		kept.append(lift)
	for i, lift in enumerate(kept, start=1):
		lift.idx = i
	doc.set(LIFTS, kept)


def timeline(route, weeks_of, bought=None):
	"""One way's rows from arrival to planting, with the running week."""
	stages = ROUTES.get(route) or []
	if not stages:
		return []
	entry = stages[0]
	bought = (entry in BOUGHT_STAGES) if bought is None else bought
	rows = [{"kind": "Arrival", "stage": entry,
	         "activity": (_("{0} taken") if not bought else _("{0} arrive") if entry.endswith("s")
	                      else _("{0} arrives")).format(entry),
	         "weeks": weeks_of("Arrival", entry, None)}]
	for stage in stages[1:]:
		if stage == ROUTE_END:
			break
		if stage in STANDING_STAGES:
			rows.extend({"kind": "Step", "stage": stage, "step": step, "field": field,
			             "activity": step, "weeks": weeks_of("Step", stage, field)}
			            for step, field in STANDING_STEPS)
		else:
			rows.append({"kind": "Stage", "stage": stage, "activity": stage,
			             "weeks": weeks_of("Stage", stage, None)})
	rows.append({"kind": "Planting", "stage": ROUTE_END, "activity": _("Planting"),
	             "weeks": 0, "worked_out": 1})
	week = 0
	for r in rows:
		r["route"] = route
		week += cint(r["weeks"])
		r["week"] = week
	return rows


def _key(r):
	return (r.get("route"), r.get("kind"), r.get("stage"), r.get("step") or "")


def fill_way_stages(doc):
	"""Rebuild every way's timeline from the route as worked out; notes are kept."""
	if not has_ways(doc):
		return
	notes = {_key(r): r.get("notes") for r in (doc.get(WAY_STAGES) or []) if r.get("notes")}
	chains = route_chains(doc)
	out = []
	for way, chain in zip(doc.get(WAYS), chains):
		weeks = {st.stage: cint(st.get("weeks")) for st, _steps in chain}

		def weeks_of(kind, stage, field):
			return cint(doc.get(field)) if field else weeks.get(stage, 0)

		for r in timeline(way.route, weeks_of, bought=cint(way.bought) or None):
			r["notes"] = notes.get(_key(r))
			out.append(r)
	doc.set(WAY_STAGES, [])
	for r in out:
		doc.append(WAY_STAGES, r)


@frappe.whitelist()
def route_stages(route):
	"""A route's timeline rows, for the form to lay out before a save."""
	return timeline(route, lambda kind, stage, field: 0)


@frappe.whitelist()
def route_info(route):
	"""What the form needs to draw one way: its timeline, and whether it buys and"""
	stages = ROUTES.get(route) or []
	entry = stages[0] if stages else None
	return {"rows": route_stages(route), "bought": 1 if entry in BOUGHT_STAGES else 0,
	        "lifts": 1 if entry in LIFT_FORM else 0,
	        # What a consignment down this route hands over. The card shows it
	        "lift_form": LIFT_FORM.get(entry)}


def route_loss(protocol_or_version, lift_form):
	"""What a protocol loses between a consignment of `lift_form` (Tissue"""
	doc = _loss_doc(protocol_or_version)
	if not doc:
		return None
	for chain in route_chains(doc):
		entry = chain[0][0].stage
		if _stages.lift_form(entry) != lift_form:
			continue
		# Losses compound down the chain, and a step inside a stage loses on its
		surviving = 1.0
		for stage, steps in chain:
			surviving *= 1.0 - flt(stage.get("loss_pct")) / 100.0
			for step in steps:
				surviving *= 1.0 - flt(step.get("loss_pct")) / 100.0
		return round((1.0 - surviving) * 100.0, 4)
	return None


def _loss_doc(protocol_or_version):
	"""The document whose route to read: the version if that is what was named,"""
	name = protocol_or_version
	if not name:
		return None
	if frappe.db.exists("Crop Protocol Version", name):
		return frappe.get_cached_doc("Crop Protocol Version", name)
	if frappe.db.exists("Crop Protocol", name):
		return frappe.get_cached_doc("Crop Protocol", name)
	return None
