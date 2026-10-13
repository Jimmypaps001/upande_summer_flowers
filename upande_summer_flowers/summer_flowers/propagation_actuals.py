# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""What the propagation unit actually did, set against what it was asked to do.

The propagation plan is rebuilt from the production plan every time it is
saved -- it clears its own weeks and consignments and lays them out again. So
nothing recorded ON those rows can survive, and actuals cannot live there. They
live in their own records and are matched back on every rebuild, which is why a
rebuild never loses them and why correcting one corrects the variance.

Two sources, because the farm genuinely has two:

    Roots are real stock. They are an Item, the cold store and the propagation
    units are Warehouses, and a transfer between them is already written in the
    stock ledger. Asking somebody to type that number again is asking for two
    numbers that will disagree.

    Tissue culture usually is not. It comes from an outside propagator who has
    no access to this site's ledger, and arrives as an email with a count on
    it. That is typed in, on a Summer Flower Propagation Actual.

Where both exist for the same kind of event, the ledger wins and the typed
records are kept but not counted -- said out loud on the plan rather than
silently dropped, because a number that vanishes is worse than one that is
wrong. The decision is taken per event type, not per row, so a season is never
half-read from one source and half from the other.

What it then does to the plan is the point of it. Weeks that have been and gone
are closed and show what was delivered against what was promised. The shortfall
they carry is offered forward against the weeks that have not happened yet,
capped at each week's variance band so that making up a gap does not break the
tolerance the planting programme was solved to hold.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

# The events, and the material each one moves. Kept here rather than read off
# the Select options so a renamed option cannot silently stop matching.
TC_IN = "TC Received"
ROOTS_IN = "Roots Withdrawn"
TO_FARM = "Plants To Farm"
LOSS = "Loss"
UPLIFT = "Replan Uplift"

STOCK_READABLE = (ROOTS_IN, TO_FARM, TC_IN)

# How far from a planned week an actual may land and still be that week's.
# Three weeks either side: a consignment a month out of place is a different
# consignment, not a late one.
MATCH_WEEKS = 3


def _iso(d):
	y, w, _dow = getdate(d).isocalendar()
	return "%d-W%02d" % (y, w)


def _d(x):
	return getdate(x) if x else None


# ------------------------------------------------------------------ sources
def route_items(version):
	"""The Item on each stage of the material route, where one is named.

	Only the Roots row carries one today, which is exactly why the split
	between read and typed is not a setting: it follows from whether the
	material is stock on this site or not.
	"""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	out = {}
	for r in (cp.route_rows(version) or []):
		stage, item = (r.get("stage") or ""), r.get("item")
		if stage and item and stage not in out:
			out[stage] = item
	return out


def unit_warehouses():
	"""Warehouses that are propagation units."""
	names = []
	if frappe.db.has_column("Warehouse", "custom_sf_is_propagation_unit"):
		names = [w.name for w in frappe.get_all(
			"Warehouse", filters={"custom_sf_is_propagation_unit": 1},
			fields=["name"])]
	single = frappe.db.get_single_value("Summer Flower Settings",
	                                    "propagation_warehouse")
	if single and single not in names:
		names.append(single)
	return names


def _span(doc):
	"""The window a movement has to fall in to belong to this season.

	Widened by a quarter either side of the plan's own dates, because material
	arrives before the first week needs it and the last hand-over trails the
	last planting.
	"""
	dates = [_d(r.week_start_date) for r in doc.intake if r.week_start_date]
	dates += [_d(r.ready_on) for r in doc.intake if r.ready_on]
	dates += [_d(r.week_start_date) for r in doc.weeks if r.week_start_date]
	dates += [_d(r.deliver_on) for r in doc.weeks if r.deliver_on]
	dates = [d for d in dates if d]
	if not dates:
		return None, None
	return add_days(min(dates), -91), add_days(max(dates), 91)


def from_stock(doc, version=None):
	"""Movements the stock ledger already carries, read rather than retyped."""
	units = unit_warehouses()
	if not units:
		return []
	start, end = _span(doc)
	if not start:
		return []

	version = version or (doc.protocol and
	                      frappe.get_cached_doc("Crop Protocol Version", doc.protocol))
	items = route_items(version) if version else {}
	# The plants themselves are the variety's own Item. Nothing else can be:
	# the variety IS an Item on this site, and inventing a second one for the
	# same plant is how a stock figure stops meaning anything.
	wanted = [
		(items.get("Roots"), ROOTS_IN, "Roots", 1),
		(items.get("TC"), TC_IN, "Tissue Culture", 1),
		(doc.variety, TO_FARM, "Plants", -1),
	]

	rows = []
	for item, event, form, sign in wanted:
		if not item or not frappe.db.exists("Item", item):
			continue
		sles = frappe.get_all(
			"Stock Ledger Entry",
			filters={"item_code": item, "warehouse": ["in", units],
			         "is_cancelled": 0, "posting_date": ["between", [start, end]],
			         "actual_qty": [">" if sign > 0 else "<", 0]},
			fields=["posting_date", "actual_qty", "warehouse", "voucher_type",
			        "voucher_no", "item_code"],
			order_by="posting_date asc")
		for s in sles:
			qty = cint(abs(flt(s.actual_qty)))
			if not qty:
				continue
			rows.append({
				"event": event, "form": form, "date": str(s.posting_date),
				"week_iso": _iso(s.posting_date), "against_week": None,
				"units": qty if event != TO_FARM else 0,
				"plants": qty if event == TO_FARM else 0,
				"reference": s.voucher_no, "supplier": None,
				"warehouse": s.warehouse, "recorded_from": "Stock Ledger",
				"voucher_type": s.voucher_type, "voucher_no": s.voucher_no,
				"name": None, "reason": None, "item": s.item_code,
			})
	return rows


def typed(doc):
	"""Submitted records somebody entered, for this variety and season.

	Keyed on variety and season rather than on the plan's name, because that is
	what the farm means by a propagation operation -- one pool, one season --
	and a plan that is rebuilt, amended or replaced must not strand what was
	recorded against it.
	"""
	if not (doc.variety and cint(doc.season_start_year)):
		return []
	rows = []
	for a in frappe.get_all(
			"Summer Flower Propagation Actual",
			filters={"variety": doc.variety,
			         "season_start_year": cint(doc.season_start_year),
			         "docstatus": 1},
			fields=["name", "event", "form", "actual_date", "week_iso",
			        "against_week", "units", "plants", "reference", "supplier",
			        "warehouse", "reason"],
			order_by="actual_date asc"):
		rows.append({
			"event": a.event, "form": a.form, "date": str(a.actual_date),
			"week_iso": a.week_iso or _iso(a.actual_date),
			"against_week": str(a.against_week) if a.against_week else None,
			"units": cint(a.units), "plants": cint(a.plants),
			"reference": a.reference, "supplier": a.supplier,
			"warehouse": a.warehouse, "recorded_from": "Typed In",
			"voucher_type": "Summer Flower Propagation Actual",
			"voucher_no": a.name, "name": a.name, "reason": a.reason,
			"item": None,
		})
	return rows


def collect(doc, version=None):
	"""Both sources, merged, with the ledger winning an event it covers.

	Per event type, never per row. Half a season read off the ledger and half
	typed in is two sets of books for one set of plants.
	"""
	ledger = from_stock(doc, version)
	hand = typed(doc)
	owned = {r["event"] for r in ledger}

	rows = []
	for r in ledger:
		r["counted"] = 1
		rows.append(r)
	for r in hand:
		# An uplift is a decision, not an observation, so no ledger entry can
		# supersede it.
		r["counted"] = 0 if (r["event"] in owned and r["event"] != UPLIFT) else 1
		rows.append(r)

	rows.sort(key=lambda r: (r["date"], r["event"]))
	superseded = sorted({r["event"] for r in rows if not r["counted"]})
	return {
		"rows": rows,
		"ledger_events": sorted(owned),
		"superseded_events": superseded,
		"units": unit_warehouses(),
	}


# ----------------------------------------------------------------- matching
def apportion(total, shares):
	"""Split `total` across `shares` so the parts sum to exactly `total`.

	Largest remainder, the same rule the planting programme uses, because the
	alternative -- rounding each part on its own -- loses or invents plants.
	"""
	base = sum(shares)
	if not base or total <= 0:
		# Nothing to go on. An even split is the only honest guess, and it
		# still has to add up.
		n = len(shares) or 1
		out = [total // n] * n
		for i in range(total - sum(out)):
			out[i % n] += 1
		return out
	exact = [total * s / base for s in shares]
	out = [int(x) for x in exact]
	for i in sorted(range(len(out)), key=lambda i: exact[i] - out[i],
	                reverse=True)[:total - sum(out)]:
		out[i] += 1
	return out


def _nearest(target_dates, when, window_days):
	"""The planned date an actual belongs to, or None if it belongs to none."""
	best, gap = None, None
	for d in target_dates:
		g = abs((getdate(when) - d).days)
		if g <= window_days and (gap is None or g < gap):
			best, gap = d, g
	return best


def match(doc, bundle):
	"""Tie each counted actual to the planned row it answers.

	Returns the per-row tallies the plan writes onto its own tables, and the
	actuals that answered nothing -- which are reported rather than dropped,
	because an unmatched delivery is usually the interesting one.
	"""
	window = MATCH_WEEKS * 7
	intake_dates = {}
	for r in doc.intake:
		if r.week_start_date:
			intake_dates.setdefault(r.form, set()).add(_d(r.week_start_date))
	week_dates = sorted({_d(r.deliver_on) for r in doc.weeks if r.deliver_on})

	by_intake, by_week, loose, losses = {}, {}, [], []
	for a in bundle["rows"]:
		if not a["counted"]:
			continue
		if a["event"] == LOSS:
			losses.append(a)
			continue
		if a["event"] == UPLIFT:
			# Uplifts are cover the plan has already taken in; they are not a
			# delivery and must not be counted as one.
			continue

		if a["event"] in (TC_IN, ROOTS_IN):
			form = "Tissue Culture" if a["event"] == TC_IN else "Roots"
			dates = sorted(intake_dates.get(form, set()))
			hit = (_d(a["against_week"]) if a["against_week"] and
			       _d(a["against_week"]) in dates else None)
			hit = hit or _nearest(dates, a["date"], window)
			if not hit:
				loose.append(a)
				continue
			slot = by_intake.setdefault((form, hit),
			                            {"units": 0, "plants": 0, "on": None,
			                             "rows": [], "from": set()})
		else:
			hit = (_d(a["against_week"]) if a["against_week"] and
			       _d(a["against_week"]) in week_dates else None)
			hit = hit or _nearest(week_dates, a["date"], window)
			if not hit:
				loose.append(a)
				continue
			slot = by_week.setdefault(hit, {"units": 0, "plants": 0, "on": None,
			                                "rows": [], "from": set()})

		slot["units"] += cint(a["units"])
		slot["plants"] += cint(a["plants"])
		slot["rows"].append(a["voucher_no"] or a["reference"])
		slot["from"].add(a["recorded_from"])
		when = _d(a["date"])
		# The date an arrival is judged on is the last of it. A consignment
		# that came in three deliveries was not complete until the third.
		if not slot["on"] or when > slot["on"]:
			slot["on"] = when

	return {"intake": by_intake, "weeks": by_week, "loose": loose,
	        "losses": losses}


# ------------------------------------------------------------------ verdict
def baseline_of(doc):
	"""The approved plan, as it stood when it was approved.

	Variance against a plan that moves with the actuals is not variance. The
	snapshot is taken at approval and never touched again, so a week is judged
	against what was promised rather than against what it has since become.
	"""
	raw = doc.get("baseline_json")
	if not raw:
		return None
	try:
		b = json.loads(raw)
	except Exception:
		return None
	return b if isinstance(b, dict) and b.get("weeks") else None


def snapshot(doc):
	"""The plan as it stands, in the shape baseline_of reads back."""
	return {
		"taken_on": str(frappe.utils.now_datetime()),
		"plants": cint(doc.total_plants_to_stick),
		"tc": cint(doc.tc_plants_required),
		"weeks": [{"deliver_on": str(r.deliver_on), "plants": cint(r.plants_to_stick)}
		          for r in doc.weeks if r.deliver_on],
		"intake": [{"week": str(r.week_start_date), "form": r.form,
		            "units": cint(r.units), "plants": cint(r.plants)}
		           for r in doc.intake if r.week_start_date],
	}


def apply_to(doc):
	"""Write the comparison onto the plan's own rows and sum it up.

	Runs after the plan has rebuilt itself, so the planned figures it judges are
	this rebuild's. The baseline, where there is one, is what the ACTUALS are
	measured against; the current rows are left saying what the plan now holds,
	so the two questions -- what was promised, what is now intended -- do not
	collapse into one number.
	"""
	version = (doc.protocol and
	           frappe.get_cached_doc("Crop Protocol Version", doc.protocol))
	bundle = collect(doc, version)
	hits = match(doc, bundle)
	base = baseline_of(doc)
	base_weeks = {_d(w["deliver_on"]): cint(w["plants"])
	              for w in (base or {}).get("weeks", []) if w.get("deliver_on")}
	today = getdate(nowdate())
	cutoff = reported_to(bundle, today)

	# ---- consignments
	# Several consignments can land in the same week -- three labs delivering
	# into one week is normal -- so a week's actuals are SPLIT across the rows
	# that make it up rather than written against each of them. Writing the
	# pool onto every row counted it once per row, and three labs sharing a
	# week turned 29,510 plantlets into 83,700.
	shared = _share(doc.intake, hits["intake"],
	                lambda r: (r.form, _d(r.week_start_date)),
	                lambda r: cint(r.units))
	tc_in = roots_in = 0
	for r in doc.intake:
		slot = hits["intake"].get((r.form, _d(r.week_start_date)))
		mine = shared.get(id(r))
		planned = cint(r.units)
		if slot:
			r.actual_units = cint(mine["units"])
			r.actual_on = slot["on"]
			r.recorded_from = ", ".join(sorted(slot["from"]))
			r.days_late = (slot["on"] - _d(r.week_start_date)).days
			r.variance_units = r.actual_units - planned
			r.state = ("Short" if r.actual_units < planned
			           else "Over" if r.actual_units > planned else "Arrived")
			if r.form == "Tissue Culture":
				tc_in += r.actual_units
			elif r.form == "Roots":
				roots_in += r.actual_units
		else:
			r.actual_units = 0
			r.actual_on = None
			r.recorded_from = None
			r.days_late = 0
			r.variance_units = 0
			r.state = _unreported(_d(r.week_start_date), cutoff, "Awaited")

	# ---- hand-overs to the farm
	for r in doc.weeks:
		r.baseline_plants = base_weeks.get(_d(r.deliver_on),
		                                   cint(r.plants_to_stick)) \
			if r.deliver_on else cint(r.plants_to_stick)
	# Two sticking weeks can hand over in the same week, so the same split
	# applies -- on what each was promised, which is what it is judged against.
	shared_w = _share(doc.weeks, hits["weeks"], lambda r: _d(r.deliver_on),
	                  lambda r: cint(r.baseline_plants))
	delivered = short = ahead = closed = 0
	for r in doc.weeks:
		when = _d(r.deliver_on)
		slot = hits["weeks"].get(when) if when else None
		mine = shared_w.get(id(r))
		promised = cint(r.baseline_plants)
		if slot:
			r.actual_plants = cint(mine["plants"])
			r.actual_on = slot["on"]
			r.recorded_from = ", ".join(sorted(slot["from"]))
		else:
			r.actual_plants = 0
			r.actual_on = None
			r.recorded_from = None
		# A week is closed when something was recorded for it, or when the unit
		# has reported past it. Not merely when it is in the past: a plan built
		# for a season already half gone would otherwise open claiming every one
		# of those weeks failed, when all that has happened is that nobody has
		# started recording. Silence is not a shortfall.
		is_closed = bool(slot) or bool(cutoff and when and when <= cutoff)
		r.state = ("Delivered" if slot and r.actual_plants >= promised
		           else "Part" if slot
		           else "Missed" if is_closed
		           else _unreported(when, cutoff, "To Come"))
		r.variance_plants = (r.actual_plants - promised) if is_closed else 0
		if is_closed:
			closed += 1
			delivered += r.actual_plants
			short += max(0, promised - r.actual_plants)
			ahead += max(0, r.actual_plants - promised)

	hits["behind"] = [r.deliver_on for r in doc.weeks if r.state == "Not Reported"]
	lost = sum(cint(a["plants"]) for a in hits["losses"])
	counted = [a for a in bundle["rows"] if a["counted"]]

	doc.actuals_recorded = len(counted)
	doc.weeks_closed = closed
	doc.reported_to = cutoff
	doc.tc_received = tc_in
	doc.roots_withdrawn = roots_in
	doc.plants_to_farm = delivered
	doc.plants_lost = lost
	doc.delivered_short = short
	doc.delivered_ahead = ahead
	doc.carried_short = max(0, short - ahead)

	# Survival as the unit actually achieved it, cohort by cohort.
	#
	# Not season-to-date. Material that came in this week becomes plants
	# fifteen weeks later, so dividing this week's hand-over by this week's
	# intake divides one cohort by a different one -- which on real figures
	# read as 468% survival, a number that is arithmetically correct and means
	# nothing. A week counts only when EVERY consignment feeding it has been
	# reported, because a week fed half from a reported batch and half from an
	# unreported one makes the reported half look miraculous.
	doc.actual_survival_pct, doc.survival_basis = _survival(doc, version)

	doc.actuals_note = _note(bundle, hits, base)
	propose(doc, version)
	return {"bundle": bundle, "hits": hits}


def reported_to(bundle, today=None):
	"""How far the unit has actually reported, which is not the same as today.

	Capped at today, because a reporting cutoff in the future would close weeks
	that have not happened -- one mistyped year on one record and a whole
	season reads as delivered or failed.
	"""
	today = today or getdate(nowdate())
	dates = [_d(a["date"]) for a in bundle["rows"]
	         if a["counted"] and a["event"] != UPLIFT]
	if not dates:
		return None
	return min(max(dates), today)


def _unreported(when, cutoff, if_future):
	"""A week nobody has reported on yet, told apart from one still to come."""
	today = getdate(nowdate())
	if when and when < today and (not cutoff or when > cutoff):
		return "Not Reported"
	return if_future


def _share(rows, slots, key_of, weight_of):
	"""Give each row its part of the pool its key collected.

	Keyed by id() rather than by name, because a child row being rebuilt in
	memory has no name yet.
	"""
	groups = {}
	for r in rows:
		k = key_of(r)
		if k in slots:
			groups.setdefault(k, []).append(r)

	out = {}
	for k, members in groups.items():
		slot = slots[k]
		weights = [max(0, weight_of(r)) for r in members]
		units = apportion(cint(slot["units"]), weights)
		plants = apportion(cint(slot["plants"]), weights)
		for r, u, p in zip(members, units, plants):
			out[id(r)] = {"units": u, "plants": p}
	return out


def _survival(doc, version):
	"""What the unit got out of the material it was given, where both ends are in."""
	per_root = _returns_per_plant(version)
	feeds = {}
	for r in doc.intake:
		if not r.deliver_on:
			continue
		slot = feeds.setdefault(_d(r.deliver_on), {"in": 0, "whole": True})
		if not cint(r.actual_units):
			# Nothing reported for this consignment, so the cohort is not whole.
			slot["whole"] = False
			continue
		slot["in"] += (int(cint(r.actual_units) * per_root)
		               if r.form == "Roots" else cint(r.actual_units))

	got = raised = 0
	weeks = 0
	for r in doc.weeks:
		slot = feeds.get(_d(r.deliver_on)) if r.deliver_on else None
		if not (slot and slot["whole"] and slot["in"] and cint(r.actual_plants)):
			continue
		got += cint(r.actual_plants)
		raised += slot["in"]
		weeks += 1

	if not raised:
		return 0, _(
			"Not yet measurable. A week can only say what survived once every "
			"consignment feeding it has been reported and the plants off it "
			"have gone out -- and no week has both ends in yet.")
	return (got * 100.0 / raised), _(
		"{0} plants off {1} raised, over the {2} weeks whose consignments and "
		"hand-overs are both fully reported."
	).format("{:,}".format(got), "{:,}".format(raised), weeks)


def _returns_per_plant(version):
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	if not version:
		return 0.0
	row = next((r for r in (cp.route_rows(version) or [])
	            if (r.get("stage") or "") == "Roots"), None)
	return flt(row and row.get("returns_per_plant") or 0)


def _note(bundle, hits, base):
	"""Where the figures came from, and what did not fit anywhere."""
	lines = []
	if bundle["ledger_events"]:
		lines.append(_("Read from the stock ledger: {0}.").format(
			", ".join(bundle["ledger_events"])))
	if bundle["superseded_events"]:
		lines.append(_(
			"Typed records for {0} are kept but not counted, because the stock "
			"ledger already carries that movement. Cancel the stock entry if "
			"the typed figure is the right one."
		).format(", ".join(bundle["superseded_events"])))
	if not bundle["units"]:
		lines.append(_(
			"No warehouse is marked as a propagation unit, so nothing can be "
			"read from the stock ledger and every figure here is typed in."))
	if hits["loose"]:
		lines.append(_(
			"{0} records do not fall within {1} weeks of any planned week and "
			"are not counted against one: {2}. Set 'Against Planned Week' on "
			"them to say which week they answer."
		).format(len(hits["loose"]), MATCH_WEEKS,
		         ", ".join(sorted({a["voucher_no"] or a["date"]
		                           for a in hits["loose"]}))[:200]))
	if hits["losses"]:
		lines.append(_("{0} plants recorded as lost: {1}.").format(
			sum(cint(a["plants"]) for a in hits["losses"]),
			"; ".join(sorted({(a["reason"] or _("no reason given")).strip()
			                  for a in hits["losses"]}))[:200]))
	cutoff = reported_to(bundle)
	if not cutoff:
		lines.append(_(
			"Nothing has been recorded against this plan yet, so no week has "
			"been judged. Weeks already in the past read as not reported "
			"rather than as missed -- nobody has said either way."))
	else:
		behind = [r for r in (hits.get("behind") or [])]
		lines.append(_("The unit has reported up to {0}.").format(cutoff))
		if behind:
			lines.append(_(
				"{0} weeks sit between that and today with nothing recorded "
				"for them. They are not counted as missed, but nothing says "
				"they happened either."
			).format(len(behind)))
	if base:
		lines.append(_("Measured against the plan as approved on {0}.").format(
			str(base.get("taken_on"))[:10]))
	elif any(a["counted"] for a in bundle["rows"]):
		lines.append(_(
			"This plan has not been approved, so there is no frozen baseline "
			"and every week is judged against the plan as it stands now -- "
			"which moves. Approve it to fix what the unit is held to."))
	return "\n".join(lines) or None


# ------------------------------------------------------------------ re-plan
def propose(doc, version=None):
	"""Offer the weeks that have not happened yet the shortfall the closed ones carry.

	Three rules, and they are the whole of it.

	    A week that has been and gone cannot be fixed. Its plants are not
	    coming, so the gap moves forward rather than staying where it was.

	    A forward week may not be loaded past its variance band. The planting
	    programme was solved to hold +/- that band against market demand, and
	    making up a shortfall by dumping it on one week would break the thing
	    the plan exists to protect.

	    Cover has to come from somewhere real. Roots still in store, or
	    plantlets a lab is already down for beyond what the order needs. A
	    re-plan that covers a gap from nothing is a worse lie than the gap.
	"""
	short = cint(doc.get("carried_short"))
	doc.replan_cover = 0
	doc.replan_still_short = 0
	if not short:
		doc.replan_note = None
		return None

	today = getdate(nowdate())
	band = _band(doc)
	forward = [r for r in doc.weeks
	           if r.deliver_on and _d(r.deliver_on) >= today]
	have = _headroom(doc, version)
	pool = cint(have["roots_plants"]) + cint(have["tc_plants"])

	left, cover = short, []
	for r in forward:
		if left <= 0 or pool <= 0:
			break
		room = int(cint(r.baseline_plants or r.plants_to_stick) * band)
		take = min(left, room, pool)
		if take <= 0:
			continue
		cover.append({"week": str(r.deliver_on), "week_iso": _iso(r.deliver_on),
		              "plants": take})
		left -= take
		pool -= take

	taken = short - left
	doc.replan_cover = taken
	doc.replan_still_short = left
	doc._replan_cover = cover
	doc.replan_note = _replan_note(doc, short, taken, left, cover, have, band)
	return {"short": short, "cover": cover, "covered": taken, "still_short": left,
	        "headroom": have}


def _band(doc):
	"""The weekly tolerance the planting programme was solved to hold."""
	pct = 0
	if doc.production_plan:
		pct = flt(frappe.db.get_value("Summer Flower Production Plan",
		                              doc.production_plan,
		                              "weekly_variance_band_pct"))
	if not pct:
		pct = flt(frappe.db.get_single_value(
			"Summer Flower Settings", "weekly_variance_tolerance_pct"))
	return (pct if pct > 0 else 10.0) / 100.0


def _headroom(doc, version=None):
	"""Material that could cover a gap, and is not already spoken for."""
	version = version or (doc.protocol and
	                      frappe.get_cached_doc("Crop Protocol Version", doc.protocol))
	per_root = _returns_per_plant(version)
	out = {"roots": 0, "roots_plants": 0, "tc_plants": 0, "why": []}

	# Roots on the shelf, less what has already come off it this season.
	try:
		from upande_summer_flowers.summer_flowers import root_line as rl

		plan = frappe.get_cached_doc("Summer Flower Production Plan",
		                             doc.production_plan)
		in_store = cint(rl.root_stock(plan).get("qty"))
	except Exception:
		frappe.clear_last_message()
		in_store = 0
	spare_roots = max(0, in_store - cint(doc.get("roots_withdrawn")))
	if spare_roots and per_root:
		out["roots"] = spare_roots
		out["roots_plants"] = int(spare_roots * per_root)
		out["why"].append(_("{0} roots still in store, worth {1} plants").format(
			"{:,}".format(spare_roots), "{:,}".format(out["roots_plants"])))

	# Plantlets already allocated to a lab over and above what the order needs.
	over = _over_allocated(doc)
	if over > 0:
		out["tc_plants"] = over
		out["why"].append(_(
			"{0} plantlets allocated to labs beyond the {1} the order needs"
		).format("{:,}".format(over), "{:,}".format(cint(doc.tc_plants_required))))
	return out


def _over_allocated(doc):
	"""Plantlets the labs are down for in excess of the committed order."""
	if not doc.production_plan:
		return 0
	raw = frappe.db.get_value("Summer Flower Production Plan",
	                          doc.production_plan, "tc_supplier_allocations")
	if not raw:
		return 0
	try:
		allocs = json.loads(raw) if isinstance(raw, str) else raw
		total = sum(cint(a.get("tc")) for a in (allocs or []))
	except Exception:
		frappe.clear_last_message()
		return 0
	return max(0, total - cint(doc.tc_plants_required))


def _replan_note(doc, short, taken, left, cover, have, band):
	lines = [_(
		"{0} plants are short across the {1} weeks that have closed. Those "
		"weeks cannot be fixed, so the gap is offered to the weeks still to "
		"come."
	).format("{:,}".format(short), cint(doc.weeks_closed))]
	if have["why"]:
		lines.append(_("Cover available: {0}.").format("; ".join(have["why"])))
	else:
		lines.append(_(
			"There is nothing to cover it with. No roots are left in store and "
			"the labs are not down for more than the order needs, so the only "
			"ways to close this are a further purchase or a smaller season."))
	if cover:
		lines.append("")
		lines.append(_("Proposed, no week loaded past {0}% of what it was "
		               "already due:").format(round(band * 100, 1)))
		for c in cover[:12]:
			lines.append("    %s   +%s" % (c["week_iso"],
			                               "{:,}".format(c["plants"])))
		if len(cover) > 12:
			lines.append(_("    ... and {0} more weeks").format(len(cover) - 12))
	if left:
		lines.append("")
		lines.append(_(
			"{0} plants still cannot be placed. Either the forward weeks have "
			"no room inside the band, or there is not the material to fill "
			"them."
		).format("{:,}".format(left)))
	lines.append("")
	lines.append(_("Nothing has moved. Accept the re-plan to take it in."))
	return "\n".join(lines)


@frappe.whitelist()
def accept(propagation_plan):
	"""Take the proposal in, as records the plan reads back like any other.

	An accepted re-plan is a decision about forward weeks, so it is written the
	same way an arrival is -- its own submitted records -- rather than typed
	into the plan's tables, which the next rebuild would clear. Cancelling them
	undoes the re-plan.
	"""
	doc = frappe.get_doc("Summer Flower Propagation Plan", propagation_plan)
	result = propose(doc)
	if not result or not result["cover"]:
		frappe.throw(_("There is nothing to accept: no shortfall has been "
		               "carried, or nothing can cover it."))

	made = []
	for c in result["cover"]:
		a = frappe.new_doc("Summer Flower Propagation Actual")
		a.propagation_plan = doc.name
		a.event = UPLIFT
		a.form = "Plants"
		a.actual_date = nowdate()
		a.against_week = c["week"]
		a.plants = cint(c["plants"])
		a.remarks = _("Covering {0} plants short across weeks that have closed."
		              ).format("{:,}".format(result["short"]))
		a.flags.ignore_permissions = True
		a.insert()
		a.submit()
		made.append(a.name)

	doc.reload()
	doc.flags.ignore_permissions = True
	doc.save()
	return {"accepted": len(made), "plants": result["covered"], "names": made}


def uplifts(doc):
	"""Accepted re-plan cover, by the week it was added to."""
	if not (doc.variety and cint(doc.season_start_year)):
		return {}
	out = {}
	for a in frappe.get_all(
			"Summer Flower Propagation Actual",
			filters={"variety": doc.variety,
			         "season_start_year": cint(doc.season_start_year),
			         "event": UPLIFT, "docstatus": 1},
			fields=["against_week", "plants", "name"]):
		if not a.against_week:
			continue
		slot = out.setdefault(_d(a.against_week), {"plants": 0, "names": []})
		slot["plants"] += cint(a.plants)
		slot["names"].append(a.name)
	return out
