# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Move the per-stage figures out of the protocol's columns and onto the route.

The protocol carries about fifteen scalars that describe how long each part of the
journey takes and what is lost along it -- weeks on tray, hardening weeks, rooting
success, supplier lead. Every one of them describes ONE route: the TC to motherstock
one that Aster takes. Matricaria is never stuck, Dahlia never sees a pot, and a crop
bought as plants has no propagation at all, so on those crops the same columns are
either empty or quietly wrong.

The route table already says the same things generically, per stage. This writes the
scalars onto it as steps, so that afterwards the rows are a complete record of the
journey and the columns are a summary of the rows rather than the other way round.

Nothing is invented and nothing is overwritten: a protocol that already carries steps
is left alone, and a step is only written when the scalar behind it is non-zero. Run
it twice and the second run does nothing.

    bench --site SITE execute \
        upande_summer_flowers.summer_flowers.route_backfill.report
    bench --site SITE execute \
        upande_summer_flowers.summer_flowers.route_backfill.report \
        --kwargs "{'dry_run': 0}"
"""

import frappe
from frappe.utils import cint, flt

from upande_summer_flowers.summer_flowers.crop_protocol import (
	PROTOCOL_DOCTYPE,
	ROUTE_END,
	block_weeks,
	is_step,
	route_blocks,
)


def _loss_from_success(pct):
	"""A success percentage is a loss percentage read from the other end."""
	pct = flt(pct)
	return round(100.0 - pct, 4) if pct else 0.0


def plan_for(doc):
	"""What this protocol's scalars say, expressed as route rows.

	Steps under a stage are the wait and the losses incurred turning THAT stage's
	material into the next stage's. So the raising steps sit under the stage the
	material is bought at, and the finishing ones under the last stage before the
	plant goes in the ground.
	"""
	blocks = route_blocks(doc)
	if not blocks:
		return None
	stages = [b[0] for b in blocks]
	names = [s.stage for s in stages]
	entry = stages[0]
	pre_plants = stages[-2] if len(stages) > 1 and names[-1] == ROUTE_END else stages[-1]

	g = lambda f: cint(doc.get("custom_sf_" + f))
	steps = {}   # stage row name -> [step dicts, in order]

	if "Motherstock" in names:
		steps.setdefault(entry.name, []).extend([
			{"step": "Tray", "weeks": g("weeks_on_tray")},
			{"step": "Hardening", "weeks": g("hardening_weeks")},
			{"step": "Pot", "weeks": g("weeks_on_pot")},
		])
	else:
		# Every other route raises its material in one go, and the protocol only
		# has one column for it.
		steps.setdefault(entry.name, []).append(
			{"step": "Growing on",
			 "weeks": cint(doc.get("weeks_in_propagation")) or g("weeks_on_tray")})

	# The build-up to full capacity. The mother exists but is not yet yielding what
	# it will, and that wait is what "establishment" has always silently included --
	# which is why the column read four weeks longer than the transit steps did.
	multiplier = next((s for s in stages if s.stage in ("Motherstock", "Sprouting")), None)
	if multiplier is not None and (g("ramp_weeks") or g("weeks_to_max_pc")):
		steps.setdefault(multiplier.name, []).append(
			{"step": "Bulking", "weeks": g("ramp_weeks") or g("weeks_to_max_pc")})

	steps.setdefault(pre_plants.name, []).extend([
		{"step": "Sticking",
		 "weeks": g("sticking_to_planting_weeks"),
		 "loss_pct": _loss_from_success(doc.get("custom_sf_rooting_success_pct"))},
		{"step": "Field establishment",
		 "weeks": 0,
		 "loss_pct": _loss_from_success(doc.get("custom_sf_field_establishment_pct"))},
	])

	# Figures that belong to a stage itself rather than to a wait inside it.
	on_stage = {}
	if cint(doc.get("custom_sf_supplier_lead_weeks")):
		on_stage.setdefault(entry.name, {})["lead_weeks"] = g("supplier_lead_weeks")
	if flt(doc.get("custom_sf_tc_order_loss_pct")) and not flt(entry.loss_pct):
		on_stage.setdefault(entry.name, {})["loss_pct"] = flt(
			doc.get("custom_sf_tc_order_loss_pct"))
	for s in stages:
		if s.stage in ("Motherstock", "Sprouting") and g("max_multiplication_cycles"):
			on_stage.setdefault(s.name, {})["max_cycles"] = g("max_multiplication_cycles")

	# A step with nothing to say is not written. An empty row is a row someone has
	# to read and dismiss every time they open the protocol.
	steps = {k: [x for x in v if cint(x.get("weeks")) or flt(x.get("loss_pct"))]
	         for k, v in steps.items()}
	return {"steps": {k: v for k, v in steps.items() if v}, "on_stage": on_stage}


def apply_to(doc):
	"""Rebuild the route with the steps interleaved. Returns rows written."""
	plan = plan_for(doc)
	if not plan:
		return 0
	rebuilt, written = [], 0
	for stage, _existing in route_blocks(doc):
		row = {k: stage.get(k) for k in
		       ("row_type", "stage", "step", "weeks", "loss_pct", "is_purchase",
		        "yields_per_unit", "lead_weeks", "rate", "max_cycles", "item",
		        "returns_per_plant", "notes")}
		row["row_type"] = "Stage"
		row.update(plan["on_stage"].get(stage.name, {}))
		rebuilt.append(row)
		for step in plan["steps"].get(stage.name, []):
			rebuilt.append(dict(row_type="Step", stage=None, yields_per_unit=1,
			                    is_purchase=0, **step))
			written += 1
	if not written:
		return 0
	doc.set("custom_sf_material_route", [])
	for row in rebuilt:
		doc.append("custom_sf_material_route", row)
	return written


def compare(doc):
	"""What the columns say against what the rows now say, measuring the same thing.

	establishment_weeks is TC to a PRODUCTIVE mother -- the raising steps plus the
	build-up -- and not TC to a plant in the ground. Comparing it against the whole
	route was comparing two different journeys, which is the confusion the column
	name carries and the rows do not.
	"""
	blocks = route_blocks(doc)
	entry_steps = blocks[0][1] if blocks else []
	bulking = [st for _s, steps in blocks for st in steps if st.get("step") == "Bulking"]
	est_rows = sum(cint(x.weeks) for x in entry_steps) + sum(cint(x.weeks) for x in bulking)
	to_ground = [b for b in blocks if b[0].stage != ROUTE_END]
	return {
		"establishment_col": cint(doc.get("custom_sf_establishment_weeks")),
		"establishment_rows": est_rows,
		"to_ground_rows": sum(block_weeks(s, st) for s, st in to_ground),
		"steps": sum(1 for r in (doc.get("custom_sf_material_route") or []) if is_step(r)),
		"stages": len(blocks),
	}


@frappe.whitelist()
def run(dry_run=1, limit=None):
	dry_run = int(dry_run or 0)
	names = [p.name for p in frappe.get_all(PROTOCOL_DOCTYPE,
	                                        filters={"custom_is_summer_flower": 1},
	                                        fields=["name"], order_by="name",
	                                        limit_page_length=cint(limit) or 0)]
	done, already, nothing, no_route, failed = [], [], [], [], []
	frappe.flags.sf_protocol_move = True   # a move is not an edit; see on_update
	try:
		for name in names:
			doc = frappe.get_doc(PROTOCOL_DOCTYPE, name)
			blocks = route_blocks(doc)
			if not blocks:
				no_route.append(name)
				continue
			if any(is_step(r) for r in (doc.get("custom_sf_material_route") or [])):
				already.append(name)
				continue
			try:
				n = apply_to(doc)
				if not n:
					# Every scalar behind every step is zero, so there is nothing to
					# move. These are the protocols provisioned from the categories
					# document with their timings still to be typed.
					nothing.append(name)
					continue
				if not dry_run:
					doc.flags.ignore_permissions = True
					doc.flags.ignore_mandatory = True
					doc.save()
				done.append((name, n, compare(doc)))
			except Exception as e:
				failed.append((name, "%s: %s" % (type(e).__name__, str(e)[:120])))
	finally:
		frappe.flags.sf_protocol_move = False
	if not dry_run:
		frappe.db.commit()
	return {"dry_run": bool(dry_run), "written": done, "left_alone": already,
	        "nothing_to_move": nothing, "no_route": no_route, "failed": failed}


def report(dry_run=1, limit=None):
	r = run(dry_run=dry_run, limit=limit)
	print("DRY RUN -- nothing written\n" if r["dry_run"] else "WRITTEN\n")
	print("%-46s %5s %8s %8s %9s" % ("protocol", "steps", "col wks", "row wks", "to ground"))
	mismatched = 0
	for name, n, c in r["written"]:
		flag = "" if c["establishment_col"] == c["establishment_rows"] else "  <-- differs"
		mismatched += bool(flag)
		print("%-46s %5d %8d %8d %9d%s" % (
			name[:46], n, c["establishment_col"], c["establishment_rows"],
			c["to_ground_rows"], flag))
	print("\n%d protocols given steps, %d of them disagree with their own columns"
	      % (len(r["written"]), mismatched))
	print("%d already had steps, %d have nothing to move, %d carry no route, %d failed"
	      % (len(r["left_alone"]), len(r["nothing_to_move"]), len(r["no_route"]),
	         len(r["failed"])))
	for name, err in r["failed"][:20]:
		print("   FAILED %s -- %s" % (name, err))
	return r
