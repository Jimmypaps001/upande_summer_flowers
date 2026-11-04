# Copyright (c) 2026, James Kiruga and contributors
"""The one place a summer flower protocol is edited."""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

# Natives the protocol is EDITED in. Read from, never written back to: they are the
INPUT_NATIVE = {"farm", "variety", "plants_per_sqm", "weeks_to_pinch",
                "weeks_between_flushes", "yield_stems_per_ha"}
# Natives the protocol DERIVES into. These sat at zero for summer flowers before.
DERIVED_NATIVE = {"total_weeks_in_ground", "total_flushes",
                  "total_stems_per_plant_life", "life_expectancy_years"}

# our field name  ->  where it lives on Crop Protocol
NATIVE = {
	"farm": "farm",
	"variety": "variety",
	"plants_per_sqm_net": "plants_per_sqm",
	"weeks_to_pinch": "weeks_to_pinch",
	"flush_interval_weeks": "weeks_between_flushes",
	"total_weeks_in_ground": "total_weeks_in_ground",
	"total_flushes": "total_flushes",
	"total_stems_per_plant_life": "total_stems_per_plant_life",
	"life_expectancy_years": "life_expectancy_years",
	"stated_yield_stems_per_ha": "yield_stems_per_ha",
	# The grading loss. Native to Crop Protocol and named plainly there, so the
	"reject_pct": "reject_pct",
	# The rest of Crop Protocol, taken under the name it already has there.
	"actual_stems_per_cut": "actual_stems_per_cut",
	"breeder": "breeder",
	"color": "color",
	"crop_flush_interval_weeks": "flush_interval_weeks",
	"crop_flush_schedule": "flush_schedule",
	"crop_type": "crop_type",
	"grade_mix": "grade_mix",
	"growth_stages": "growth_stages",
	"is_summer_flower": "custom_is_summer_flower",
	"max_stems_per_plant_per_cut": "max_stems_per_plant_per_cut",
	"productive_life_weeks": "productive_life_weeks",
	"productive_life_years": "productive_life_years",
	"protocol_name": "protocol_name",
	"stems_per_cut": "stems_per_cut",
	"stems_per_plant_first_harvest": "stems_per_plant_first_harvest",
	"variety_item": "variety_item",
	"weeks_between_cuts": "weeks_between_cuts",
	"weeks_to_first_bending": "weeks_to_first_bending",
	"weeks_to_first_flush": "weeks_to_first_flush",
	"weeks_to_second_bending": "weeks_to_second_bending",
}
# The doctype this app's protocol lives on. Back on upande_agriculture's Crop
PROTOCOL_DOCTYPE = "Crop Protocol"

SKIP = ("Section Break", "Column Break", "Tab Break", "HTML")
# Machinery that belongs to the snapshot, never to the editable protocol.
MACHINERY = {
	"crop_protocol", "version", "version_status", "workflow_state", "effective_from",
	"effective_to", "is_current", "supersedes", "superseded_by", "change_reason",
	"approved_by", "approved_on", "naming_series",
	# The snapshot's own heading. It carries the version number, so copying it
	"title",
	# Worked out by the version's own controller, not copied from anywhere, so
	"route_summary", "bed_area_assumed",
}


def _fields():
	"""Every Crop Protocol Version field that carries protocol data."""
	return [f for f in frappe.get_meta("Crop Protocol Version").fields
	        if f.fieldtype not in SKIP and f.fieldname not in MACHINERY]


def multiplication_factor(cycles, per_cycle=0):
	"""What one plantlet becomes after `cycles` multiplications."""
	cycles = cint(cycles)
	if cycles < 1:
		return 1.0
	each = flt(per_cycle)
	return float((each if each > 1 else 2.0) ** cycles)


def variety_name(variety):
	"""What the farm calls this variety, falling back to its item code."""
	if not variety:
		return variety
	try:
		return frappe.get_cached_value("Item", variety, "item_name") or variety
	except Exception:
		return variety


def variety_names(codes):
	"""{item code: what the farm calls it} for a list of varieties."""
	codes = sorted({c for c in (codes or []) if c})
	if not codes:
		return {}
	rows = frappe.get_all("Item", filters={"name": ["in", codes]},
	                      fields=["name", "item_name"])
	out = {r.name: (r.item_name or r.name) for r in rows}
	# A code with no Item behind it still has to render as something.
	for c in codes:
		out.setdefault(c, c)
	return out


def source_field(fieldname):
	"""Where a version field's value is held on Crop Protocol."""
	return NATIVE.get(fieldname, "custom_sf_" + fieldname)


def is_summer_flower(doc):
	return bool(cint(doc.get("custom_is_summer_flower")))


# The one thing a user picks, and everything that used to be picked separately.
SUMMER_FLOWER = "Summer Flowers"
ROSES = "Roses"


def before_validate(doc, method=None):
	"""Derive the discriminators from crop_type, for every crop."""
	ct = doc.get("crop_type")
	if ct in (SUMMER_FLOWER, ROSES):
		doc.custom_is_summer_flower = 1 if ct == SUMMER_FLOWER else 0
		doc.custom_sf_crop_class = "Summer Flower" if ct == SUMMER_FLOWER else "Rose"

	# variety and variety_item are the same Item under two labels, which is why
	if doc.get("farm") and not doc.get("custom_sf_company"):
		doc.custom_sf_company = frappe.db.get_value("Farm", doc.get("farm"), "company")
	# The build-up profile carried a default on the version field and none on the
	if not doc.get("custom_sf_ramp_profile"):
		doc.custom_sf_ramp_profile = "25,50,75,100"
	# An empty Select reads as NULL on rows written before the field existed and
	for f in doc.meta.fields:
		if f.fieldtype == "Select" and doc.get(f.fieldname) is None:
			doc.set(f.fieldname, "")

	if doc.get("variety") and not doc.get("variety_item"):
		doc.variety_item = doc.get("variety")
	elif doc.get("variety_item") and not doc.get("variety"):
		doc.variety = doc.get("variety_item")

	# What this protocol is called, as against what it is named. The document is
	variety = doc.get("variety") or doc.get("variety_item")
	if variety:
		doc.custom_sf_title = " - ".join(
			x for x in (variety_name(variety), doc.get("farm")) if x)

	# Early, so that everything downstream -- the derivation, the native mirror,
	spread_life_over_flushes(doc)


def spread_life_over_flushes(doc):
	"""Type the lifetime yield and the flushes follow it."""
	if not is_summer_flower(doc):
		return
	rows = doc.get("custom_sf_flush_schedule") or []
	if not rows:
		return

	before = None if doc.is_new() else doc.get_latest()
	def _was(row):
		if not before:
			return None
		for r in (before.get("custom_sf_flush_schedule") or []):
			if cint(r.flush_number) == cint(row.flush_number):
				return r
		return None

	stated = flt(doc.get("custom_sf_stated_stems_per_plant_life"))
	stems_now = sum(flt(r.stems_per_plant) for r in rows)
	life_typed = bool(before) and _moved(
		stated, flt(before.get("custom_sf_stated_stems_per_plant_life")))
	share_typed = any(_moved(flt(r.pct_of_life), flt((_was(r) or {}).get("pct_of_life")))
	                  for r in rows)

	# A life of nothing is the field left blank, not an instruction to empty the
	shares = [flt(r.pct_of_life) for r in rows]
	if (life_typed or share_typed or not stems_now) and stated > 0 and sum(shares) > 0:
		total_share = sum(shares)
		for row, share in zip(rows, shares):
			row.stems_per_plant = stated * share / total_share
		stems_now = sum(flt(r.stems_per_plant) for r in rows)
	elif stems_now:
		# Nobody touched the life or the shares, so it is the sum of its flushes.
		doc.custom_sf_stated_stems_per_plant_life = stems_now

	cumulative = 0.0
	for row in rows:
		if stems_now:
			row.pct_of_life = flt(row.stems_per_plant) / stems_now * 100.0
		cumulative += flt(row.pct_of_life)
		row.cumulative_pct = cumulative


def _moved(a, b):
	"""Did a figure actually change, allowing for the slop in a repeating share?"""
	if b is None:
		return bool(a)
	return abs(flt(a) - flt(b)) > 1e-6


# The stages material can be bought at, and the one every route has to end on.
from upande_summer_flowers.summer_flowers import stages as _stages

ROUTE_ENTRY = _stages.StageSet((_stages.BOUGHT, _stages.TAKEN))
ROUTE_END = _stages.end_stage()
READY_TO_PLANT = _stages.StageSet(flag="ready_to_plant")
ROOTING_LOSSES = _stages.StageSet(flag="takes_rooting_losses")


def route_rows(doc):
	"""Every row of the route in order, stages and the steps inside them."""
	rows = doc.get("custom_sf_material_route") or doc.get("material_route") or []
	return [r for r in rows if (r.get("stage") or r.get("step"))]


def route_chains(doc):
	"""The route as one or more chains, each [(stage row, [step rows]), ...]."""
	return _chains_of(route_blocks(doc))


def _chains_of(blocks):
	"""Split a built block list into chains at each entry stage."""
	chains, current = [], []
	for stage, steps in blocks:
		if current and stage.stage in ROUTE_ENTRY:
			chains.append(current)
			current = []
		current.append((stage, steps))
	if current:
		chains.append(current)
	return chains


def chain_share(chain):
	"""What share of the order this chain carries, 0-100. One chain takes it all."""
	return flt(chain[0][0].get("share_pct")) if chain else 0.0


def is_step(row):
	return (row.get("row_type") or "Stage") == "Step"


def route_blocks(doc):
	"""The route as [(stage row, [its step rows])], in order."""
	blocks, current = [], None
	for row in route_rows(doc):
		if is_step(row):
			if current:
				current[1].append(row)
		else:
			current = (row, [])
			blocks.append(current)
	return blocks


def block_weeks(stage, steps):
	"""How long a stage lasts: its steps if it has any, else what it says itself."""
	return sum(cint(s.weeks) for s in steps) if steps else cint(stage.weeks)


def survival(rows):
	"""What is left after these rows, as a fraction."""
	out = 1.0
	for r in rows:
		out *= 1.0 - flt(r.get("loss_pct")) / 100.0
	return out


def route_summary(doc):
	"""The route as one line: "TC -> Motherstock -> Plants"."""
	return " -> ".join(r.stage for r, _steps in route_blocks(doc))


def farm_bed_geometry(farm):
	"""The bed and block a farm actually has, read off its own records."""
	if not farm:
		return None, None
	# A bed reaches its farm two ways: its own `farm` field, and the greenhouse
	area = frappe.db.sql(
		"""select avg(b.bed_area) from tabBed b
		   left join tabWarehouse w on w.name = b.greenhouse
		   where ifnull(b.bed_area, 0) > 0
		     and (w.custom_farm = %(farm)s or b.farm = %(farm)s)""",
		{"farm": farm})[0][0]
	beds = frappe.db.sql(
		"""select avg(custom_total_beds) from tabBlock
		   where farm=%s and ifnull(custom_total_beds,0)>0""", farm)[0][0]
	return (round(flt(area), 4) or None), (cint(round(flt(beds))) or None)

def number_flushes(doc):
	"""Number the harvest rows by where they sit."""
	for i, row in enumerate(doc.get("custom_sf_flush_schedule") or [], start=1):
		row.flush_number = i


def check_route(doc):
	"""A route has to begin somewhere real, end in a plant, and keep its levels straight."""
	all_rows = route_rows(doc)
	if not all_rows:
		return
	if is_step(all_rows[0]):
		frappe.throw(
			_("A route opens with a stage. {0} is a step, and a step belongs to the "
			  "stage above it.").format(frappe.bold(all_rows[0].get("step") or "")),
			title=_("Route starts on a step"))
	for row in all_rows:
		if not is_step(row):
			continue
		# A step is the same material standing still. Anything that multiplies it or
		if flt(row.get("yields_per_unit") or 1) != 1:
			frappe.throw(
				_("{0} is a step, and a step cannot multiply material. A row that "
				  "turns one unit into several is a stage.").format(
					frappe.bold(row.get("step") or "")),
				title=_("A step that multiplies"))
		if cint(row.get("is_purchase")):
			frappe.throw(
				_("{0} is a step. Material is bought at a stage -- you buy a thing, "
				  "not a part of the wait for it.").format(
					frappe.bold(row.get("step") or "")),
				title=_("A step that is bought"))
	# A stage that carries steps lasts as long as they do, so it is not typed twice.
	for stage, steps in route_blocks(doc):
		if steps:
			stage.weeks = block_weeks(stage, steps)

	chains = route_chains(doc)
	for chain in chains:
		rows = [r for r, _steps in chain]
		if rows[0].stage not in ROUTE_ENTRY:
			frappe.throw(
				_("A route starts with the material that is bought or taken: {0}. "
				  "{1} cannot be the first stage.").format(
					", ".join(ROUTE_ENTRY), frappe.bold(rows[0].stage)),
				title=_("Route has no beginning"))
		if rows[-1].stage != ROUTE_END:
			frappe.throw(
				_("Every route ends at {0}, because that is what goes in the ground. "
				  "The route starting at {1} ends at {2}.").format(
					frappe.bold(ROUTE_END), frappe.bold(rows[0].stage),
					frappe.bold(rows[-1].stage)),
				title=_("Route has no end"))
		# Bought-in Plants IS the plant, so it needs no stages after it.
		if rows[0].stage in READY_TO_PLANT and len(rows) > 2:
			frappe.throw(
				_("Plants bought ready to plant do not pass through {0}. The route is "
				  "Bought-in Plants then Plants, or the material is not bought as "
				  "plants.").format(
					", ".join(r.stage for r in rows[1:-1])),
				title=_("Route buys plants and then grows them"))

	# Shares only mean anything when there is more than one way in. With one chain
	if len(chains) > 1:
		entries = [c[0][0] for c in chains]
		total = sum(flt(e.get("share_pct")) for e in entries)
		if abs(total - 100) > 0.01:
			frappe.throw(
				_("This protocol buys the material {0} ways, so each has to carry its "
				  "share of the order. {1} comes to {2}%, not 100%.").format(
					len(chains),
					" + ".join("%s %s%%" % (e.stage, flt(e.get("share_pct")))
					           for e in entries),
					total),
				title=_("The shares do not add up"))
		if len({e.stage for e in entries}) < len(entries):
			frappe.throw(
				_("Two routes here start at the same stage. Buying twice from one "
				  "form is one line with one share, not two."),
				title=_("The same way in, twice"))


# ---------------------------------------------------------------------------

# A stage that is established once and cut from week after week. It is not
STANDING_STAGES = _stages.StageSet((_stages.MOTHERSTOCK,))

# Material that comes from outside the farm. A route that starts at one of these
BOUGHT_STAGES = _stages.StageSet((_stages.BOUGHT,))

# Material the farm takes off its own crop. Nothing is ordered for these.
TAKEN_STAGES = _stages.StageSet((_stages.TAKEN,))


# Where each stage's numbers live on the protocol. The route used to hold its own
def _n(doc, field, default=0.0, native=False):
	name = field if native else source_field(field)
	return flt(doc.get(name) or default)



def _native_blocks(rows):
	"""route_blocks() for a Crop Protocol Version, whose route is not custom_sf_."""
	blocks, current = [], None
	for row in (rows or []):
		if (row.get("row_type") or "Stage") == "Step":
			if current:
				current[1].append(row)
		else:
			current = (row, [])
			blocks.append(current)
	return blocks


def set_route_quantities(doc, native=False):
	"""Write the protocol's own arithmetic onto the route's quantity columns."""
	# A Crop Protocol holds these under custom_sf_*; the Version that snapshots it
	cycles = cint(_n(doc, "max_multiplication_cycles", native=native))
	per_cycle = _n(doc, "multiplication_factor_per_cycle", native=native)
	factor = multiplication_factor(cycles, per_cycle)
	rooting = _n(doc, "rooting_success_pct", 100, native=native) / 100.0
	establishment = _n(doc, "field_establishment_pct", 100, native=native) / 100.0
	rows = (doc.get("material_route") or []) if native else None

	# Weeks each stage takes, off the protocol's own timings. They were typed on the
	establish = (cint(_n(doc, "weeks_on_tray", native=native))
	             + cint(_n(doc, "weeks_on_pot", native=native)))
	hardening = cint(_n(doc, "hardening_weeks", native=native))
	cycle_time = cint(_n(doc, "cycle_time_weeks", native=native))
	to_planting = cint(_n(doc, "sticking_to_planting_weeks", native=native))

	blocks = _native_blocks(rows) if native else route_blocks(doc)
	names = [st.stage for st, _s in blocks]
	# The first stage from outside the farm, in EACH chain. It used to be the first
	bought_in_chain = set()
	for chain in _chains_of(blocks):
		entry = next((st for st, _s in chain if st.stage in BOUGHT_STAGES), None)
		if entry:
			bought_in_chain.add(id(entry))

	# With one way in, every stage figure comes off the protocol's own fields and
	multi = len(_chains_of(blocks)) > 1 or bool(not native and doc.flags.sf_typed_weeks)

	for stage, steps in blocks:
		name = stage.stage
		standing = 1 if name in STANDING_STAGES else 0
		stage.is_purchase = 1 if id(stage) in bought_in_chain else 0
		typed_weeks = cint(stage.get("weeks")) if multi else 0
		if not native and doc.flags.sf_typed_weeks and standing:
			# A motherstock's weeks are its tray and pot weeks, not typed against the way.
			typed_weeks = 0
		# And how long it takes. A stage with steps under it is the sum of them --
		if not steps:
			if typed_weeks and name != ROUTE_END:
				stage.weeks = typed_weeks
			elif name == ROUTE_END:
				stage.weeks = 0
			elif standing:
				# Plantlet to first cutting off the pool it becomes.
				stage.weeks = establish + (hardening if cint(_n(
					doc, "establishment_includes_hardening", native=native)) else 0)
			elif cint(stage.is_purchase):
				# The material arriving takes no time; the wait is the supplier's,
				stage.weeks = 0
			elif name in ROOTING_LOSSES:
				# Stuck to plantable.
				stage.weeks = to_planting or establish
			elif cycle_time:
				stage.weeks = cycle_time
		stage.is_standing = standing
		stage.yields_per_week = (_n(doc, "cuttings_per_plant_per_week", native=native)
		                         if standing else 0)
		if name == ROUTE_END:
			# Planting out. The end of the road multiplies nothing, but it is not
			stage.yields_per_unit = 1
			stage.loss_pct = round((1 - establishment) * 100, 4)
			continue
		if standing:
			# One plantlet becomes `factor` mother plants over its cycles, and each
			stage.yields_per_unit = factor
			# No loss here. What a pool loses is cuttings that fail to root, and
			stage.loss_pct = 0
		elif cint(stage.is_purchase):
			# The allowance on the order itself: order the requirement exactly and
			stage.yields_per_unit = 1
			stage.loss_pct = _n(doc, "tc_order_loss_pct", native=native)
		elif name in ROOTING_LOSSES:
			# Raising bought or cut material to something plantable. What is lost
			stage.yields_per_unit = 1
			stage.loss_pct = round((1 - rooting) * 100, 4)
		else:
			stage.yields_per_unit = 1
		for step in steps:
			# A step is the same material standing still; check_route already refuses
			step.yields_per_unit = 1
			step.is_standing = 0
			step.yields_per_week = 0


def to_version(doc, version=None):
	"""Load a Crop Protocol's values onto an (unsaved) Crop Protocol Version."""
	v = version or frappe.new_doc("Crop Protocol Version")
	for f in _fields():
		src = source_field(f.fieldname)
		if f.fieldtype == "Table":
			v.set(f.fieldname, [])
			for row in (doc.get(src) or []):
				v.append(f.fieldname, {
					k: row.get(k) for k in row.as_dict()
					if k not in ("name", "parent", "parenttype", "parentfield",
					             "doctype", "idx", "owner", "creation", "modified",
					             "modified_by", "docstatus")
				})
			continue
		v.set(f.fieldname, doc.get(src))
	# Derived on the protocol, stored on the snapshot: see route_summary().
	v.route_summary = route_summary(doc)
	v.crop_protocol = doc.name
	return v


def derive(doc):
	"""Compute every derived figure by running the version's own controller."""
	if not is_summer_flower(doc):
		return
	if not doc.get("plants_per_sqm"):
		# The version controller throws without it, and a half-filled protocol
		return

	probe = to_version(doc)
	probe.version = 1
	probe.effective_from = frappe.utils.nowdate()
	probe.change_reason = "derivation probe"
	probe.flags.from_protocol_snapshot = True   # it is never saved; the lock would
	probe.flags.ignore_permissions = True       # otherwise refuse the validate call
	probe.flags.ignore_mandatory = True
	probe.run_method("validate")

	for f in _fields():
		if not f.read_only or f.fieldtype == "Table":
			continue
		target = source_field(f.fieldname)
		# Only our own fields and the four natives this protocol is meant to derive.
		if target in INPUT_NATIVE or (
			not target.startswith("custom_sf_") and target not in DERIVED_NATIVE
		):
			continue
		doc.set(target, probe.get(f.fieldname))
	# The flush interval is typed as a seed for blank offsets, and read back off the
	if cint(probe.flush_interval_weeks):
		doc.weeks_between_flushes = cint(probe.flush_interval_weeks)

	# The flush rows gain their derived columns too.
	rows = {cint(r.flush_number): r for r in probe.flush_schedule}
	for row in (doc.get("custom_sf_flush_schedule") or []):
		p = rows.get(cint(row.flush_number))
		if p:
			row.weeks_from_planting = p.weeks_from_planting
			row.harvest_week_of_year = p.harvest_week_of_year
			# The share of the life this flush is, and everything gathered by the end
			row.pct_of_life = p.pct_of_life
			row.cumulative_pct = p.cumulative_pct
			# And the stems themselves, when the flush was entered as a percentage
			row.stems_per_plant = p.stems_per_plant


# Before routes existed the propagation half of the timeline was this: six fixed
LEGACY_PROP_STAGES = [
	# label,                  from week expression,        to week expression
	("Sticking to rooting", "0", "weeks_on_tray"),
	("Rooted, on pot", "weeks_on_tray", "weeks_on_tray + weeks_on_pot"),
	("Hardening", "weeks_on_tray + weeks_on_pot",
	 "weeks_on_tray + weeks_on_pot + hardening_weeks"),
]


def _legacy_prop_stages(doc):
	env = {
		"weeks_on_tray": cint(doc.get("custom_sf_weeks_on_tray")),
		"weeks_on_pot": cint(doc.get("custom_sf_weeks_on_pot")),
		"hardening_weeks": cint(doc.get("custom_sf_hardening_weeks")),
	}
	out = []
	for label, frm, to in LEGACY_PROP_STAGES:
		try:
			a, b = int(eval(frm, {}, env)), int(eval(to, {}, env))
		except Exception:
			continue
		if b > a:
			out.append((label, a, b))
	return out


def _route_prop_stages(doc):
	"""The propagation half, read straight off the route."""
	out, at = [], 0
	for stage, steps in route_blocks(doc):
		if stage.stage == ROUTE_END:
			break
		for leaf in (steps or [stage]):
			weeks = cint(leaf.get("weeks"))
			if weeks <= 0:
				continue
			label = leaf.get("step") or leaf.get("stage") or ""
			if steps and stage.stage:
				label = "%s: %s" % (stage.stage, label)
			out.append((label, at, at + weeks))
			at += weeks
	return out


def _field_stages(doc):
	"""The half that starts when the plant goes in the ground."""
	pinch = cint(doc.get("weeks_to_pinch"))
	first = cint(doc.get("custom_sf_first_harvest_offset_weeks"))
	life = cint(doc.get("total_weeks_in_ground"))
	out = []
	if pinch and first > pinch:
		out.append(("Planted to pinch", 0, pinch))
		out.append(("Pinch to first harvest", pinch, first))
	elif first:
		out.append(("Planted to first harvest", 0, first))
	if life > first:
		out.append(("Harvesting to uproot", first, life))
	turn = cint(doc.get("custom_sf_turnaround_weeks"))
	if life and turn:
		out.append(("Uproot to replant", life, life + turn))
	return out


def set_growth_stages(doc):
	"""Fill the growth stage table from the route and the field timeline."""
	if not is_summer_flower(doc):
		return
	prop = _route_prop_stages(doc) or _legacy_prop_stages(doc)
	field = _field_stages(doc)
	if not prop and not field:
		return

	# Write whichever columns this site's child doctype actually has. The boundary
	child = doc.meta.get_field("growth_stages").options
	stage_meta = frappe.get_meta(child)
	has_span = stage_meta.has_field("days_from") and stage_meta.has_field("days_to")
	has_to_harvest = stage_meta.has_field("days_to_harvest")

	rows = []
	for order, (label, a, b, clock) in enumerate(
		[(l, a, b, "propagation") for l, a, b in prop]
		+ [(l, a, b, "field") for l, a, b in field], start=1
	):
		row = {
			"stage_name": label,
			"stage_order": order,
			"description": _("Weeks {0} to {1} {2}").format(
				a, b,
				_("from the start of propagation") if clock == "propagation"
				else _("from planting")),
		}
		if has_span:
			row["days_from"] = a * 7
			row["days_to"] = b * 7
			if stage_meta.has_field("weeks"):
				row["weeks"] = b - a
		if has_to_harvest:
			row["days_to_harvest"] = b * 7
		rows.append(row)

	doc.set("growth_stages", [])
	for r in rows:
		doc.append("growth_stages", r)


def set_length_distribution(doc):
	"""Deliberately does nothing: the native table is not the same thing."""
	return


def set_native_flush_schedule(doc):
	"""Mirror the flush schedule into the protocol's own native table, if it has one."""
	if not is_summer_flower(doc):
		return
	if not doc.meta.has_field("flush_schedule"):
		return
	rows = sorted((doc.get("custom_sf_flush_schedule") or []),
	              key=lambda r: cint(r.flush_number))
	if not rows:
		return
	doc.set("flush_schedule", [])
	prev = 0
	for r in rows:
		frm_pinch = cint(r.weeks_from_pinch)
		doc.append("flush_schedule", {
			"flush_number": cint(r.flush_number),
			"stems_per_plant": flt(r.stems_per_plant),
			# Crop Protocol Flush measures the gap from the flush before it; the first
			"weeks_after_previous": frm_pinch - prev,
		})
		prev = frm_pinch


def fill_native_gaps(doc):
	"""Give the natives our own numbers instead of leaving them at zero."""
	if not is_summer_flower(doc):
		return
	rows = sorted((doc.get("custom_sf_flush_schedule") or []),
	              key=lambda r: cint(r.flush_number))
	if rows:
		# The native field asks for pinch to first harvest, which is our first
		doc.weeks_pinch_to_first_harvest = cint(rows[0].weeks_from_pinch)
	grades = doc.get("custom_sf_grade_allocation") or []
	total = sum(flt(g.allocation_pct) for g in grades)
	if grades and total:
		# Weighted mean of the grade bands: 60cm at 30%, 70 at 40%, 80 at 30% is 71.
		acc = 0.0
		for g in grades:
			digits = "".join(ch for ch in (g.grade or "") if ch.isdigit())
			if digits:
				acc += float(digits) * flt(g.allocation_pct)
		doc.average_stem_length_cm = round(acc / total, 2) if acc else 0
	# Weeks to max PC and the build-up are the same span; the build-up is the one typed.
	if cint(doc.get("custom_sf_ramp_weeks")):
		doc.custom_sf_weeks_to_max_pc = cint(doc.get("custom_sf_ramp_weeks"))


# ---------------------------------------------------------------------------

ACTIVITY_FIELD = "custom_sf_field_activities"
# Rows the system keeps in the table and dates itself. Planting is week 0 by
FIXED_ROLES = ("Planting", "Harvesting", "Uprooting")


def _activity_for(role):
	return frappe.db.get_value("Field Activity", {"role": role}, "name")


def _activity_rows(doc):
	rows = doc.get(ACTIVITY_FIELD) or []
	for r in rows:
		# fetch_from only fills on the client; a row added in code has no role yet.
		if r.activity and not r.role:
			r.role = frappe.db.get_value("Field Activity", r.activity, "role") or ""
	return rows


def set_pinch_from_activities(doc):
	"""Not every crop is pinched -- some are bent, most are planted and cut -- so"""
	if not doc.get(ACTIVITY_FIELD):
		seed_field_activities(doc)
	pinches = sorted((r for r in _activity_rows(doc) if r.role == "Pinching"),
	                 key=lambda r: cint(r.weeks_from_planting))
	doc.weeks_to_pinch = cint(pinches[0].weeks_from_planting) if pinches else 0


def seed_field_activities(doc):
	for role, weeks in (("Planting", 0),
	                    ("Pinching", cint(doc.get("weeks_to_pinch"))),
	                    ("Harvesting", cint(doc.get("custom_sf_first_harvest_offset_weeks"))),
	                    ("Uprooting", cint(doc.get("total_weeks_in_ground")))):
		if role == "Pinching" and not weeks:
			continue
		activity = _activity_for(role)
		if activity:
			doc.append(ACTIVITY_FIELD, {"activity": activity, "role": role,
			                            "weeks_from_planting": weeks})


def fill_field_activities(doc):
	"""Date the rows the system owns, after derive() has worked out the harvest."""
	rows = list(_activity_rows(doc))
	weeks = {"Planting": 0,
	         "Harvesting": cint(doc.get("custom_sf_first_harvest_offset_weeks")),
	         "Uprooting": cint(doc.get("total_weeks_in_ground"))}
	for role in FIXED_ROLES:
		mine = [r for r in rows if r.role == role]
		if not mine:
			activity = _activity_for(role)
			if not activity:
				continue
			mine = [doc.append(ACTIVITY_FIELD, {"activity": activity, "role": role})]
			rows.append(mine[0])
		for r in mine:
			r.weeks_from_planting = weeks[role]
	# In the order they happen; planting first and uprooting last on a tie.
	rank = {"Planting": 0, "Uprooting": 2}
	rows.sort(key=lambda r: (cint(r.weeks_from_planting), rank.get(r.role, 1)))
	for i, r in enumerate(rows, start=1):
		r.idx = i
	doc.set(ACTIVITY_FIELD, rows)


def on_update(doc, method=None):
	"""When the workflow reaches Approved, write the snapshot."""
	if not is_summer_flower(doc):
		return
	# A bulk move is not an edit. This guard used to live on the
	if frappe.flags.get("sf_protocol_move"):
		return
	if doc.get("custom_sf_protocol_status") != "Approved":
		return
	changed = diff_against_current(doc)
	if changed is None or changed:
		try:
			approve(doc.name)
		except frappe.ValidationError:
			# approve() states its own reason -- no farm, no change reason. Leave the
			raise


def validate(doc, method=None):
	# Crop Protocol now holds every crop, so this runs on rose and chrysanthemum
	if not is_summer_flower(doc):
		return
	# The ramp drives weeks-to-max-PC, and the establishment total is built from
	if cint(doc.get("custom_sf_ramp_weeks")):
		doc.custom_sf_weeks_to_max_pc = cint(doc.get("custom_sf_ramp_weeks"))
	from upande_summer_flowers.summer_flowers import route_methods as rm
	from upande_summer_flowers.summer_flowers import route_ways as rw

	# The ways in are what the user enters. A protocol from before them still has
	if not rw.has_ways(doc):
		rm.check_shares(doc)
		rm.sync_route_from_methods(doc)
		rw.ways_from_route(doc)
	if rw.has_ways(doc):
		rw.sync_route_from_ways(doc)
	set_pinch_from_activities(doc)
	number_flushes(doc)
	derive(doc)
	check_route(doc)
	set_raised_from_pool(doc)
	set_row_types(doc)
	set_harvest_is_scheduled(doc)
	check_lifts(doc)
	set_route_quantities(doc)
	rm.sync_methods_from_route(doc)
	rw.fill_way_stages(doc)
	fill_native_gaps(doc)
	set_status(doc)
	set_growth_stages(doc)
	set_length_distribution(doc)
	set_native_flush_schedule(doc)
	fill_field_activities(doc)

# ---------------------------------------------------------------------------

def approver_roles():
	"""Who may approve a protocol: Summer Flower Settings, else the managers."""
	from upande_summer_flowers.summer_flowers.settings import approver_roles as _roles

	return _roles()


def _has_role():
	return bool(set(approver_roles()) & set(frappe.get_roles(frappe.session.user)))


def _comparable(doc_or_version, from_protocol):
	"""The protocol's INPUTS, flattened, so two of them can be compared."""
	out = {}
	for f in _fields():
		if f.read_only:
			continue
		name = source_field(f.fieldname) if from_protocol else f.fieldname
		val = doc_or_version.get(name)
		if f.fieldtype == "Table":
			out[f.fieldname] = [
				tuple(round(flt(r.get(k)), 4) if isinstance(r.get(k), (int, float))
				      else r.get(k)
				      for k in sorted(r.as_dict())
				      if k not in ("name", "parent", "parenttype", "parentfield",
				                   "doctype", "idx", "owner", "creation", "modified",
				                   "modified_by", "docstatus"))
				for r in (val or [])
			]
		elif isinstance(val, float):
			out[f.fieldname] = round(val, 4)
		else:
			out[f.fieldname] = val
	return out


def diff_against_current(doc):
	"""Which fields differ from the snapshot in force."""
	current = doc.get("custom_sf_current_version")
	if not current or not frappe.db.exists("Crop Protocol Version", current):
		return None
	v = frappe.get_doc("Crop Protocol Version", current)
	mine, theirs = _comparable(doc, True), _comparable(v, False)
	return sorted(k for k in mine if mine[k] != theirs[k])


POOL_STAGES = STANDING_STAGES + TAKEN_STAGES

# A lift profile may miss a hundred by this much and still be rounding: three
def lift_slack():
	"""How far lift shares may be off 100%: Summer Flower Settings, else half a percent."""
	from upande_summer_flowers.summer_flowers.settings import lift_share_tolerance

	return lift_share_tolerance()


def set_raised_from_pool(doc):
	"""Whether this crop is cut from a pool of its own, or handed its material."""
	rows = route_rows(doc) or []
	doc.custom_sf_raised_from_pool = 1 if any(
		(r.get("stage") or "") in POOL_STAGES for r in rows) else 0


def check_grades(doc):
	"""A protocol cannot be approved without saying what it grades out as."""
	rows = doc.get("custom_sf_grade_allocation") or []
	if not rows:
		frappe.throw(_(
			"{0} has no grade allocation. A protocol is approved so plans and "
			"budgets can be built on it, and a stem is priced by its grade, so "
			"say what this crop grades out as before approving it."
		).format(doc.name), title=_("No grades"))
	total = sum(flt(r.get("allocation_pct")) for r in rows)
	if abs(total - 100) > 0.5:
		frappe.throw(_(
			"The grades on {0} come to {1}%. Every stem lands in one grade or "
			"another, so they have to make a hundred -- {2}."
		).format(doc.name, round(total, 2),
		         _("take {0}% off").format(round(total - 100, 2)) if total > 100
		         else _("another {0}% is unaccounted for").format(
			         round(100 - total, 2))),
			title=_("Grades do not make a whole"))
	seen = set()
	for r in rows:
		if not r.get("grade"):
			frappe.throw(_("A grade row on {0} names no grade.").format(doc.name))
		if r.get("grade") in seen:
			frappe.throw(_("{0} is listed twice on {1}.").format(
				r.get("grade"), doc.name))
		seen.add(r.get("grade"))


def set_row_types(doc):
	"""A row that names a step IS a step. Nothing else can say it."""
	for row in (doc.get("custom_sf_material_route") or []):
		row.row_type = "Step" if (row.get("step") or "").strip() else "Stage"
		if row.row_type == "Step":
			# A step is a phase of the stage above, so it cannot also BE one.
			row.stage = None


def set_harvest_is_scheduled(doc):
	"""Whether the harvest is entered rather than seeded."""
	doc.custom_sf_harvest_is_scheduled = 1 if (
		doc.get("custom_sf_flush_schedule") or []) else 0


def check_lifts(doc):
	"""Each lift adds its two halves, and each form's shares make a whole."""
	rows = doc.get("custom_sf_lifts") or []
	if not rows:
		return
	from upande_summer_flowers.summer_flowers.route_ways import LIFT_FORM, ROUTES

	groups = {}
	for r in rows:
		# The route already says which material is being lifted -- a route that
		if not r.form and r.route:
			r.form = LIFT_FORM.get((ROUTES.get(r.route) or [None])[0])
		if not r.form:
			frappe.throw(_("Row {0} of the lifts is not against a route, so there "
			               "is nothing to say which material it hands over. Add it "
			               "from the route it belongs to.").format(r.idx))
		r.weeks_after_arrival = cint(r.trough_weeks) + cint(r.rooting_weeks)
		groups.setdefault(r.form, []).append(r)

	for form, mine in sorted(groups.items()):
		total = sum(flt(r.share_pct) for r in mine)
		if total > 100 + lift_slack():
			frappe.throw(_(
				"The {0} lifts come to {1}%. A consignment cannot hand over more "
				"than all of itself, so take {2}% off somewhere."
			).format(form, round(total, 2), round(total - 100, 2)),
				title=_("More than a whole consignment"))
		if total < 100 - lift_slack():
			frappe.throw(_(
				"The {0} lifts come to {1}%, so {2}% of every consignment is "
				"never handed over. Add a lift for it, or raise the shares."
			).format(form, round(total, 2), round(100 - total, 2)),
				title=_("Less than a whole consignment"))

		seen = {}
		for r in sorted(mine, key=lambda x: cint(x.lift_no)):
			n = cint(r.lift_no)
			if not n:
				frappe.throw(_("Number the {0} lifts: 1, 2, 3.").format(form))
			if n in seen:
				frappe.throw(_("There are two {0} lift {1}s.").format(form, n))
			if flt(r.share_pct) <= 0:
				frappe.throw(_("{0} lift {1} takes no share of the consignment. "
				               "Give it one or remove it.").format(form, n))
			seen[n] = r
		order = [seen[k] for k in sorted(seen)]
		for a, b in zip(order, order[1:]):
			if cint(b.weeks_after_arrival) <= cint(a.weeks_after_arrival):
				frappe.throw(_(
					"{0} lift {1} reaches the farm {2} weeks after the "
					"consignment arrives and lift {3} reaches it at {4}. A "
					"later lift comes "
					"off later, so give it more weeks on the trough."
				).format(form, cint(b.lift_no), cint(b.weeks_after_arrival),
				         cint(a.lift_no), cint(a.weeks_after_arrival)))


def protocol_lifts(doc, form):
	"""[(weeks after arrival, share), ...] this protocol states for `form`."""
	rows = [r for r in (doc.get("custom_sf_lifts") or doc.get("lifts") or [])
	        if (r.get("form") or "") == form]
	if not rows:
		return []
	rows.sort(key=lambda r: cint(r.get("lift_no")))
	total = sum(flt(r.get("share_pct")) for r in rows) or 1.0
	scale = 100.0 if abs(total - 100.0) <= lift_slack() else total
	return [(cint(r.get("weeks_after_arrival"))
	         or cint(r.get("trough_weeks")) + cint(r.get("rooting_weeks")),
	         flt(r.get("share_pct")) / scale) for r in rows]


def set_status(doc):
	"""Say plainly whether this protocol matches what was last approved."""
	if not is_summer_flower(doc):
		return
	changed = diff_against_current(doc)
	if changed is None:
		doc.custom_sf_protocol_status = doc.get("custom_sf_protocol_status") or "Draft"
		doc.custom_sf_pending_changes = None if doc.get("custom_sf_current_version") \
			else _("Never approved. Approving writes the first snapshot.")
		return
	if changed:
		# Anything unapproved puts it back to Draft: an approved status with different
		status = doc.get("custom_sf_protocol_status")
		was = frappe.db.get_value(doc.doctype, doc.name,
		                          "custom_sf_protocol_status") if not doc.is_new() else None
		approving_now = status == "Approved" and was != "Approved"
		if status != "Pending Approval" and not approving_now:
			doc.custom_sf_protocol_status = "Draft"
		doc.custom_sf_pending_changes = _("Differs from {0} in: {1}").format(
			doc.get("custom_sf_current_version"), ", ".join(changed))
	else:
		# Matching the version again does not approve a protocol. Editing a number and
		if doc.get("custom_sf_protocol_status") == "Approved":
			doc.custom_sf_pending_changes = None
			return
		doc.custom_sf_pending_changes = _(
			"Nothing differs from {0}. Approving is still a decision someone makes, so "
			"this stays where the workflow left it."
		).format(doc.get("custom_sf_current_version")) \
			if doc.get("custom_sf_current_version") else None


@frappe.whitelist()
def submit_for_approval(protocol):
	doc = frappe.get_doc(PROTOCOL_DOCTYPE, protocol)
	if not is_summer_flower(doc):
		frappe.throw(_("Not a summer flower protocol."))
	if not doc.get("custom_sf_change_reason"):
		frappe.throw(_("Give a reason for the change before submitting it."))
	doc.db_set("custom_sf_protocol_status", "Pending Approval")
	return doc.get("custom_sf_protocol_status")


@frappe.whitelist()
def active_versions(doc):
	"""Every Active version of this protocol, the newest first."""
	seen, out = set(), []
	# The farm is on both keys. A protocol is approved for a variety AT A FARM,
	for f in ({"crop_protocol": doc.name, "farm": doc.get("farm"),
	           "version_status": "Active"},
	          {"variety": doc.get("variety"), "farm": doc.get("farm"),
	           "version_status": "Active"}):
		if not all(v for v in f.values()):
			continue
		for r in frappe.get_all("Crop Protocol Version", filters=f,
		                        fields=["name", "version", "effective_from"]):
			if r.name not in seen:
				seen.add(r.name)
				out.append(r)
	out.sort(key=lambda r: (cint(r.version), str(r.effective_from or "")),
	         reverse=True)
	return out


def approve(protocol):
	"""Approve the protocol, which is the only thing that writes a version."""
	doc = frappe.get_doc(PROTOCOL_DOCTYPE, protocol)
	if not is_summer_flower(doc):
		frappe.throw(_("Not a summer flower protocol."))
	if not _has_role():
		frappe.throw(_("Only an Agriculture Manager or Farm Manager can approve a "
		               "protocol."), frappe.PermissionError)
	if not doc.get("farm"):
		frappe.throw(_("Set the farm. A protocol is approved for a variety at a farm, "
		               "because the same variety runs differently elsewhere."))
	check_grades(doc)
	changed = diff_against_current(doc)
	if changed == []:
		frappe.throw(_("Nothing has changed since {0} was approved.").format(
			doc.get("custom_sf_current_version")))

	priors = active_versions(doc)
	prior = priors[0] if priors else None
	prior_name = prior.name if prior else None

	# A reason records what moved, so there has to be something it moved from. The
	if prior_name and not doc.get("custom_sf_change_reason"):
		frappe.throw(_("A reason is required: it is what the history records. "
		               "{0} is in force, so say what this changes.").format(prior_name))
	# Over the same scope the priors were found in. Numbering off (variety, farm)
	last = frappe.db.sql("""select ifnull(max(version), 0) from `tabCrop Protocol Version`
		where crop_protocol = %s or (variety = %s and farm = %s)""",
		(doc.name, doc.variety, doc.farm))[0][0]

	v = to_version(doc)
	v.version = cint(last) + 1
	v.farm = doc.farm
	v.variety = doc.variety
	# The version doctype requires each one to take effect after the one before it,
	effective = getdate(nowdate())
	if prior and prior.effective_from and getdate(prior.effective_from) >= effective:
		effective = add_days(getdate(prior.effective_from), 1)
	v.effective_from = effective
	v.change_reason = doc.get("custom_sf_change_reason")
	v.supersedes = prior_name
	v.version_status = "Active"
	# No workflow_state: the version's workflow is retired, and setting a state its
	v.is_current = 1
	v.approved_by = frappe.session.user
	v.approved_on = now_datetime()
	v.flags.from_protocol_snapshot = True
	v.flags.ignore_permissions = True
	v.flags.ignore_mandatory = True
	v.insert()

	# EVERY one of them, not the one a get_value happened to return. Two rows
	for row in priors:
		p = frappe.get_doc("Crop Protocol Version", row.name)
		p.db_set("superseded_by", v.name, update_modified=False)
		p.db_set("version_status", "Superseded", update_modified=False)
		p.db_set("is_current", 0, update_modified=False)
		p.db_set("effective_to", add_days(effective, -1), update_modified=False)

	doc.db_set("custom_sf_current_version", v.name, update_modified=False)
	doc.db_set("custom_sf_protocol_status", "Approved", update_modified=False)
	doc.db_set("custom_sf_approved_by", frappe.session.user, update_modified=False)
	doc.db_set("custom_sf_approved_on", now_datetime(), update_modified=False)
	doc.db_set("custom_sf_pending_changes", None, update_modified=False)
	doc.db_set("custom_version_count", cint(last) + 1, update_modified=False)
	frappe.db.commit()
	return {"version": v.name, "version_no": v.version, "supersedes": prior_name,
	        "effective_from": str(effective),
	        "changed": changed}
