# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""What a production plan asks of the land, against what the farm has.

This used to be eleven fields and a block-by-block table written onto every
production plan on every save. Nothing read them: they were there to be looked
at, once, by somebody deciding whether to approve the plan. Stored, they were
eleven more numbers to keep true and a table that went stale the moment a block
changed; worked out on demand they are the same answer, always current, and the
plan is eleven fields shorter.

Two capacity columns, deliberately, because they disagree. The stated figure is
what each block claims through its area history; the measured one is the sum of
its beds' own length and width. Where most blocks have no beds pointing at them
the measured column reads low -- which is a statement about the bed register,
not about the land, and saying so is more use than averaging the two into one
number nobody can act on.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt


def analyse(plan):
	"""Returns the totals, the block-by-block rows and the verdict.

	`plan` is a Summer Flower Production Plan document or its name.
	"""
	from upande_summer_flowers.summer_flowers.doctype.planting_calendar \
		.planting_calendar import standing_plantings
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan \
		.summer_flower_production_plan import bed_sqm

	if isinstance(plan, str):
		plan = frappe.get_doc("Summer Flower Production Plan", plan)

	t = {"beds_required_peak": cint(plan.peak_concurrent_beds),
	     "space_required_ha": 0.0, "space_stated_ha": 0.0, "space_measured_ha": 0.0,
	     "beds_available_stated": 0, "beds_available_measured": 0,
	     "beds_unblocked": 0, "space_unblocked_ha": 0.0,
	     "space_utilisation_pct": 0.0}
	out = {"totals": t, "blocks": [], "notes": [], "verdict": None,
	       "ground_demanded": None}
	if not plan.farm:
		return out

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol) if plan.protocol else None
	sqm_per_bed = bed_sqm(v) if v else 0
	t["space_required_ha"] = (t["beds_required_peak"] * sqm_per_bed) / 10_000
	# No bed size, no land answer. Reporting hectares from a bed nobody has
	# measured is worse than reporting none, because a plan that says it fits
	# gets approved.
	bed_size_unknown = not sqm_per_bed

	plants_peak = sum(cint(r.plants) for r in plan.plan_blocks
	                  if r.get("is_new_planting"))
	parts = ["{0} beds at the peak".format(t["beds_required_peak"]),
	         "{0} plants".format("{:,}".format(plants_peak))]
	if sqm_per_bed:
		parts.append("{0} ha at {1} m2 a bed".format(
			round(t["space_required_ha"], 3), round(sqm_per_bed, 2)))
	else:
		parts.append(_("hectares unknown -- {0} has no measured bed").format(plan.farm))
	out["ground_demanded"] = " | ".join(parts)

	blocks = frappe.get_all(
		"Block", filters={"farm": plan.farm, "custom_is_summer_flower_block": 1},
		fields=["name", "custom_total_beds", "custom_net_area_ha",
		        "custom_measured_beds", "custom_measured_net_area_ha"],
		order_by="name asc")

	# What this plan itself asked of each block, and what someone else holds.
	mine = defaultdict(int)
	for row in plan.plan_blocks:
		if row.get("block") and row.get("is_new_planting"):
			mine[row.block] = max(mine[row.block], cint(row.beds))
	others = defaultdict(int)
	for pl in standing_plantings(plan.farm, plan.variety):
		others[pl.block] += cint(pl.beds)

	for b in blocks:
		stated_beds = cint(b.custom_total_beds)
		needed = mine.get(b.name, 0)
		held = others.get(b.name, 0)
		free = max(0, stated_beds - held)
		t["space_stated_ha"] += flt(b.custom_net_area_ha)
		t["space_measured_ha"] += flt(b.custom_measured_net_area_ha)
		t["beds_available_stated"] += stated_beds
		t["beds_available_measured"] += cint(b.custom_measured_beds)
		out["blocks"].append({
			"block": b.name,
			"beds_stated": stated_beds,
			"beds_measured": cint(b.custom_measured_beds),
			"area_stated_ha": flt(b.custom_net_area_ha),
			"area_measured_ha": flt(b.custom_measured_net_area_ha),
			"beds_needed_by_plan": needed,
			"beds_held_by_others": held,
			"beds_free": free,
			"verdict": (
				_("{0} beds to this plan").format(needed) if needed else
				_("full -- held by another planting") if held and not free else
				_("{0} beds free").format(free) if free else
				_("no beds stated")
			),
		})

	t["space_utilisation_pct"] = (
		t["space_required_ha"] / t["space_stated_ha"] * 100
		if t["space_stated_ha"] else 0)

	# Land at this farm that belongs to no block. A bed sits under a block or
	# directly under a greenhouse, never both, so this is the rest of the farm --
	# and it is where the answer to "we have no space" usually is. A farm with no
	# blocks at all and 14.4 ha of beds has a different problem from one with no
	# land, and needs a different thing done about it.
	unblocked = frappe.db.sql('''
		select count(*) n, ifnull(sum(b.bed_area), 0) sqm
		from tabBed b join tabWarehouse w on w.name = b.greenhouse
		where w.custom_farm = %s and ifnull(b.custom_block, '') = ''
	''', plan.farm, as_dict=True)[0]
	t["beds_unblocked"] = cint(unblocked.n)
	t["space_unblocked_ha"] = flt(unblocked.sqm) / 10_000

	notes = out["notes"]
	not_placed = cint(plan.plantings_not_placed)
	# Said first and on its own, because everything after it is arithmetic on a
	# bed size nobody has given. A plan that reports hectares it cannot know gets
	# approved on the strength of them.
	if bed_size_unknown:
		notes.append(_(
			"The land figures below cannot be trusted: {0} does not say how big a "
			"bed is, so this plan has no way to turn {1} plants into beds or "
			"hectares. Set the net area per bed on the protocol -- the farm's beds "
			"are 50 m2 -- and regenerate. Until then the plan may be far larger "
			"than the farm, and nothing here will say so."
		).format(plan.protocol, "{:,}".format(cint(plan.new_plants_required))))

	if not blocks and t["beds_unblocked"]:
		notes.append(_(
			"{0} has no summer flower blocks, so nothing can be placed there -- but "
			"it does have {1} beds ({2} ha) in its greenhouses belonging to no "
			"block. The land is there; draw blocks over it and this plan can be "
			"planted."
		).format(plan.farm, t["beds_unblocked"], round(t["space_unblocked_ha"], 3)))
	elif not blocks:
		notes.append(_(
			"{0} has no summer flower blocks and no beds in its greenhouses, so it "
			"has no space to plan into at all."
		).format(plan.farm))
	elif t["space_utilisation_pct"] > 100:
		notes.append(_(
			"This plan needs {0} ha at its peak and {1} has {2} ha, so it cannot fit "
			"however the blocks are arranged. {3} plantings are waiting on a block "
			"and some of them will not get one until the farm has more land."
		).format(round(t["space_required_ha"], 3), plan.farm,
		         round(t["space_stated_ha"], 3), not_placed))
	elif not_placed and not bed_size_unknown:
		notes.append(_(
			"There is enough land in total ({0} ha needed of {1} ha) but {2} "
			"plantings are still waiting on a block: a block is held for a whole "
			"planting's life, so the free beds are not free at the same time. "
			"Moving a planting a week or two either way often finds one."
		).format(round(t["space_required_ha"], 3),
		         round(t["space_stated_ha"], 3), not_placed))
	elif not_placed:
		# Same fact, without the claim about land. Saying the figures cannot be
		# trusted and then "there is enough land in total" in the next breath is
		# worse than either line alone.
		notes.append(_(
			"{0} plantings are waiting on a block. Whether the farm has room for "
			"them cannot be said until the bed size is set.").format(not_placed))
	# Its own note, not part of the chain above. Land the plan cannot reach is
	# worth saying, but never at the price of suppressing the reason plantings
	# failed -- that line is the one a planner acts on.
	if blocks and t["beds_unblocked"]:
		notes.append(_(
			"A further {0} beds ({1} ha) at this farm belong to no block, so the "
			"plan cannot reach them."
		).format(t["beds_unblocked"], round(t["space_unblocked_ha"], 3)))
	if t["beds_available_measured"] < t["beds_available_stated"]:
		notes.append(_(
			"The bed register accounts for {0} of the {1} beds these blocks claim. "
			"Planning uses the stated figure; until the beds are linked to their "
			"blocks the measured column is not a second opinion, it is a gap."
		).format(t["beds_available_measured"], t["beds_available_stated"]))
	if not sqm_per_bed:
		notes.append(_("The protocol does not state a net area per bed, so the "
		               "hectares this plan needs cannot be computed."))
	out["verdict"] = "\n".join(notes) or _(
		"{0} ha of {1} ha, {2} beds of {3} at the peak."
	).format(round(t["space_required_ha"], 3), round(t["space_stated_ha"], 3),
	         t["beds_required_peak"], t["beds_available_stated"])
	return out
