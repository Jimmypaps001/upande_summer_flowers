# Copyright (c) 2026, James Kiruga and contributors
"""Who supplies the material, in how many cycles, and what that does to the plan.

A tissue culture house does not deliver a week's plants in that week. It takes
an order and hands it over in cycles -- "four lifts, the first at seven weeks,
the rest a week apart" -- so part of every consignment lands after the week it
was ordered for. That carry is not waste: it is next week's plants, already
bought. Buying as though every week stood alone orders the same plants twice.

This module answers, for a chosen set of suppliers and their stated cycles:
what has to be ordered each week once the carry is counted, when it lands, and
which planting weeks are still short. Nothing here is saved; it is for trying
suppliers against each other before anything is committed to.
"""

import datetime
import math

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

DEFAULT_FORM = "Tissue Culture"

# Two different fours, which were one constant until they were told apart.
# DEFAULT_CYCLE_COUNT is a GAP IN WEEKS -- a delivery every four weeks, not
# four deliveries in a year. DEFAULT_LIFT_COUNT is how many lifts one
# consignment is handed over in: four, a week apart, the bulk in the first.
# A supplier who works differently is edited on screen; this is only the shape
# to start the conversation from.
DEFAULT_CYCLE_COUNT = 4
DEFAULT_LIFT_COUNT = 4
DEFAULT_CYCLE_SHARES = (55.0, 25.0, 12.0, 8.0)
DEFAULT_FIRST_WEEK = 7


def default_cycles(count=DEFAULT_LIFT_COUNT, first_week=DEFAULT_FIRST_WEEK):
	"""`count` LIFTS a week apart, front-loaded, adding to a hundred.

	Named for cycles and kept so because the page reads the key, but what it
	returns is a lift profile: how one consignment is handed over, never how
	often a consignment comes."""
	count = max(1, cint(count))
	if count == len(DEFAULT_CYCLE_SHARES):
		shares = list(DEFAULT_CYCLE_SHARES)
	else:
		# Halving weights, normalised: the first lift is always the big one.
		raw = [1.0 / (2 ** i) for i in range(count)]
		total = sum(raw)
		shares = [round(x * 100.0 / total, 2) for x in raw]
		shares[-1] = round(100.0 - sum(shares[:-1]), 2)
	return [{"lift_no": i + 1, "weeks_after": cint(first_week) + i,
	         "share_pct": shares[i]} for i in range(count)]


def _iso(d):
	if not d:
		return None
	y, w, _dow = getdate(d).isocalendar()
	return "%s-W%02d" % (y, w)


def _monday(d):
	d = getdate(d)
	return add_days(d, -d.weekday())


def _week_edge(d):
	"""The Monday of the week `d` belongs to, counted the way ISO counts.

	Rounding DOWN to Monday would put the 1st of January in the old year
	whenever it fell on a Thursday, which is the one case ISO puts it in the
	new one. Mon-Thu belong to the week that has already started; Fri-Sun to
	the one about to. For a year opening on 1 January this returns ISO week 1
	exactly, and it keeps doing something sensible for a financial year that
	opens on any other day."""
	d = getdate(d)
	return add_days(d, -d.weekday() if d.weekday() <= 3 else 7 - d.weekday())


def financial_halves():
	"""(Monday it opens, what to call it) for every half of every financial
	year on file, in order.

	The halves are the FINANCIAL year's, not the calendar's. A farm reviews
	an order against the year it reports on, and where those two are the same
	-- as they are here, 1 January to 31 December -- this is the ISO week 1
	and week 27 everyone already expects. Where a site runs a year to June,
	the halves move with it and nothing else has to know.

	The odd week of a 53-week year falls in the second half, which is where
	counting 53 // 2 = 26 weeks from the start of the first one puts it."""
	out = []
	for r in frappe.get_all("Fiscal Year",
	                        fields=["name", "year_start_date", "year_end_date"],
	                        order_by="year_start_date"):
		if not (r.year_start_date and r.year_end_date):
			continue
		opens = _week_edge(r.year_start_date)
		# Where the NEXT year opens, which is how long this one is in weeks.
		closes = _week_edge(add_days(getdate(r.year_end_date), 1))
		weeks = (closes - opens).days // 7
		if weeks < 2:
			continue
		out.append((opens, "%s H1" % r.name))
		out.append((add_days(opens, 7 * (weeks // 2)), "%s H2" % r.name))
	return sorted(out)


def _calendar_halves(lo, hi):
	"""The same thing off the calendar, for a span the Fiscal Year table does
	not reach. ISO week 1 and ISO week 27, named for the year they fall in."""
	out = []
	y = getdate(lo).isocalendar()[0] - 1
	while True:
		for w in (1, 27):
			try:
				d = datetime.date.fromisocalendar(y, w, 1)
			except ValueError:
				continue
			out.append((d, "%s H%d" % (y, 1 if w == 1 else 2)))
		if datetime.date.fromisocalendar(y, 1, 1) > getdate(hi):
			break
		y += 1
	return sorted(out)


def half_year_periods(lo, hi):
	"""Every half-year boundary the span lo..hi sits in or crosses.

	The financial year decides it. Only where the Fiscal Year table does not
	reach the whole span -- a plan running past the last year anybody has
	opened -- does the calendar stand in, because a plan still has to be
	halved somewhere."""
	lo, hi = getdate(lo), getdate(hi)
	fy = financial_halves()
	if not fy or fy[0][0] > lo or fy[-1][0] < hi:
		fy = _calendar_halves(lo, hi)
	# The first period opens where the plan does, carrying the name of the
	# half it falls in: the plan has nothing to say about the weeks before it.
	before = [x for x in fy if x[0] <= lo]
	out = [(lo, before[-1][1] if before else "%s H%d" % lo.isocalendar()[:2])]
	out += [x for x in fy if lo < x[0] <= hi]
	return out


def stated_lifts(supplier=None, form=DEFAULT_FORM):
	"""The LIFTS a supplier hands one consignment over in, as Settings states.

	Not cycles. How many weeks a house leaves between deliveries is a
	commercial arrangement and nothing on file states it; how one consignment
	is then handed over, in lifts a week or two apart, is this. The two were read off the same table,
	so Iribov's two lifts were offered as "Cycles 2" and the card proposed a
	delivery rhythm the supplier had never agreed to."""
	rows = frappe.get_all(
		"Summer Flower Handover Wave",
		filters={"parent": "Summer Flower Settings", "form": form},
		fields=["source", "lift_no", "weeks_after_arrival", "share_pct"],
		order_by="lift_no asc")
	# A supplier's OWN profile, where one is on file. Where none is, four
	# cycles -- not the site's generic two, which is a different claim: that
	# every house works the same way.
	mine = [r for r in rows if supplier and r.source == supplier]
	if not mine and not supplier:
		mine = [r for r in rows if not r.source]
	if not mine:
		return []
	return [{"lift_no": cint(r.lift_no) or i + 1,
	         "weeks_after": cint(r.weeks_after_arrival),
	         "share_pct": flt(r.share_pct)}
	        for i, r in enumerate(mine)]


@frappe.whitelist()
def suppliers_for(form=DEFAULT_FORM):
	"""Every supplier that could sell this form, with the cycles it states."""
	named = {r.source for r in frappe.get_all(
		"Summer Flower Handover Wave",
		filters={"parent": "Summer Flower Settings", "form": form},
		fields=["source"]) if r.source}
	out = []
	for name in sorted(named):
		# The lift profile is what is on file for this house. It is offered for
		# the reader to see, never as a delivery rhythm: nobody has said how
		# many weeks this house leaves between deliveries, so nothing here
		# says it either.
		out.append({"supplier": name, "lifts": stated_lifts(name, form),
		            "cycle_count": DEFAULT_CYCLE_COUNT, "stated": 1})
	return {"form": form, "default_cycles": default_cycles(),
	        "default_cycle_count": DEFAULT_CYCLE_COUNT,
	        "suppliers": out,
	        # Any supplier can be used; these are only the ones with a lift
	        # profile on file. Having a profile is not being chosen -- the card
	        # opens on nobody, because who supplies a season is a decision the
	        # farm makes and not one the settings table has already made.
	        "others": frappe.get_all("Supplier", filters={"disabled": 0},
	                                 pluck="name", order_by="name", limit=400)}


def protocol_suppliers(version, default_cycle=0, default_lead=0):
	"""The houses the protocol names for this variety, each with its own cycle.

	A cycle belongs to a house, not to a variety. Two houses supplying the
	same Craspedia can be on a four-week rhythm and a twelve-week one, and
	the single figure the protocol used to carry could only ever be one of
	them -- so a plan drawn on it either hurried one house or starved the
	other. This reads the table, keeps the row that names this farm over the
	row that names none, and fills the blanks from the protocol's own figures
	so a half-typed row is still usable.

	Rows with no supplier, and rows for another farm, are not here."""
	v = (version if hasattr(version, "get")
	     else frappe.get_cached_doc("Crop Protocol Version", version))
	rows = _supplier_rows(v)
	farm = v.get("farm")
	best = {}
	for i, r in enumerate(rows):
		name = (r.get("supplier") or "").strip()
		if not name:
			continue
		at = (r.get("farm") or "").strip()
		if at and farm and at != farm:
			continue
		# A row naming this farm is this farm's arrangement; a blank row is the
		# standing one. Keeping both would plan the same house twice.
		if name in best and not at and best[name]["farm"]:
			continue
		best[name] = {
			"supplier": name,
			"farm": at or None,
			"cycle_weeks": max(0, min(52, cint(r.get("cycle_weeks"))
			                          or cint(default_cycle))),
			"lead_weeks": cint(r.get("lead_weeks")) or cint(default_lead),
			"share_pct": flt(r.get("share_pct")),
			"min_order_qty": cint(r.get("min_order_qty")),
			"first_week": (r.get("first_delivery_week") or "").strip() or None,
			"preferred": cint(r.get("preferred")),
			"idx": cint(r.get("idx")) or i + 1,
		}
	out = sorted(best.values(), key=lambda x: (not x["preferred"], x["idx"]))
	# The share is ours to give. Typed shares are scaled to a hundred; where
	# none are typed the order is split evenly, which is a starting point.
	total = sum(flt(x["share_pct"]) for x in out)
	if out and total <= 0:
		for x in out:
			x["share_pct"] = round(100.0 / len(out), 4)
	elif out and abs(total - 100.0) > 0.01:
		for x in out:
			x["share_pct"] = round(flt(x["share_pct"]) * 100.0 / total, 4)
	return out


def _supplier_rows(v):
	"""The protocol's supplier rows, from the version or else its master."""
	rows = list(v.get("suppliers") or [])
	# A version taken before the table existed carries none, and re-approving
	# every protocol to teach it who supplies it would be a poor trade. The
	# master is read directly in that case -- these are commercial facts, and
	# the version is a snapshot of the agronomy.
	if not rows and v.get("crop_protocol"):
		rows = frappe.get_all(
			"Summer Flower Protocol Supplier",
			filters={"parent": v.crop_protocol,
			         "parenttype": "Crop Protocol",
			         "parentfield": "custom_sf_suppliers"},
			fields=["supplier", "farm", "cycle_weeks", "lead_weeks", "share_pct",
			        "min_order_qty", "first_delivery_week", "preferred", "idx"],
			parent_doctype="Crop Protocol", order_by="idx asc")
	return rows


def unnamed_rhythm(version):
	"""What the protocol's supplier rows say when they name nobody.

	A row with the cycle and lead typed but the house left blank is still the
	farm saying "this comes every five weeks, fifteen weeks after ordering".
	protocol_suppliers() leaves such rows out, because a plan cannot buy from
	nobody, and for months that meant 3359 was planned as though its
	protocol said nothing at all. Read here as the same assumption the two
	protocol-wide figures make: N interchangeable houses on the shortest gap."""
	v = (version if hasattr(version, "get")
	     else frappe.get_cached_doc("Crop Protocol Version", version))
	farm = v.get("farm")
	rows = [r for r in _supplier_rows(v)
	        if not (r.get("supplier") or "").strip()
	        and not ((r.get("farm") or "").strip() and farm
	                 and (r.get("farm") or "").strip() != farm)]
	gaps = [max(0, min(52, cint(r.get("cycle_weeks")))) for r in rows]
	gaps = [g for g in gaps if g]
	leads = [cint(r.get("lead_weeks")) for r in rows if cint(r.get("lead_weeks"))]
	return {"houses": len(rows) if gaps else 0,
	        "cycle_weeks": min(gaps) if gaps else 0,
	        "lead_weeks": max(leads) if leads else 0}


def delivery_weeks(houses, earliest, until, order_from=None, anchor=None):
	"""Every week one of these houses could put material on the ground.

	A house comes on a rhythm and not before its lead time is up, so the
	weeks it can serve are: the first one it could reach, and then every
	`cycle_weeks` after it. `earliest` is the first week the plan would
	plant in, `until` the last; `order_from` is the day the farm could place
	an order, which is today for a plan being drawn now.

	Returned as {Monday: [supplier, ...]}, because two houses on different
	rhythms land on the same week often enough to matter.

	A house with no cycle stated is on no rhythm at all: it can serve any
	week from its first. Saying otherwise would invent a timetable.

	`anchor` is the week the farm most wants served -- normally the one the
	first demand has to be cut in. The supplier states the GAP between its
	deliveries and the notice it needs; which particular week the rhythm
	starts on is not its to state, and taking it to be "as early as possible"
	made the phase an accident of the day the plan was drawn. The beat is
	counted out from the anchor instead, forward in whole cycles until it
	clears the lead time, so the same plan drawn next week keeps the same
	weeks. A house with a first delivery week on file overrules all of it.

	The lead time bars the weeks still to be ORDERED FOR, not the whole span.
	A plan whose season opened last winter is not ordering for last winter;
	whatever came then came. Barring those weeks too emptied every plan drawn
	over a season already under way -- eleven of them here -- and said the
	reason was a notice period nobody could have given retrospectively. So
	the beat runs across the whole window and the lead takes out one stretch:
	from the day the order would be placed to the day it could arrive."""
	first = _monday(earliest)
	last = _monday(until)
	if not first or not last or last < first:
		return {}
	order_day = _monday(order_from or nowdate())
	at_anchor = _monday(anchor) if anchor else None
	out = {}
	for h in houses or []:
		# Nothing ordered now can land before the house has had its lead time.
		# A plan that plants in week three on twelve weeks' lead is a plan
		# nobody can supply -- but only if week three is still ahead of us.
		lead = max(0, cint(h["lead_weeks"]))
		ready = add_days(order_day, 7 * lead)
		barred = (lambda at: order_day <= at < ready) if lead else (lambda at: False)
		stated = _week_monday(h.get("first_week")) if h.get("first_week") else None
		step = cint(h["cycle_weeks"])
		# Where a house sits inside the cycle. Two houses on the same four-week
		# rhythm are not one house that comes twice as often unless they are
		# staggered, and houses all put on the same beat was why a protocol
		# saying "two houses, every four weeks" planned exactly like one.
		phase = cint(h.get("phase_weeks")) % step if step else 0
		if stated:
			start = max(first, _monday(stated))
		elif at_anchor and step:
			# Whole cycles from the anchor to the first week of the window, so
			# the anchor week itself stays on the beat. The count runs
			# BACKWARDS as readily as forwards: the rhythm did not begin on
			# the week we happened to ask about it.
			base = add_days(at_anchor, 7 * phase)
			behind = (first - base).days
			hops = -(-behind // (7 * step))
			start = add_days(base, 7 * step * hops)
		else:
			start = add_days(first, 7 * phase)
		if start > last:
			continue
		at = start
		gap = 7 * (step or 1)
		while at <= last:
			if not barred(at):
				out.setdefault(at, []).append(h.get("label") or h["supplier"])
			at = add_days(at, gap)
	return dict(sorted(out.items()))


def default_houses(defaults, phases=None):
	"""The houses a protocol implies but does not name, from its two numbers.

	A protocol that says "two houses, every four weeks" has said something
	real about how this material arrives, and a plan drawn deaf to it is a
	plan drawn on a rhythm nobody has. It is an ASSUMPTION and says so --
	nobody has typed these houses -- but it is the farm's own assumption and
	it is the right place for a first plan to start.

	The houses are spread evenly round the cycle, because that is what having
	more than one of them buys: two houses on a four-week rhythm, staggered,
	put material down every second week. Stacking them on the same beat makes
	a second house worth nothing, which is how the figure came to look useless.

	More houses than there are weeks in the cycle cannot beat weekly, so the
	count is capped there rather than inventing two deliveries in one week.

	`phases` overrides where in the cycle each house sits. An even spread is
	the obvious arrangement and usually the right one, but it is not always
	the one that covers the most weeks -- see stagger_patterns()."""
	cycle = max(0, min(52, cint(defaults.get("cycle_weeks"))))
	if not cycle:
		# No rhythm stated is not a rhythm of one: it is silence, and silence
		# constrains nothing.
		return []
	count = max(1, min(cint(defaults.get("suppliers")) or 1, cycle))
	lead = cint(defaults.get("lead_weeks"))
	step = cycle / float(count)
	if phases is None:
		phases = [int(round(i * step)) for i in range(count)]
	return [{
		# Not a Supplier link: nobody has been named, and writing "House 2"
		# into a field that points at the Supplier master would not save.
		"supplier": "",
		"label": _("Supplier {0}").format(i + 1),
		"named": False,
		"farm": None,
		"cycle_weeks": cycle,
		"lead_weeks": lead,
		"share_pct": round(100.0 / count, 4),
		"min_order_qty": 0,
		"first_week": None,
		"preferred": 0,
		"idx": i + 1,
		"phase_weeks": cint(phases[i]) if i < len(phases) else int(round(i * step)),
		# Nobody typed this house. Everything that reports on it has to say so.
		"assumed": True,
	} for i in range(count)]


def stagger_patterns(cycle, count):
	"""Ways to arrange `count` houses round a `cycle`-week rhythm, best first.

	An even spread is the arrangement anyone would describe: two houses on a
	four-week cycle, a fortnight apart. It is also, for some crops, the worst
	one available -- and for a reason nothing about the supply makes obvious.

	A crop whose flushes are an even number of weeks apart, planted only in
	even weeks, can only ever cut in even weeks. SFR-TCPLANTS flushes at 12
	and 14 weeks after planting; two houses a fortnight apart leave 26 of its
	52 weeks short, every odd week of the year, and no amount of ordering
	more plants fixes it. The same two houses a WEEK apart -- 0, 1, 4, 5, 8,
	9 -- leave none short at all, because that set of weeks reaches both
	halves of the year.

	So the arrangements are offered as candidates and the caller picks the
	one that covers the most demand. Even spread leads, and wins every tie,
	because where it costs nothing it is the one to ask a supplier for."""
	cycle = max(1, cint(cycle))
	count = max(1, min(cint(count), cycle))
	step = cycle / float(count)
	even = [int(round(i * step)) for i in range(count)]
	out = []
	for p in (even,
	          # Consecutive weeks: the houses come back to back and then the
	          # cycle waits. Lumpy, and it is the one that breaks a parity.
	          list(range(count)),
	          # Even, but every other house nudged a week off the beat.
	          [(v + (i % 2)) % cycle for i, v in enumerate(even)]):
		p = sorted({v % cycle for v in p})
		if len(p) == count and p not in out:
			out.append(p)
	return out


def offsets_to_try(step, cap=16):
	"""The starting weeks worth trying for a house on a `step`-week cycle.

	Every one of them where the cycle is short. Where it is long the search
	would cost more than the answer is worth, so it is sampled evenly: a house
	on a 52-week cycle does not need all 52 tried to be put somewhere sensible.
	"""
	step = max(1, cint(step))
	if step <= cap:
		return list(range(step))
	stride = max(1, step // cap)
	return list(range(0, step, stride))


def choose_phases(houses, measure, rounds=2):
	"""Give each house a starting week so the set reaches the most demand.

	A supplier states a GAP between deliveries and a lead time. It does not
	state which week of the year its rhythm begins on -- that is ours to ask
	for. Left unasked every house started on the same week, so three houses on
	three different cycles all landed together on the first beat and the plan
	was as thin as one house's, while the farm was paying for three.

	`measure` is handed the houses with their phases set and answers how good
	that arrangement is; bigger is better. It is passed in because what counts
	as good is the demand, which lives on the plan, not here.

	A house whose first delivery week IS stated is left where it is. That is a
	fact about an arrangement somebody made, not a free variable.
	"""
	free = [i for i, h in enumerate(houses)
	        if not h.get("first_week") and cint(h.get("cycle_weeks")) > 1]
	if not free or len(houses) < 2:
		return None
	best = [cint(h.get("phase_weeks")) for h in houses]

	def put(phases):
		for h, ph in zip(houses, phases):
			h["phase_weeks"] = cint(ph)
		return measure(houses)

	mark = put(best)
	for _round in range(max(1, cint(rounds))):
		moved = False
		# The longest cycle first: it lands least often, so where it falls
		# decides the most, and the shorter ones fill in around it.
		for i in sorted(free, key=lambda j: -cint(houses[j]["cycle_weeks"])):
			for ph in offsets_to_try(houses[i]["cycle_weeks"]):
				if ph == best[i]:
					continue
				trial = list(best)
				trial[i] = ph
				got = put(trial)
				if got > mark:
					mark, best, moved = got, trial, True
		# A second pass lets a house moved early improve now the others have
		# settled. It converges quickly, so it stops as soon as nothing moves.
		if not moved:
			break
	put(best)
	return best


def supply_defaults(version):
	"""What the PROTOCOL says about buying this material, before any plan.

	Two figures the farm states once per variety and farm, rather than typing
	into every plan: how many weeks a house delivering this form leaves
	between one delivery and the next, and how many houses can supply it at
	all. They are not lifts -- a lift is how one consignment is handed over, a
	cycle is how often a consignment comes -- and the two were read off the
	same table for months, which is why a house with two lifts on file was
	planned as a house that comes every two weeks.

	Both are advisory: they seed the card, and the farm overrules them on the
	plan. Zero means the protocol says nothing, which is not the same as zero
	suppliers."""
	from upande_summer_flowers.summer_flowers import route_ways, sourcing

	v = (version if hasattr(version, "material_route")
	     else frappe.get_cached_doc("Crop Protocol Version", version))
	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	form = route_ways.LIFT_FORM.get(entry)
	# Who brings it, each on its own rhythm. The table is the only place this
	# is stated: there were two numbers beside it saying the same thing for
	# the whole crop, and when they disagreed with the table nothing on screen
	# said which the plan obeyed. (It obeyed the table.)
	#
	# A row's cycle is a GAP IN WEEKS -- 4 means a house comes every four
	# weeks, so a 53-week year is thirteen deliveries, not four.
	houses_named = protocol_suppliers(
		v, default_lead=cint(decided.get("lead_weeks"))
		or cint(v.get("supplier_lead_weeks")))
	# The beat the plan can count on is the shortest gap among them; a slower
	# house lands on some of the same weeks and never on fewer.
	gaps = [h["cycle_weeks"] for h in houses_named if h["cycle_weeks"]]
	cycle_weeks = min(gaps) if gaps else 0
	houses = len(houses_named)
	lead_weeks = cint(decided.get("lead_weeks")) or cint(v.get("supplier_lead_weeks"))
	if not houses_named:
		# Rows that give a rhythm but no name are an assumption, not silence.
		u = unnamed_rhythm(v)
		cycle_weeks = cycle_weeks or u["cycle_weeks"]
		houses = houses or u["houses"]
		lead_weeks = lead_weeks or u["lead_weeks"]
	return {
		# Only TC and roots are handed over in lifts, so only they have a lift
		# form. A seed, tuber or bought-in-plant route fell back to the module
		# default and the card announced "Tissue Culture" over a crop raised
		# from seed. What is bought is the entry stage; say that.
		"form": form or entry or DEFAULT_FORM,
		"entry_stage": entry,
		# Nothing is bought on a route that starts in our own hands, so the
		# two figures are not asked for and not offered. A route that buys
		# seeds or plants outright IS bought, even though no lift profile
		# describes how they arrive -- saying "nothing here is bought in"
		# of a seed crop would be plainly untrue.
		"is_bought": bool(entry),
		"handed_over_in_lifts": bool(form),
		# Kept under the old key too: the page and the saved supply rows
		# still speak it, and both now mean weeks between deliveries.
		"cycle_weeks": cycle_weeks,
		"suppliers": max(0, min(12, houses)),
		# The named houses themselves, in the order the protocol lists them.
		# Empty where the protocol states only the two numbers, and the rest
		# of the module goes on treating the suppliers as interchangeable.
		# NOT "houses": proposal() uses that for the COUNT, and a key that is
		# a list in one function and an integer in the next is a trap.
		"named_houses": houses_named,
		"named": bool(houses_named),
		"lifts": len(v.get("lifts") or []),
		"lead_weeks": lead_weeks,
		# How long a plant may wait if the house comes on the stated rhythm:
		# the gap between deliveries, less the week it is planted in.
		"wait_weeks": max(0, cycle_weeks - 1) if cycle_weeks else 0,
	}


def _week_monday(week_iso):
	"""'2026-W12' -> the Monday of that week."""
	import re

	m = re.match(r"^(\d{4})-W(\d{1,2})$", str(week_iso or "").strip())
	if not m:
		return None
	try:
		return datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
	except ValueError:
		return None


def _drops(weeks, wait):
	"""Which of these planting weeks a delivery opens in.

	A delivery opens, and carries the planting weeks behind it until one of
	them would have waited longer than `wait` weeks on the bench."""
	out = []
	for w in weeks:
		if not out or (getdate(w) - getdate(out[-1])).days > 7 * max(0, cint(wait)):
			out.append(w)
	return out


def _apportion(total, parts):
	"""`total` split into `parts` whole numbers that still add to `total`."""
	parts = max(1, int(parts))
	base, rem = divmod(int(total), parts)
	return [base + (1 if i < rem else 0) for i in range(parts)]


def _share_out(total, shares):
	"""`total` split by percentage, to the plant, still adding to `total`."""
	total = int(total)
	raw = [total * flt(p) / 100.0 for p in shares]
	out = [int(x) for x in raw]
	for i in sorted(range(len(raw)), key=lambda i: -(raw[i] - out[i]))[:total - sum(out)]:
		out[i] += 1
	return out


def _lift_rows(plants, lifts, lift_total, lands):
	"""One row per lift: its share of this delivery and the week it is planted.

	A delivery row is named for the week its FIRST lift goes in the ground --
	that is how the plan schedules it, and how programme_from_supply plants
	it. Each lift is therefore that week plus its gap from the first lift.
	Adding the whole weeks-after-arrival again showed every lift seven weeks
	after the planting it was for."""
	if not lifts:
		return []
	scale = 100.0 if abs(lift_total - 100.0) <= 0.5 else lift_total
	parts = _share_out(plants, [x["share_pct"] * 100.0 / scale for x in lifts])
	first = min(cint(x["weeks_after"]) for x in lifts)
	return [{"lift_no": x["lift_no"], "weeks_after": x["weeks_after"],
	         "share_pct": round(x["share_pct"] * 100.0 / scale, 2),
	         "plants": parts[i],
	         "week": _iso(add_days(lands, 7 * (cint(x["weeks_after"]) - first)))}
	        for i, x in enumerate(lifts)]


# A half with less than this share of the season's plants is ordered with the
# half next to it rather than on its own.
SMALL_HALF_SHARE = 0.10


def proposal(plan_doc, version=None, shares=None, second_half=None, cycles=None):
	"""What the protocol says this plan will have to be delivered.

	A cycle is the GAP, not the count: cycle 4 means a house comes every four
	weeks, so a 53-week year is thirteen deliveries. The supplier takes the
	whole order and brings it in equal instalments on that rhythm.

	The order is worked in halves of the FINANCIAL year, read from the Fiscal
	Year table, so where a site runs its year to June the halves move with it.
	On a year of 1 January to 31 December they are ISO weeks 1 to 26 and 27 to
	the end -- the weeks the farm actually reviews on. A plan opening in week
	38 opens inside the second half of that year and is cut at the new year,
	not twenty-six weeks after whatever week its plantings happened to begin.

	That means a plan gets as many periods as financial halves it touches: two
	for most, three for a full year that starts mid-half, more for a plan that
	runs over several years -- which used to be handed one 79-week "half".

	A half is grouped on the week the TC has to be DELIVERED, not the week it
	is planted. The two part company at every boundary, because the
	consignment covering the first weeks of a half lands in the one before it:
	the order is placed, paid for and received against the delivery, so the
	delivery is what the half holds.

	Each half's quantity is the material the planting weeks inside it need,
	which is why a hundred thousand for the year can be forty in the first
	half. It is divided by the deliveries that actually land in that half,
	not by half the year's: twelve deliveries can be seven and five, and
	dividing both halves by six over-delivered the first and starved the
	second. At the midpoint the farm sees how the first half went, and a
	figure it agrees for the second half replaces the arithmetic.

	What a planting week happens to want no longer SIZES anything. It is still
	reported -- the week a delivery covers, what those weeks ask for -- because
	it is worth knowing, but the instalment is the instalment."""
	v = version or frappe.get_cached_doc("Crop Protocol Version",
	                                     plan_doc.protocol)
	d = supply_defaults(v)
	wanted = _wanted_by_week(plan_doc)
	weeks = sorted(wanted)
	# What each house was promised. Even where the card has said nothing --
	# but a share typed on the card is the share, and splitting the instalment
	# evenly regardless was why changing an allocation moved nothing.
	if isinstance(shares, str):
		shares = frappe.parse_json(shares or "[]")
	if isinstance(cycles, str):
		cycles = frappe.parse_json(cycles or "[]")
	# How many houses this plan is BUYING FROM, which is not always how many
	# the protocol names. The count came off the protocol while the card
	# iterated the houses the plan had picked, and the two were matched by
	# position: a plan buying from one of the two houses named got one share
	# of a two-way split and the other was simply lost, and before the houses
	# were named at all the count fell to one, so every picked house read the
	# same instalment and each brought the whole order. A caller that has
	# picked its houses has already answered this; only where it has picked
	# none does the protocol's count stand.
	picked = max(len(shares or []), len(cycles or []))
	houses = max(1, picked or cint(d["suppliers"]) or 1)
	shares = [flt(x) for x in (shares or [])][:houses]
	# Where the protocol names the houses, their own shares are the default --
	# an even split is only right when nobody has said otherwise.
	named = d.get("named_houses") or []
	if (len(shares) < houses or sum(shares) <= 0) and len(named) == houses:
		shares = [flt(h["share_pct"]) for h in named]
	if len(shares) < houses or sum(shares) <= 0:
		shares = [100.0 / houses] * houses
	step = cint(d["cycle_weeks"])
	# Each house keeps its own rhythm. The card carries a cycle per row, and a
	# number typed there has to move the schedule under it -- a box that does
	# nothing is worse than no box. Where a row says nothing, the protocol's
	# figure stands.
	cyc = [max(0, min(52, cint(c))) or step for c in (cycles or [])][:houses]
	# Each named house brings its own rhythm with it. Falling back to one
	# figure for all of them was the old shape, and it planned two houses on
	# four weeks and twelve as though both came every four.
	if len(named) == houses:
		cyc += [max(0, min(52, cint(h["cycle_weeks"]))) or step
		        for h in named[len(cyc):]]
	cyc += [step] * (houses - len(cyc))
	# A pool crop's TC is one build-up delivery, not a rhythm: every house that
	# brings part of it brings it that week.
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		_cut_from_a_pool as _pooled,
	)
	if _pooled(v):
		cyc = [0] * houses
	uniform = len(set(cyc)) <= 1
	# How a consignment is handed over. A lifted crop never gets the whole
	# delivery in one week: 90% at seven weeks, 10% at eight.
	lifts = [{"lift_no": cint(r.get("lift_no")) or i + 1,
	          "weeks_after": cint(r.get("weeks_after_arrival")),
	          "share_pct": flt(r.get("share_pct"))}
	         for i, r in enumerate(v.get("lifts") or [])]
	lift_total = sum(x["share_pct"] for x in lifts)
	if lifts and lift_total <= 0:
		lifts = []
	total = sum(cint(x) for x in wanted.values())
	if not weeks:
		d.update({"deliveries": 0, "weeks": [], "planting_weeks": 0, "plants": 0,
		          "houses": houses, "rows": [], "supplier_deliveries": 0,
		          "halves": None})
		return d

	# The drumbeat runs from the first planting week to the last, one delivery
	# every `step` weeks. No cycle stated means no rhythm to keep, so each
	# planting week is served on its own.
	first, last = weeks[0], weeks[-1]
	span_weeks = (getdate(last) - getdate(first)).days // 7 + 1

	def _beat(gap, phase=0):
		# How many whole cycles fit the span, which is the farm's own sum: a
		# 53-week year at a four-week cycle is thirteen deliveries, not the
		# fourteen you get by counting both ends of 0, 4, 8 ... 52. The last
		# delivery's window carries the weeks after it. No cycle stated means
		# no rhythm to keep, so each planting week is served on its own.
		if not gap:
			return list(weeks)
		phase = cint(phase) % gap
		return [add_days(first, 7 * (phase + gap * i))
		        for i in range(max(1, (span_weeks - phase) // gap))]

	# Which week each house BEGINS on. A supplier states the gap between its
	# deliveries, never the week its rhythm starts, so every house used to
	# open on the first planting week: three houses on three different cycles
	# all landed together and the plan waited as long as if one had come.
	# Started apart, the same three houses cover the same season with the
	# material arriving nearer the week it goes in the ground.
	import bisect

	def _covers(hs):
		"""Plants served, then how long they wait, then how spread it is."""
		at = sorted({getdate(x)
		             for h in hs
		             for x in _beat(cint(h["cycle_weeks"]), h["phase_weeks"])})
		if not at:
			return (0, 0, 0)
		served = waiting = 0
		for w in weeks:
			wd = getdate(w)
			i = bisect.bisect_right(at, wd)
			if not i:
				continue          # nothing has landed yet for this week
			served += cint(wanted[w])
			waiting += cint(wanted[w]) * ((wd - at[i - 1]).days // 7)
		return (served, -waiting, len(at))

	beat_houses = [{"cycle_weeks": g, "phase_weeks": 0,
	                "first_week": (named[i].get("first_week")
	                               if i < len(named) else None)}
	               for i, g in enumerate(cyc)]
	choose_phases(beat_houses, _covers)
	phases = [cint(h["phase_weeks"]) for h in beat_houses]

	# One beat per house, and the schedule is their union: a week where only
	# the second house comes is still a week something lands.
	house_drops = [_beat(g, p) for g, p in zip(cyc, phases)]
	# A house promised none of the order is not on the rota. It kept its slot
	# in the rhythm, so the weeks where it alone was due were handed to a
	# house with nothing to bring: three houses on four weeks, one of them on
	# a zero share, left a third of the drops carrying one plant and the plan
	# ordered 32% less material than it had to plant.
	house_drops = [[] if flt(shares[h]) <= 0 else hd
	               for h, hd in enumerate(house_drops)]
	lands = [set(_iso(x) for x in hd) for hd in house_drops]
	drops = sorted({getdate(x) for hd in house_drops for x in hd})

	# Halves of the CALENDAR year. Counting twenty-six weeks off the first
	# planting week made a half that began and ended on weeks nobody reviews
	# on: a plan opening in week 38 was told its first half ran to week 11 of
	# the next year. The farm settles an order in January and in July, so the
	# only weeks a period may start on are ISO week 1 and ISO week 27. The
	# 53rd week of a long year falls in the second half, where the calendar
	# puts it.
	#
	# A plan therefore gets one period per calendar half it touches, not
	# always two -- which also retires the other half of the same fault, a
	# plan running over two years quoting the farm a seventy-nine-week
	# instalment and calling it a half.
	periods = half_year_periods(min([getdate(first)] + drops[:1]),
	                            max([getdate(last)] + drops[-1:]))
	edges = [x[0] for x in periods]
	names = [x[1] for x in periods]
	# Bought in halves by default: each half is ordered on its own, so the
	# second can be revised once the first shows whether the plan is holding.
	# A plan bought once for the year has one period, and every delivery in it
	# brings the same share of the year.
	once = (plan_doc.get("supply_mode") or "") == "Once for the year"
	if once:
		edges, names = edges[:1], [_("Whole season")]
	# What the farm has settled for the second half, where it has settled
	# anything: the review at the half-year is a decision, and a decision
	# outranks the arithmetic that stood in for it.
	agreed_2nd = cint(second_half if second_half not in (None, "")
	                  else plan_doc.get("supply_second_half_plants"))

	def _in_half(seq, lo, hi):
		return [x for x in seq if getdate(x) >= getdate(lo)
		        and (hi is None or getdate(x) < getdate(hi))]

	bounds = [(e, edges[i + 1] if i + 1 < len(edges) else None)
	          for i, e in enumerate(edges)]

	# Which delivery brings each planting week's material: the last one to
	# land on or before it. Weeks that run before anything has arrived belong
	# to the first delivery, because that is the one that has to carry them.
	carried_by = [max(0, bisect.bisect_right(drops, getdate(w)) - 1)
	              for w in weeks]
	per_drop = [0] * len(drops)
	weeks_on = [0] * len(drops)
	for i, w in zip(carried_by, weeks):
		per_drop[i] += cint(wanted[w])
		weeks_on[i] += 1

	# Which period each delivery lands in. The delivery date is the thing
	# being grouped: a half is the TC the farm has to TAKE DELIVERY of in it.

	# Grouped on the delivery, not on the planting week. The two part company
	# at every year end, because the consignment that covers January lands in
	# December: counted on the planting week, the first half asked for
	# material nothing was due to bring and the half that WAS bringing it
	# showed none of it. The order is placed against the delivery, so the
	# delivery is what the half is. It also retires the rule that used to
	# hand an empty half's plants back to a full one -- a half with no
	# delivery in it now has no plants in it, by construction.
	def _tally(bounds_):
		of_ = [max(n for n, (lo, _hi) in enumerate(bounds_)
		           if getdate(dt) >= getdate(lo)) for dt in drops]
		want_ = [0] * len(bounds_)
		served_ = [0] * len(bounds_)
		for i, _dt in enumerate(drops):
			want_[of_[i]] += per_drop[i]
			served_[of_[i]] += weeks_on[i]
		return of_, want_, served_

	of_half, want, served = _tally(bounds)
	# A half carrying only a sliver of the season -- one small early planting,
	# say -- is not worth its own order: it is folded into the half next to
	# it, which is ordered with it. Six deliveries of 129 TC for a 640-plant
	# planting were a half of their own before this.
	season_total = sum(want) or 0
	while len(edges) > 1 and season_total:
		small = [n for n, w in enumerate(want) if w < SMALL_HALF_SHARE * season_total]
		if not small:
			break
		n = small[0]
		keep = n + 1 if n + 1 < len(edges) else n - 1
		lo_i, hi_i = min(n, keep), max(n, keep)
		names[lo_i] = "%s + %s" % (names[lo_i], names[hi_i])
		del edges[hi_i]
		del names[hi_i]
		bounds = [(e, edges[i + 1] if i + 1 < len(edges) else None)
		          for i, e in enumerate(edges)]
		of_half, want, served = _tally(bounds)
	if agreed_2nd and len(want) > 1 and not once:
		want[1] = agreed_2nd

	halves = []
	for n, (lo, hi) in enumerate(bounds):
		ds = _in_half(drops, lo, hi)
		plants = want[n]
		# Divided by the deliveries that actually land in this half. Dividing
		# by half the year's -- 13 deliveries read as 6.5 a half -- sent 7 x
		# (40,000 / 6.5) = 43,078 against an order of 40,000, which is why the
		# delivery schedule and the plan's plants to plant did not agree.
		over = len(ds) or 1
		# A house with no delivery in this half cannot bring its share of it,
		# so the houses that do come share the half between them. Holding the
		# absent house's share left that part of the half belonging to nobody.
		coming = [h for h in range(houses) if _in_half(house_drops[h], lo, hi)]
		total_share = sum(flt(shares[h]) for h in coming) or 0.0
		live_share = [(flt(shares[h]) * 100.0 / total_share if h in coming and total_share else 0.0)
		              for h in range(houses)]
		halves.append({
			# A plan whose plantings end before the half-year is up has an
			# empty second half. Its window still starts at the midpoint, so
			# it is shown closing there too rather than at a last planting
			# week that fell before it began.
			"from": _iso(lo),
			"to": (_iso(add_days(getdate(hi), -7)) if hi
			       else _iso(max([getdate(last), getdate(lo)] + drops[-1:]))),
			# Which half of which financial year this is, so the card can name
			# the period the farm reviews on instead of counting "half 1,
			# half 2" off whichever week a plan happens to open in.
			"label": names[n],
			"plants": plants,
			# The planting weeks this half's DELIVERIES cover, which is what
			# it is answerable for -- not the weeks that fall between its own
			# two dates, which somebody else's consignment may be bringing.
			"planting_weeks": served[n], "deliveries": len(ds),
			"split_over": over,
			"per_delivery": int(math.ceil(plants / over - 1e-9)) if plants else 0,
			# What each house brings each time it comes. Equal cycles make
			# these add up to per_delivery; unequal ones are why the figure
			# has to be stated per house at all.
			"per_house": [
				{"deliveries": len(_in_half(house_drops[h], lo, hi)),
				 "split_over": len(_in_half(house_drops[h], lo, hi)) or 1,
				 "per_delivery": (int(math.ceil(
					 plants * live_share[h] / 100.0
					 / (len(_in_half(house_drops[h], lo, hi)) or 1) - 1e-9))
				                  if plants and live_share[h] else 0)}
				for h in range(houses)],
			# Whether this half's figure was decided or worked out.
			"agreed": 1 if (n and agreed_2nd and not once) else 0,
		})

	# A period after the last delivery has nothing in it to order.
	while len(halves) > 1 and not halves[-1]["deliveries"] and not halves[-1]["plants"]:
		halves.pop()

	# A house will not break an order below its minimum, so an instalment
	# under that figure is one it cannot bring. Said here, where the figure is
	# worked out, rather than discovered when the order is placed.
	under = []
	for n, half in enumerate(halves):
		for h in range(houses):
			floor = cint(named[h].get("min_order_qty")) if h < len(named) else 0
			got = (half["per_house"][h]["per_delivery"]
			       if h < len(half["per_house"]) else 0)
			if floor and got and got < floor:
				under.append({"house": (named[h].get("label")
				                         or named[h]["supplier"]), "half": n + 1,
				              "per_delivery": got, "minimum": floor})

	rows = []
	today_ = getdate(_monday(nowdate()))
	for i, start in enumerate(drops):
		end = drops[i + 1] if i + 1 < len(drops) else None
		carried = [w for w in weeks if getdate(w) >= getdate(start)
		           and (end is None or getdate(w) < getdate(end))]
		half = of_half[i]
		here = _iso(start)
		# Who is actually due this week. Equal cycles used to be read as
		# "every house comes every time", which was true only while they all
		# started together -- and starting them apart is the whole point of
		# the stagger. Two houses on four weeks, a week apart, come on
		# alternate drops: giving each of them half of EVERY drop left the
		# half that fell on a week they do not visit belonging to nobody, and
		# the plan bought half what it needed. The instalment is shared among
		# the houses that land, and only those.
		due = [h for h in range(houses) if here in lands[h]]
		# Each house brings its own share of the half, spread evenly over its
		# own deliveries in that half: 60,000 over seven deliveries is 8,572
		# each, and two houses on 50% bring 4,286 each, whether they land the
		# same week or a week apart.
		split = [halves[half]["per_house"][h]["per_delivery"] if h in due else 0
		         for h in range(houses)]
		plants = sum(split)
		arrives = add_days(start, -7 * (min(cint(x["weeks_after"]) for x in lifts)
		                                if lifts else 0))
		lead_ = max([cint(named[h].get("lead_weeks")) for h in due if h < len(named)]
		            + [cint(d.get("lead_weeks"))])
		order_by = getdate(add_days(arrives, -7 * lead_))
		rows.append({
			"no": i + 1, "week": here, "half": half + 1,
			"plants": plants,
			# Who is actually due this week.
			"houses_landing": due,
			# What the weeks between this delivery and the next ask for.
			# Reported, not used to size the instalment.
			"wants": sum(cint(wanted[w]) for w in carried),
			"covers": [_iso(w) for w in carried],
			"planting_weeks": len(carried),
			"split": split,
			# The delivery broken into the parts it actually arrives in, and
			# the week each part lands. Without this a lifted crop read as
			# though the whole consignment turned into plants on the day it
			# was dropped.
			"lifts": _lift_rows(plants, lifts, lift_total, start),
			# When the material has to be on the farm: the first lift goes in
			# the ground this many weeks after it arrives.
			"arrives": _iso(arrives),
			# The week the order has to be placed, and whether that week has
			# already gone. A first plan meets demand in full even where its
			# early deliveries could no longer be ordered in time; the card
			# says which they are rather than leaving them looking orderable.
			"order_by": _iso(order_by),
			"past": 1 if order_by < today_ else 0,
		})
	d.update({"deliveries": len(drops), "weeks": [_iso(x) for x in drops],
	          "planting_weeks": len(weeks), "plants": total,
	          "houses": houses, "rows": rows, "halves": halves,
	          # How many consignments somebody has to agree and receive. With
	          # one rhythm every house turns up every time; with different
	          # rhythms it is each house's own count, added up.
	          "supplier_deliveries": sum(len(hd) for hd in house_drops),
	          "cycles_by_house": cyc,
	          # Which week each house was asked to begin on, so the card can
	          # say so and the saved schedule can keep it. Zero means "with
	          # the first planting", which is where they all used to start.
	          "phases_by_house": phases,
	          # Houses whose instalment on this rhythm is below the smallest
	          # order they accept. Empty where every house can bring its share.
	          "below_minimum": under,
	          "uniform_cycle": uniform,
	          # What the deliveries add up to: the halves' plants, rounded up
	          # to whole deliveries.
	          "plants_bought": sum(r["plants"] for r in rows),
	          # Deliveries whose order week has already passed.
	          "past_deliveries": sum(1 for r in rows if r["past"]),
	          "past_plants": sum(r["plants"] for r in rows if r["past"]),
	          "supply_mode": "Once for the year" if once else "In two halves"})
	return d


def proposal_note(plan_doc, version=None):
	"""The same thing in a sentence, for the plan to carry."""
	d = proposal(plan_doc, version)
	if not d["is_bought"]:
		return _("Nothing on this route is bought in, so there is no supply to "
		         "schedule.")
	if not d["deliveries"]:
		return _("Nothing to deliver to yet: this plan has no planting weeks.")
	named = d.get("named_houses") or []
	bits = [
		# Named houses are named. "Three suppliers available" was all the
		# protocol could say when it held a count instead of a table, and a
		# plan that cannot say who it is buying from cannot be checked.
		_("{0}").format(", ".join(h.get("label") or h["supplier"]
		                          for h in named)) if named
		else _("{0} supplier(s) available").format(d["suppliers"])
		if d["suppliers"] else _("no supplier count on the protocol"),
		_("; ").join(_("{0} every {1} wk").format(h.get("label") or h["supplier"],
		                                          h["cycle_weeks"] or "?")
		             for h in named) if named and len(set(
			h["cycle_weeks"] for h in named)) > 1
		else _("a delivery every {0} weeks").format(d["cycle_weeks"])
		if d["cycle_weeks"]
		else _("no cycle stated, so one delivery per planting week"),
		_("{0} lift(s) per consignment").format(d["lifts"]) if d["lifts"]
		else _("no lift profile"),
	]
	head = _("{0}: {1}. {2} deliveries x {3} supplier(s) = {4} consignments over "
	         "{5} planting weeks, {6} plants in all. Nobody has been named yet."
	         ).format(d["form"], "; ".join(bits), d["deliveries"], d["houses"],
	                  d["supplier_deliveries"], d["planting_weeks"], d["plants"])
	lines = []
	for n, h in enumerate(d.get("halves") or []):
		lines.append(_("{0}, {1} to {2}: {3} plants / {4} deliveries = "
		               "{5} each ({6} of them land in this half).").format(
			h.get("label") or _("Half {0}").format(n + 1), h["from"], h["to"],
			h["plants"], h["split_over"], h["per_delivery"], h["deliveries"]))
		if not d.get("uniform_cycle"):
			# Houses on different rhythms do not share one instalment, so one
			# figure for the delivery would be a figure nobody brings.
			lines.append("   " + "; ".join(
				_("supplier {0}: every {1} wk, {2} deliveries, {3} each").format(
					i + 1, (d.get("cycles_by_house") or [])[i] if i < len(
						d.get("cycles_by_house") or []) else "?",
					x["deliveries"], x["per_delivery"])
				for i, x in enumerate(h.get("per_house") or [])))
	for u in (d.get("below_minimum") or []):
		lines.append(_("{0} would bring {1} plants each time in half {2}, under "
		               "the {3} it will break an order for. Give it a longer "
		               "cycle, a bigger share, or buy from somebody else."
		               ).format(u["house"], u["per_delivery"], u["half"],
		                        u["minimum"]))
	for r in d["rows"]:
		lines.append("%s  %s  half %s  %s plants  (%s)" % (
			r["no"], r["week"], r["half"], r["plants"],
			" / ".join(str(x) for x in r["split"])))
	return head + "\n" + "\n".join(lines)


def _wanted_by_week(plan):
	"""{Monday of the planting week: plants that have to go in the ground}.

	From the plan's own plantings where it has any. Where it has none -- which
	is now the normal state of a draft, because nothing is planted until the
	supply is agreed -- from the demand itself. The houses are sized against
	what the market wants, not against a programme that does not exist yet."""
	pooled = _pool_build_up(plan)
	if pooled is not None:
		return pooled
	out = {}
	for b in plan.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		key = _monday(b.planting_date)
		out[key] = out.get(key, 0) + (cint(b.get("plants_to_plant")) or cint(b.plants))
	if out:
		return dict(sorted(out.items()))
	try:
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
			planting_requirement,
		)

		need, _from_demand = planting_requirement(plan)
		# What goes IN the ground. A buyer buys plants to plant, not plants
		# that will still be standing in six weeks.
		return {_monday(at): cint(to_plant) for at, _st, to_plant in need
		        if cint(to_plant)}
	except Exception:
		frappe.log_error(frappe.get_traceback(),
		                 "Demand sizing on %s" % plan.name)
		return {}


def _pool_build_up(plan):
	"""For a crop cut from our own mother stock: the one TC delivery that builds it.

	Asters, Phlox, Solidago and the like are bought as TC once (twice at
	most) to raise the mother stock, and every planting after that is cut
	from it. Sizing the supply card off the plantings asked a TC house for a
	consignment every planting week, which is not how these crops are bought.
	The build-up is the plan's TC order, arriving when it has to be on the
	farm to reach the first sticking week. A second delivery is added by hand
	where the farm tops the pool up.

	None for a crop that is not cut from a pool."""
	from upande_summer_flowers.summer_flowers import root_line as rl
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		_cut_from_a_pool,
	)

	if not plan.get("protocol"):
		return None
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	if not _cut_from_a_pool(v):
		return None
	units = (cint(plan.get("tc_plants_committed")) if cint(plan.get("tc_choice_committed"))
	         else 0) or cint(plan.get("tc_plants_to_order"))
	ordered = plan.get("tc_order_date_committed") or plan.get("tc_order_by_date")
	if not units or not ordered:
		return {}
	lead = cint(supply_defaults(v).get("lead_weeks"))
	arrives = _monday(add_days(getdate(ordered), 7 * lead))
	per_unit = flt(rl.plants_per_unit_at_plant(v)) or 1.0
	return {arrives: int(round(units * per_unit))}


def _normalised(picks, form, wanted=None, default_count=None):
	"""The chosen houses: their share of the season, and how often they bring it.

	A cycle is the GAP between deliveries: "every four weeks" is a consignment
	a month, each carrying the plants wanted between it and the next one. The share of each
	is suggested from the demand in its window and can be overruled, which is
	how a house that insists on equal drops is planned for."""
	weeks = sorted(wanted or {})
	out = []
	for p in picks or []:
		name = (p.get("supplier") or "").strip()
		if not name:
			continue
		typed = [c for c in (p.get("cycles") or []) if c]
		# What the protocol states for this variety beats the module's guess of
		# four: the farm has said how often this house comes.
		count = (cint(p.get("cycle_count")) or len(typed)
		         or cint(default_count) or DEFAULT_CYCLE_COUNT)
		count = max(1, min(52, count))
		# Weeks stated outright beat any drumbeat. This is how a delivery is
		# added or taken away one at a time: the card hands back the weeks it
		# is showing, with one more or one fewer in the list.
		stated = []
		for wk in (p.get("weeks") or []):
			at = _monday(wk) if not str(wk).strip().count("-W") else _week_monday(wk)
			if at:
				stated.append(getdate(at))
		out.append({
			"supplier": name,
			"share_pct": flt(p.get("share_pct")),
			"lead_weeks": cint(p.get("lead_weeks")),
			"cycle_count": count,
			"weeks": sorted(set(stated)),
			"typed": [flt(c.get("share_pct")) for c in typed][:count],
			# A quantity typed against one delivery. The share was the only
			# way to say "this one brings more": a percentage of a total that
			# moves whenever any other row is touched, which is no way to
			# agree a consignment with a house. A number typed here is the
			# number ordered.
			"typed_units": [cint(c.get("units")) if c.get("units") not in
			                (None, "") else None for c in typed][:count],
		})
	# The share is ours to give: how much of the season each house is promised.
	# Typed shares are kept and only scaled to add to a hundred; where none are
	# typed the season is split evenly, which is a starting point, not a rule.
	share = sum(flt(x["share_pct"]) for x in out)
	if out and share <= 0:
		for x in out:
			x["share_pct"] = round(100.0 / len(out), 4)
	elif out and abs(share - 100.0) > 0.01:
		for x in out:
			x["share_pct"] = round(flt(x["share_pct"]) * 100.0 / share, 4)

	# When each delivery lands, and what the season wants between it and the
	# next. The cycle IS the gap in weeks, so "4" is a delivery every four
	# weeks, counted from the first planting.
	if not weeks:
		return out
	first, last = weeks[0], weeks[-1]
	span_weeks = max(1, (getdate(last) - getdate(first)).days // 7 + 1)

	def _run(x, phase):
		"""Where this house's deliveries land, begun `phase` weeks in."""
		if x.get("weeks"):
			return [getdate(w) for w in x["weeks"]]
		gap = max(1, cint(x["cycle_count"]))
		phase = cint(phase) % gap
		return [add_days(first, 7 * (phase + gap * i))
		        for i in range(max(1, (span_weeks - phase) // gap))]

	# Which week each house begins on. The gap between a house's deliveries is
	# the supplier's; the week its rhythm starts is ours, and asking for none
	# put every house on the same beat -- three houses landing together, then
	# nothing for weeks, with the plants waiting on the bench in between.
	# A house whose weeks are stated outright is left exactly where it is.
	import bisect

	def _covers(hs):
		at = sorted({d for h, x in zip(hs, out)
		             for d in _run(x, h["phase_weeks"])})
		if not at:
			return (0, 0, 0)
		served = waiting = 0
		for w in weeks:
			wd = getdate(w)
			i = bisect.bisect_right(at, wd)
			if not i:
				continue
			served += cint((wanted or {}).get(w) or 0)
			waiting += cint((wanted or {}).get(w) or 0) * ((wd - at[i - 1]).days // 7)
		return (served, -waiting, len(at))

	beat = [{"cycle_weeks": max(1, cint(x["cycle_count"])), "phase_weeks": 0,
	         # Stated weeks are a fact, so that house is not moved.
	         "first_week": _iso(first) if x.get("weeks") else None}
	        for x in out]
	choose_phases(beat, _covers)
	for x, h in zip(out, beat):
		x["phase_weeks"] = cint(h["phase_weeks"])

	for x in out:
		# The cycle IS the gap in weeks: 4 means every four weeks, which is
		# thirteen deliveries in a 53-week year, not four. Over however long
		# the plan runs: two years on a four-week cycle is twenty-six
		# consignments, not four.
		step = max(1, cint(x["cycle_count"]))
		# A consignment lands on a week the farm is planting in. Counting the
		# drumbeat out from the first planting regardless of where the
		# plantings are put cycles on weeks with nothing to deliver to: a plan
		# planting in three tight pairs a year apart was given five cycles, two
		# of which carried nothing, covered nothing and were then dropped
		# again further down -- so the card asked for four, showed five and
		# ended on three. A cycle is started on the first planting week that is
		# at least a step on from the last one, so every cycle lands on a real
		# planting week and none of them is empty.
		if x.get("weeks"):
			# Stated outright. Kept as given, in order, so a week the farm
			# added by hand is not quietly moved onto the nearest planting.
			drops = list(x["weeks"])
		else:
			# The drumbeat, exactly as the card draws it: one delivery every
			# `step` weeks from the first planting week, as many whole cycles
			# as the span holds. Anchoring each cycle on "the next planting
			# week at least a step on" instead put the consignments on
			# different weeks from the schedule the farm agreed, so the plan
			# was saved against a timetable nobody had seen.
			span_weeks = (getdate(last) - getdate(first)).days // 7 + 1
			phase = cint(x.get("phase_weeks")) % step
			count = max(1, (span_weeks - phase) // step)
			drops = [add_days(first, 7 * (phase + step * i))
			         for i in range(count)]
		x["drops_in_plan"] = len(drops)
		x["weeks_between"] = step
		# What was asked for, beside what the plan's own shape allows. A
		# four-week cycle over a plan that only plants in three weeks is three
		# cycles, and saying so is better than quietly delivering a different
		# number.
		x["cycle_weeks"] = x["cycle_count"]
		x["cycles_in_plan"] = len(drops)
		cycles = []
		for i, at in enumerate(drops):
			nxt = drops[i + 1] if i + 1 < len(drops) else None
			covers = [w for w in weeks
			          if getdate(w) >= getdate(at)
			          and (nxt is None or getdate(w) < getdate(nxt))]
			plants = sum(cint(wanted[w]) for w in covers)
			cycles.append({"lift_no": i + 1, "lands": at, "lands_iso": _iso(at),
			               "covers_from": _iso(at),
			               "covers_to": _iso(covers[-1]) if covers else _iso(at),
			               "planting_weeks": len(covers),
			               "suggested_plants": plants})
		total_plants = sum(c["suggested_plants"] for c in cycles) or 1
		for i, c in enumerate(cycles):
			typed = x["typed"][i] if i < len(x["typed"]) else None
			c["suggested_pct"] = round(c["suggested_plants"] * 100.0 / total_plants, 2)
			c["share_pct"] = flt(typed) if typed not in (None, "") else c["suggested_pct"]
			units = (x["typed_units"][i] if i < len(x.get("typed_units") or [])
			         else None)
			c["typed_units"] = cint(units) if units not in (None, "") else None
		given = sum(flt(c["share_pct"]) for c in cycles)
		if given <= 0:
			for c in cycles:
				c["share_pct"] = round(100.0 / len(cycles), 4)
		elif abs(given - 100.0) > 0.01:
			for c in cycles:
				c["share_pct"] = round(flt(c["share_pct"]) * 100.0 / given, 4)
		for c in cycles:
			c["share"] = flt(c["share_pct"]) / 100.0
		x["cycles"] = cycles
	return out


def _half_instalment(prop, week_iso, house):
	"""A house's per-delivery quantity in the half this week falls in.

	For a delivery moved by hand off the card's weeks: it still brings that
	half's instalment."""
	at = _week_monday(week_iso)
	if not at:
		return None
	halves = prop.get("halves") or []
	for n, h in enumerate(halves):
		lo = _week_monday(h.get("from"))
		nxt = _week_monday(halves[n + 1].get("from")) if n + 1 < len(halves) else None
		if lo and at >= lo and (not nxt or at < nxt):
			rows = h.get("per_house") or []
			if house < len(rows):
				return cint(rows[house].get("per_delivery")) or None
	return None


@frappe.whitelist()
def plan_supply(plan, picks=None, form=None):
	"""What to order each week from each supplier, once the carry is counted."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	if isinstance(picks, str):
		picks = frappe.parse_json(picks or "[]")
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)

	from upande_summer_flowers.summer_flowers import route_ways, sourcing

	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	form = form or route_ways.LIFT_FORM.get(entry) or DEFAULT_FORM

	wanted = _wanted_by_week(doc)
	stated = supply_defaults(v)
	chosen = _normalised(picks, form, wanted,
	                     default_count=stated["cycle_weeks"])
	# How often a house comes is one question; how each consignment is then
	# handed over, in lifts weeks apart, is another. The agreement carried only
	# the first, so every lifted crop was planned as though the whole order
	# became plants the week it landed.
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	for s_ in chosen:
		s_["handover"] = [{"lift_no": i + 1, "weeks_after": cint(wk),
		                   "share_pct": round(flt(sh) * 100, 2)}
		                  for i, (wk, sh) in enumerate(
			                  ts.handover_for(form, s_["supplier"], 0, v) or [])]
		first = cint(s_["handover"][0]["weeks_after"]) if s_["handover"] else 0
		s_["weeks_to_a_plant"] = first
		for c in (s_.get("cycles") or []):
			c["weeks_after"] = first
	if not chosen:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": [], "weeks": [],
		        "reason": _("Choose at least one supplier to try.")}
	if not wanted:
		return {"plan": plan, "form": form, "entry_stage": entry,
		        "suppliers": chosen, "weeks": [],
		        "reason": _("This plan has no new plantings to supply.")}

	# One unit bought becomes this many plants in the ground, losses and all.
	# Taken from the same function the material card uses, so the two cards
	# cannot quote different orders for the same planting again.
	from upande_summer_flowers.summer_flowers import root_line as rl
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	per_unit = flt(rl.plants_per_unit_at_plant(v)) or (
		1.0 / (flt(v.cuttings_per_plant_required) or 1.0))
	need_total = sum(wanted.values())
	# A quantity typed on the material card is the quantity: the houses split
	# what was agreed to be bought, not what the demand asked for in a perfect
	# world. Where the two differ the weeks below say where it runs out.
	agreed_units = (cint(doc.get("tc_plants_committed"))
	                if cint(doc.get("tc_choice_committed")) else 0)
	buy_plants = (agreed_units * per_unit) if agreed_units else need_total

	# Every consignment this agreement puts on the ground: who brings it, when
	# it lands, what it carries and when it has to be ordered.
	landing, drops, orders = {}, [], []
	by_hand = {}
	for d in (doc.get("plan_deliveries") or []):
		at = _monday(d.week_start or _week_monday(d.week_iso))
		if not at:
			continue
		got = int(round(cint(d.units) * per_unit))
		by_hand[at] = cint(by_hand.get(at, 0)) + got
		landing[at] = cint(landing.get(at, 0)) + got
		drops.append({"supplier": d.supplier or _("by hand"), "by_hand": 1,
		              "cycle": None, "lands_iso": _iso(at), "lands": str(at),
		              "order_iso": None, "order_passed": 0,
		              "units": cint(d.units), "plants": got,
		              "covers_to": None, "planting_weeks": 0})

	# What a consignment of `plants` actually puts in the field, week by week.
	# A house that hands over in lifts does not deliver a planting, it delivers
	# the first lift of one; the rest follows a week or two later and belongs
	# to the weeks it lands in.
	def _spread(at, plants, waves):
		out, running = {}, 0
		first = cint(waves[0][0]) if waves else 0
		for i, (wk, share) in enumerate(waves or [(0, 1.0)]):
			got = (plants - running if i == len(waves) - 1
			       else int(round(plants * flt(share))))
			running += got
			if got <= 0:
				continue
			when = add_days(getdate(at), 7 * (cint(wk) - first))
			out[when] = cint(out.get(when, 0)) + got
		return out

	# Each house against its OWN share of each week, and its own cycles in the
	# order they land. A cycle is sized on what is still wanted of that share
	# -- not on the gross demand in its window -- because the tail of the
	# cycle before it has already landed in those weeks, and buying for them
	# twice is how a 10% lift came to be bought again as somebody else's 100%.
	#
	# Shares are kept because they are a decision: "Iribov brings 70% of the
	# season and ACME the rest" is how a farm arranges cover, and a sizing
	# that let whichever house landed first take the whole week would quietly
	# throw that arrangement away.
	# Each week's plants split between the houses so the parts add back to the
	# whole. Rounding each house's share on its own leaves a plant or two of
	# every week belonging to nobody, and a week one plant short is reported
	# as a week short -- which is how a plan with two houses on fifty each
	# could read "3 weeks short" against a week it covered exactly.
	split = {}
	for w, q in wanted.items():
		parts, given = [], 0
		for s_ in chosen:
			take = int(cint(q) * flt(s_["share_pct"]) / 100.0)
			parts.append([take, (cint(q) * flt(s_["share_pct"]) / 100.0) - take])
			given += take
		# The plants left over by flooring go to the largest remainders, which
		# is the one split that is both exact and stable.
		for i in sorted(range(len(parts)), key=lambda i: -parts[i][1])[:cint(q) - given]:
			parts[i][0] += 1
		split[w] = [p[0] for p in parts]

	# The instalment schedule: the same one the card draws, at the same shares,
	# so what is saved IS what was agreed. Each consignment used to be sized
	# from the planting weeks it carried -- a different model from the card's,
	# which meant the farm agreed instalments and the plan was built on
	# something else.
	prop = proposal(doc, v, shares=[flt(x["share_pct"]) for x in chosen],
	                cycles=[cint(x.get("cycle_count")) for x in chosen])
	instalment = {}
	for r in (prop.get("rows") or []):
		for k, q in enumerate(r.get("split") or []):
			instalment[(r["week"], k)] = cint(q)
	if prop.get("rows"):
		# The card's delivery weeks are the ones saved. A house whose weeks
		# were not stated takes the proposal's weeks outright, so the
		# consignments saved are the ones the card drew.
		weeks_sorted = sorted(wanted)
		for idx, s_ in enumerate(chosen):
			if s_.get("weeks"):
				continue
			mine_at = [getdate(_week_monday(r["week"])) for r in prop["rows"]
			           if idx in (r.get("houses_landing") or [])]
			if not mine_at:
				continue
			old = {c.get("lands_iso"): c for c in (s_.get("cycles") or [])}
			first_after = cint(s_.get("weeks_to_a_plant"))
			cycles_ = []
			for i, at in enumerate(mine_at):
				nxt = mine_at[i + 1] if i + 1 < len(mine_at) else None
				covers = [w for w in weeks_sorted if getdate(w) >= at
				          and (nxt is None or getdate(w) < nxt)]
				was = old.get(_iso(at)) or {}
				cycles_.append({
					"lift_no": i + 1, "lands": at, "lands_iso": _iso(at),
					"covers_from": _iso(at),
					"covers_to": _iso(covers[-1]) if covers else _iso(at),
					"planting_weeks": len(covers),
					"suggested_plants": sum(cint(wanted[w]) for w in covers),
					"share_pct": round(100.0 / len(mine_at), 4),
					"share": 1.0 / len(mine_at),
					"typed_units": was.get("typed_units"),
					"weeks_after": first_after})
			s_["cycles"] = cycles_
			s_["cycles_in_plan"] = s_["drops_in_plan"] = len(cycles_)
	# Where the farm has agreed a total on the material card, the instalments
	# give the shape and that total gives the size.
	bought_basis = flt(prop.get("plants_bought") or prop.get("plants"))
	scale = (flt(buy_plants) / bought_basis
	         if agreed_units and bought_basis else 1.0)

	for idx, s_ in enumerate(chosen):
		s_["waves"] = (ts.handover_for(form, s_["supplier"], 0, v) or [(0, 1.0)])
		mine = {w: split[w][idx] for w in wanted}
		covered = {}
		for c in sorted((s_.get("cycles") or []),
		                key=lambda x: getdate(_monday(x["lands"]))):
			at = getdate(_monday(c["lands"]))
			window = [w for w in mine
			          if getdate(w) >= at and (c.get("covers_to") is None
			                                   or _iso(w) <= c["covers_to"])]
			net = sum(max(0, cint(mine[w]) - cint(covered.get(w, 0)))
			          for w in window)
			# And big enough that the FIRST lift covers the week this
			# consignment is for. A house handing over 90/10 against a week
			# wanting 40,000 must be bought 44,445 for 40,000 to be there on
			# the day -- buying 40,000 puts 36,000 in the ground and leaves
			# the week 4,000 short with the balance arriving after it was
			# wanted.
			first_share = flt(s_["waves"][0][1]) if s_.get("waves") else 1.0
			first_need = (max(0, cint(mine[window[0]])
			                  - cint(covered.get(window[0], 0))) if window else 0)
			if first_need and 0 < first_share < 1:
				net = max(net, int(math.ceil(first_need / first_share - 1e-9)))
			# The instalment for this delivery and this house. A week added by
			# hand is not on the schedule and keeps the old reckoning from the
			# weeks it covers.
			share_of = instalment.get((c.get("lands_iso"), idx))
			# A house on a cycle brings its one quantity on every visit, even
			# a week the farm moved by hand.
			if share_of is None:
				share_of = _half_instalment(prop, c.get("lands_iso"), idx)
			if share_of is not None:
				net = int(round(share_of * scale))
			c["net_plants"] = max(0, net)
			c["instalment"] = 1 if share_of is not None else 0
			c["suggested_units"] = (int(math.ceil(c["net_plants"] / (per_unit or 1.0)
			                                      - 1e-9))
			                        if c["net_plants"] > 0 else 0)
			# Typed against this delivery: that is what is ordered, and the
			# weeks below are walked against what it really brings. A house
			# that will only send 50,000 is planned on 50,000, and the weeks
			# it leaves short say so instead of being quietly made up.
			if c.get("typed_units") is not None:
				c["net_plants"] = max(0, int(round(cint(c["typed_units"])
				                                   * (per_unit or 1.0))))
			for when, got in _spread(at, c["net_plants"], s_["waves"]).items():
				covered[when] = cint(covered.get(when, 0)) + got

	bought_total = 0
	for s_ in chosen:
		s_plants = buy_plants * flt(s_["share_pct"]) / 100.0
		for c in s_.get("cycles") or []:
			# Sized on what is still wanted where the demand is the guide. A
			# quantity typed on the material card still splits by share: that
			# is a decision, not an estimate.
			typed_units = c.get("typed_units")
			# The instalment is the quantity. Apportioning the agreed total by
			# each cycle's share of the demand in its window was the old model
			# answering a question the card no longer asks; where the farm has
			# agreed a total, the instalments were already scaled to it.
			plants = cint(c.get("net_plants"))
			# Up, never to the nearest: half a plantlet short of a bed is a
			# bed that cannot be planted, and rounding down put the supply
			# card one unit under the material card on every order.
			units = (int(math.ceil(plants / (per_unit or 1.0) - 1e-9))
			         if plants > 0 else 0)
			# Typed is typed. Working the units back out of the plants that
			# quantity makes would hand the reader a number one off the one
			# they had just entered.
			if typed_units is not None:
				units = cint(typed_units)
			c["units"], c["plants"] = units, plants
			at = _monday(c["lands"])
			# Ordered a lead time before it ARRIVES, and it arrives the first
			# lift's weeks before the planting week the consignment is named
			# for. Counting the lead back from the planting week dated every
			# order a lift late -- seven weeks on a TC crop.
			order_week = add_days(at, -7 * (cint(s_["lead_weeks"])
			                                + cint(s_.get("weeks_to_a_plant"))))
			c["order_iso"] = _iso(order_week)
			c["order_passed"] = 1 if getdate(order_week) < getdate(nowdate()) else 0
			if plants <= 0:
				continue
			# Landed in lifts, so the weekly walk below spends what has
			# really arrived. Counting the whole consignment at its first
			# lift said every week was covered that in fact was 10% short.
			for when, got in _spread(at, plants, s_["waves"]).items():
				landing[when] = cint(landing.get(when, 0)) + got
			bought_total += units
			c["order_iso"] = _iso(order_week)
			c["order_passed"] = 1 if getdate(order_week) < getdate(nowdate()) else 0
			drops.append({
				"supplier": s_["supplier"], "by_hand": 0, "cycle": c["lift_no"],
				"lands_iso": c["lands_iso"], "lands": str(at),
				"order_iso": c["order_iso"], "order_passed": c["order_passed"],
				"units": units, "plants": plants,
				"covers_to": c.get("covers_to"),
				"planting_weeks": cint(c.get("planting_weeks")),
			})
			orders.append({"supplier": s_["supplier"], "order_iso": c["order_iso"],
			               "units": units})
		s_["units"] = sum(cint(c.get("units")) for c in (s_.get("cycles") or []))
		s_["plants"] = sum(cint(c.get("plants")) for c in (s_.get("cycles") or []))
	drops.sort(key=lambda d: d["lands"])

	# Walk the planting weeks forward, spending what has landed. A consignment
	# carries the weeks between it and the next one, so plants wait on the
	# bench -- which is the cost of being delivered every four weeks.
	rows = []
	stock = []
	short_total = 0
	held_max = 0
	for when, want in wanted.items():
		came = 0
		for at in sorted([k for k in landing if getdate(k) <= getdate(when)]):
			got = cint(landing.pop(at))
			came += got
			stock.append((at, got))
		in_hand = sum(q for _at, q in stock)
		take = min(in_hand, want)
		left, waited = take, 0
		while left > 0 and stock:
			at, q = stock[0]
			use = min(q, left)
			waited = max(waited, (getdate(when) - getdate(at)).days // 7)
			left -= use
			if use >= q:
				stock.pop(0)
			else:
				stock[0] = (at, q - use)
		held_max = max(held_max, waited)
		short = max(0, want - take)
		short_total += short
		rows.append({
			"week_iso": _iso(when), "week_start": str(when),
			"wanted": want,
			"by_hand": cint(by_hand.get(when, 0)),
			"landed": came,
			"planted": take,
			"held_weeks": waited,
			"short": short,
			"stock_left": sum(q for _at, q in stock),
			"drops": [d for d in drops if d["lands"] == str(when)],
		})

	# Anything that lands after the last planting week is bought and not used.
	stranded = sum(cint(x) for x in landing.values()) + sum(q for _at, q in stock)
	by_supplier = {}
	for o in orders:
		b = by_supplier.setdefault(o["supplier"], {"orders": 0, "first_order": None})
		b["orders"] += 1
		if not b["first_order"] or (o["order_iso"] or "") < (b["first_order"] or "~"):
			b["first_order"] = o["order_iso"]

	wanted_total = sum(wanted.values())
	out = {
		"plan": plan, "form": form, "entry_stage": entry,
		"suppliers": [dict(s, **by_supplier.get(s["supplier"], {})) for s in chosen],
		"weeks": rows,
		"drops": drops,
		"plants_wanted": wanted_total,
		"plants_short": short_total,
		"units_bought": bought_total,
		"plants_stranded": int(stranded),
		"weeks_short": sum(1 for r in rows if r["short"]),
		"weeks_total": len(rows),
		"cover_pct": (round((wanted_total - short_total) * 100.0 / wanted_total, 2)
		              if wanted_total else 0),
		"plants_per_unit": round(per_unit, 6),
		# What the material card agreed to buy, where it has agreed anything,
		# and what that stands up against what the plantings want.
		"units_agreed": agreed_units,
		"plants_from_agreed": int(round(buy_plants)) if agreed_units else None,
		"plants_needed": need_total,
		# The longest a later cycle's plants wait on the bench before a planting
		# week wants them. A profile that carries two weeks into a crop planted
		# ten weeks apart is not a carry, it is a write-off.
		"longest_held_weeks": held_max,
		"deliveries": [{"name": d.name, "week_iso": d.week_iso,
		                "supplier": d.supplier, "units": cint(d.units),
		                "plants": cint(d.plants), "note": d.note}
		               for d in (doc.get("plan_deliveries") or [])],
		"confirmed": cint(doc.get("supply_confirmed")),
		"docstatus": doc.docstatus,
	}
	# What it does to the plantings already on the plan, which is the question
	# behind choosing a supplier at all.
	out["calendar"] = _calendar_effect(doc, out)
	return out


def _calendar_effect(doc, result):
	"""What the chosen suppliers do to the plantings already on the plan."""
	by_week = {r["week_iso"]: r for r in result.get("weeks") or []}
	out = []
	for b in doc.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		key = _iso(_monday(b.planting_date))
		w = by_week.get(key) or {}
		want = cint(b.get("plants_to_plant")) or cint(b.plants)
		# A week is served pro rata when it is short: every planting in it
		# takes the same cut, because the material does not know whose it is.
		served = (flt(w.get("planted", 0)) / flt(w.get("wanted"))
		          if flt(w.get("wanted")) else 1.0)
		lands = int(round(want * served))
		out.append({
			"row": b.name,
			"block": b.block,
			"planting_iso": key,
			"planting_date": str(b.planting_date),
			"plants_planned": want,
			"plants_supplied": lands,
			"short": max(0, want - lands),
			"first_cut_iso": ("%d-W%02d" % (cint(b.first_harvest_year),
			                                cint(b.first_harvest_week))
			                  if cint(b.first_harvest_year) and cint(b.first_harvest_week)
			                  else None),
			"from_stock": cint(w.get("planted")) - cint(w.get("landed")),
			"waited_weeks": cint(w.get("held_weeks")),
			"ordered": [{"supplier": x["supplier"], "units": x["units"],
			             "order_iso": x["order_iso"], "passed": x["order_passed"]}
			            for x in (w.get("drops") or [])],
		})
	return out


@frappe.whitelist()
def save_supply(plan, picks, confirm=0):
	"""Write the chosen suppliers and cycles onto the plan, and agree them.

	The plan cannot be submitted until this is agreed, because what the houses
	will actually hand over, and when, is the plan -- not what the demand asked
	for in a perfect world."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is already submitted. Its supply was agreed when it "
		               "was.").format(doc.name))

	out = plan_supply(plan, picks=picks)
	if not out.get("weeks"):
		frappe.throw(out.get("reason") or _("There is nothing to supply."))

	units_by = {s["supplier"]: cint(s.get("units")) for s in out["suppliers"]}
	plants_by = {s["supplier"]: cint(s.get("plants")) for s in out["suppliers"]}

	doc.set("plan_supply", [])
	for s in out["suppliers"]:
		for i, c in enumerate(s["cycles"]):
			doc.append("plan_supply", {
				"supplier": s["supplier"],
				"share_pct": flt(s["share_pct"]),
				"lead_weeks": cint(s["lead_weeks"]),
				# How often they come, kept as the number that was chosen. The
				# rows below are what it came to over THIS plan, which is
				# fewer, and reading the count off them told a buyer their
				# four-week cycle had become an eight-week one.
				"cycle_count": cint(s.get("cycle_count")),
				"cycle_no": cint(c["lift_no"]) or i + 1,
				"weeks_after": cint(c.get("weeks_after")),
				"cycle_share_pct": flt(c["share_pct"]),
				# The week it lands and the quantity agreed on it. Without
				# these a saved supply reopened on a fresh rhythm at freshly
				# sized quantities, so a delivery moved by hand, or a figure a
				# house had actually agreed to, was lost on the next visit.
				"lands_iso": c.get("lands_iso"),
				"units_typed": cint(c.get("typed_units") or 0),
				# Stated on the first cycle only: it is the supplier's figure,
				# not the cycle's, and repeating it reads as four orders.
				"units": cint(units_by.get(s["supplier"], 0)) if not i else 0,
				"plants": cint(plants_by.get(s["supplier"], 0)) if not i else 0,
			})
	# Every consignment, not only the shape of the agreement: a buyer wants the
	# list of drops, and a list that only exists on a dashboard is one nobody
	# can hand to anybody.
	doc.set("plan_consignments", [])
	for d in out.get("drops") or []:
		doc.append("plan_consignments", {
			"supplier": d["supplier"], "cycle_no": cint(d.get("cycle")),
			"lands_week": d.get("lands_iso"), "lands_date": d.get("lands"),
			"units": cint(d.get("units")), "plants": cint(d.get("plants")),
			"order_week": d.get("order_iso"), "covers_to": d.get("covers_to"),
			"planting_weeks": cint(d.get("planting_weeks")),
			"by_hand": cint(d.get("by_hand")),
		})
	doc.supply_units = cint(out["units_bought"])
	doc.supply_short_plants = cint(out["plants_short"])
	doc.supply_stranded_plants = cint(out["plants_stranded"])
	short = cint(out["plants_short"])
	doc.supply_note = _(
		"{0} supplying {1} plants in {2}. {3} to buy; {4} of {5} planting "
		"weeks covered{6}."
	).format(
		" and ".join(
			# A cycle is the gap between deliveries, so this number is weeks,
			# not deliveries a year. The note used to read "5 cycles a year"
			# beside a proposal that said "a delivery every 4 weeks", for the
			# same house on the same plan.
			"{} ({}%, a delivery every {} weeks)".format(
				s["supplier"], round(flt(s["share_pct"])), cint(s["cycle_count"]))
			for s in out["suppliers"]),
		"{:,}".format(cint(out["plants_wanted"])),
		_("{0} deliveries over the plan").format(
			sum(cint(s.get("drops_in_plan")) for s in out["suppliers"])),
		"{:,} {}".format(cint(out["units_bought"]), out["form"]),
		out["weeks_total"] - out["weeks_short"], out["weeks_total"],
		(_(", {0} plant short") if short == 1 else _(", {0} plants short")
		 ).format("{:,}".format(short)) if short else "")
	# An order week already behind us is a delivery that cannot happen on the
	# date shown. The rows knew (order_passed); the note never said so, and a
	# plan for next season went out with its first orders dated last year.
	late = [d for d in (out.get("drops") or []) if cint(d.get("order_passed"))]
	if late:
		doc.supply_note += " " + _(
			"{0} of {1} consignments should already have been ordered (first: "
			"{2}, needed by {3}); move them or the plantings behind them."
		).format(len(late), len(out.get("drops") or []),
		         late[0]["supplier"], late[0].get("order_iso"))
	# One place names the sellers. Choosing cycles for one house on this card
	# and buying from another on the procurement card is two decisions where
	# the farm only ever makes one, so what is agreed here is written through.
	proc_name = _write_sellers(doc, out)
	# And the bench, where the bench is ours. Agreeing a supply changes what
	# our own propagation unit has been asked to raise, and the draft plan
	# that says so was only rebuilt when somebody opened the procurement
	# card -- so the farm agreed a supply and its own unit went on showing
	# the quantities from the supply before it.
	if proc_name:
		try:
			proc = frappe.get_doc("Summer Flower Procurement Plan", proc_name)
			if proc.own_unit_rows():
				out["propagation"] = proc.sync_draft_propagation_plans()
		except Exception:
			# A propagation draft that will not build must not cost the farm
			# the supply it just agreed. It is logged and said plainly.
			frappe.log_error(frappe.get_traceback(), "propagation after supply")
			out["propagation"] = {"error": _("The draft propagation plan could "
			                                 "not be rebuilt; the supply is saved.")}

	if cint(confirm):
		doc.supply_confirmed = 1
		doc.supply_confirmed_on = frappe.utils.now()
		doc.supply_confirmed_by = frappe.session.user

	# And now the plan itself. Until this moment it had no new plantings at
	# all: a draft says what the market wants and what is already standing,
	# and stops there. The programme is drawn from the consignments just
	# written -- every lift of every one of them, each in the week it becomes
	# a plant the field can take -- so the plantings, the weekly grid, the
	# space and the material line are all answers to what was agreed.
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		rebuild_from_supply,
	)

	rebuild_from_supply(doc)
	out["settled"] = _settle_from_agreement(doc, out)
	doc.flags.ignore_validate_update_after_submit = True
	doc.save()
	out["plantings"] = len([b for b in doc.plan_blocks
	                        if cint(b.is_new_planting)])
	out["plants_planted"] = sum(cint(b.plants) for b in doc.plan_blocks
	                            if cint(b.is_new_planting))
	out["saved"] = 1
	out["confirmed"] = cint(doc.supply_confirmed)
	out["calendar"] = _calendar_effect(doc, out)
	out["note"] = doc.supply_note
	return out


def _settle_from_agreement(doc, out):
	"""Re-date the draft from what the houses actually agreed to.

	A draft is dated off the protocol -- what the CROP does, which is the only
	thing known before anybody has been asked. Once the houses and their cycles
	are agreed, what THEY do is the plan: their lead times and their lifts.

	Only the dates the agreement decides are moved. Which weeks are planted,
	and how much goes in each, is the demand's answer and no supplier's
	business -- a house that is slow moves the order earlier, it does not get
	to move the harvest."""
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		_cut_from_a_pool,
	)

	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	# A crop cut from our own standing pool is stuck off that pool. What a
	# house hands over feeds the pool; it does not date the sticking.
	if _cut_from_a_pool(v):
		return None
	# Where two houses hand over on different rhythms the binding one is the
	# slowest: the week is only supplied once the last of them has landed.
	weeks = max([cint(s.get("weeks_to_a_plant"))
	             for s in (out.get("suppliers") or [])] or [0])
	if not weeks:
		return None
	moved, rows = 0, 0
	for b in doc.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		rows += 1
		y, w, _dow = add_days(getdate(b.planting_date), -7 * weeks).isocalendar()
		if cint(b.sticking_year) != cint(y) or cint(b.sticking_week) != cint(w):
			moved += 1
		b.sticking_year, b.sticking_week = cint(y), cint(w)
	orders = sorted(d.get("order_iso") for d in (out.get("drops") or [])
	                if d.get("order_iso"))
	return {"weeks_to_a_plant": weeks, "rows": rows, "rows_moved": moved,
	        "order_week": orders[0] if orders else None,
	        "by_supplier": [{"supplier": s["supplier"],
	                         "lead_weeks": cint(s.get("lead_weeks")),
	                         "weeks_to_a_plant": cint(s.get("weeks_to_a_plant")),
	                         "lifts": len(s.get("handover") or [])}
	                        for s in (out.get("suppliers") or [])]}


def _write_sellers(plan_doc, out):
	"""Put the agreed houses on the draft procurement plan as its sellers."""
	name = frappe.db.get_value(
		"Summer Flower Procurement Plan",
		{"production_plan": plan_doc.name, "docstatus": 0}, "name")
	if not name:
		return None
	proc = frappe.get_doc("Summer Flower Procurement Plan", name)
	stage = proc.entry_stage or out.get("entry_stage")
	proc.set("purchase_allocation", [])
	for s in out.get("suppliers") or []:
		proc.append("purchase_allocation", {
			"entry_stage": stage,
			"supplier": s["supplier"],
			"share_pct": flt(s["share_pct"]),
			"lead_weeks": cint(s.get("lead_weeks")),
		})
	proc.flags.choosing_sellers = True
	proc.flags.ignore_permissions = True
	proc.save()
	return proc.name


@frappe.whitelist()
def set_consignment_supplier(plan, row, supplier=None):
	"""Hand one consignment to a different house.

	Which house brings a particular drop is a commercial fact that changes
	after the rhythm is agreed -- one lab is short that month, another takes
	it -- and it is not a reason to re-agree the whole supply. So this writes
	the one row and nothing else: no quantity moves, no week moves, and the
	schedule the plan was built on is untouched.

	A name is not checked against the Supplier list because the field never
	was one: a house the farm has not set up yet is still the house that is
	bringing it, and refusing the name would only send somebody to create a
	record before they could record what already happened.
	"""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("{0} has been submitted, so its consignments cannot be "
		               "handed to another house here.").format(doc.name))
	for c in (doc.get("plan_consignments") or []):
		if c.name != row:
			continue
		was = c.supplier
		now = (supplier or "").strip() or None
		if was == now:
			return {"row": row, "supplier": now, "changed": 0}
		# Straight to the row. Saving the plan would redraw the programme from
		# the supply, and the supply has not changed -- only who is bringing
		# one drop of it.
		c.db_set("supplier", now, update_modified=False)
		frappe.db.commit()
		return {"row": row, "supplier": now, "was": was, "changed": 1,
		        "note": _("{0} now brings the consignment landing {1}.").format(
			        now or _("nobody"), c.lands_week or "")}
	frappe.throw(_("That consignment is not on {0}.").format(doc.name))

@frappe.whitelist()
def clear_supply(plan):
	"""Un-agree the supply, so it can be chosen again."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is submitted.").format(doc.name))
	doc.supply_confirmed = 0
	doc.supply_confirmed_on = None
	doc.supply_confirmed_by = None
	# Un-agreeing takes the programme away with it: the plantings were the
	# consignments' own, and without them there is nothing holding them up.
	doc.set("plan_consignments", [])
	doc.set("plan_supply", [])
	from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
		_populate,
	)

	_populate(doc)
	doc.save()
	return {"plan": doc.name, "confirmed": 0,
	        "plantings": len([b for b in doc.plan_blocks
	                          if cint(b.is_new_planting)])}


@frappe.whitelist()
def set_supply_mode(plan, mode):
	"""Buy in two halves (the default) or once for the year, on this plan only."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	if mode not in ("In two halves", "Once for the year"):
		frappe.throw(_("Choose In two halves or Once for the year."))
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is already submitted.").format(doc.name))
	frappe.db.set_value("Summer Flower Production Plan", doc.name, "supply_mode", mode)
	return {"plan": doc.name, "supply_mode": mode}


@frappe.whitelist()
def saved_supply(plan):
	"""What the plan has agreed to, for the card to open on."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	picks = {}
	for r in (doc.get("plan_supply") or []):
		p = picks.setdefault(r.supplier, {"supplier": r.supplier,
		                                  "share_pct": flt(r.share_pct),
		                                  "lead_weeks": cint(r.lead_weeks),
		                                  "cycle_count": cint(r.get("cycle_count")),
		                                  "cycles": []})
		if r.get("lands_iso"):
			p.setdefault("weeks", []).append(r.lands_iso)
		p["cycles"].append({"lift_no": cint(r.cycle_no),
		                    "weeks_after": cint(r.weeks_after),
		                    "share_pct": flt(r.cycle_share_pct),
		                    "units": cint(r.get("units_typed")) or None})
	return {"plan": doc.name, "picks": list(picks.values()),
	        # What the protocol states about buying this material, and the
	        # deliveries that rhythm actually comes to on THIS plan, so the
	        # card opens on the farm's own agreement rather than on a module
	        # guess -- and can show what it means rather than assert it.
	        "defaults": proposal(doc) if doc.protocol else None,
	        "deliveries": [{"name": d.name, "week_iso": d.week_iso,
	                        "supplier": d.supplier, "units": cint(d.units),
	                        "plants": cint(d.plants), "note": d.note}
	                       for d in (doc.get("plan_deliveries") or [])],
	        "confirmed": cint(doc.supply_confirmed),
	        "confirmed_on": str(doc.supply_confirmed_on or "") or None,
	        "confirmed_by": doc.supply_confirmed_by,
	        "note": doc.supply_note,
	        "docstatus": doc.docstatus}


@frappe.whitelist()
def save_delivery(plan, week_iso, units, supplier=None, note=None, row=None):
	"""Add a delivery against a week, or change one already typed in."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is submitted.").format(doc.name))
	monday = _week_monday(week_iso)
	if not monday:
		frappe.throw(_("{0} is not a week. Weeks look like 2026-W12.").format(week_iso))
	if cint(units) <= 0:
		frappe.throw(_("A delivery of nothing is not a delivery."))

	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	per_unit = 1.0 / (flt(v.cuttings_per_plant_required) or 1.0)
	existing = next((d for d in (doc.get("plan_deliveries") or [])
	                 if d.name == row), None) if row else None
	target = existing or doc.append("plan_deliveries", {})
	target.week_iso = _iso(monday)
	target.week_start = monday
	target.supplier = supplier or None
	target.units = cint(units)
	target.plants = int(round(cint(units) * per_unit))
	target.note = note
	# Typing a delivery changes what was agreed, so the agreement is re-asked.
	doc.supply_confirmed = 0
	doc.supply_confirmed_on = None
	doc.supply_confirmed_by = None
	doc.save()
	return saved_supply(doc.name)


@frappe.whitelist()
def delete_delivery(plan, row):
	"""Take a typed-in delivery back off the plan."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is submitted.").format(doc.name))
	keep = [d for d in (doc.get("plan_deliveries") or []) if d.name != row]
	if len(keep) == len(doc.get("plan_deliveries") or []):
		frappe.throw(_("That delivery is not on this plan."))
	doc.set("plan_deliveries", [])
	for d in keep:
		doc.append("plan_deliveries", {
			"week_iso": d.week_iso, "week_start": d.week_start,
			"supplier": d.supplier, "units": d.units, "plants": d.plants,
			"note": d.note})
	doc.supply_confirmed = 0
	doc.supply_confirmed_on = None
	doc.supply_confirmed_by = None
	doc.save()
	return saved_supply(doc.name)


@frappe.whitelist()
def pull_procurement(plan):
	"""Copy what the sellers and raisers agreed to onto the plan itself.

	The plan is the document people keep. Leaving the agreement only on the
	procurement plan meant reading two records to know what a season was
	actually built on, and the second one outlives nobody's memory of it."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)

	proc = frappe.get_all(
		"Summer Flower Procurement Plan",
		filters={"production_plan": doc.name, "docstatus": ["<", 2]},
		fields=["name", "status", "docstatus", "entry_stage"],
		order_by="creation desc", limit=1)
	proc = proc[0] if proc else None

	# What was ordered is the Supply tab's business; what comes back here is
	# who raises the plants and when they hand them over.
	raisers = []
	plants = outside_cost = 0
	units = outsiders = 0
	if proc:
		p = frappe.get_doc("Summer Flower Procurement Plan", proc.name)
		for a in (p.get("propagation_allocation") or []):
			out = a.alloc_type == "Outside Propagator"
			plants += cint(a.plants)
			units += 0 if out else 1
			outsiders += 1 if out else 0
			outside_cost += flt(a.amount) if out else 0
			raisers.append({
				"alloc_type": a.alloc_type, "party": a.party or a.supplier
				or a.propagation_unit, "raises": a.raises,
			"lead_weeks": cint(a.lead_weeks),
				"share_pct": flt(a.share_pct), "plants": cint(a.plants),
				"rate_per_plant": flt(a.rate_per_plant), "amount": flt(a.amount),
				"start_by": a.start_by, "plants_from": a.plants_from,
				"plants_to": a.plants_to,
				"propagation_plan": a.propagation_plan,
				"purchase_order": a.purchase_order,
			})

	if doc.docstatus == 2:
		frappe.throw(_("{0} is cancelled. Its revision carries the agreement."
		               ).format(doc.name))

	scalars = {}
	if raisers:
		scalars.update({
			"propagation_plants": plants,
			"propagation_cost": outside_cost,
			"propagation_units": units,
			"propagation_outside": outsiders,
		})
	# Only where a procurement plan actually says who raises it. A plan that
	# has raised nothing yet still has the choice made on the planning board,
	# and overwriting it with an empty list loses the only copy.
	tables = ({"plan_raisers": ("Summer Flower Plan Raiser", raisers)}
	          if raisers else {})
	if doc.docstatus == 0:
		for field, (_dt, rows) in tables.items():
			doc.set(field, [])
			for r in rows:
				doc.append(field, r)
		for k, v in scalars.items():
			setattr(doc, k, v)
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
	else:
		# A submitted plan still has to be able to say what came back: these
		# are a record of what happened, not an edit to what was approved.
		for field, (child_dt, rows) in tables.items():
			frappe.db.delete(child_dt, {"parent": doc.name, "parentfield": field})
			for i, r in enumerate(rows):
				child = frappe.get_doc(dict(
					r, doctype=child_dt, parent=doc.name, parenttype=doc.doctype,
					parentfield=field, idx=i + 1))
				child.db_insert()
		for k, v in scalars.items():
			doc.db_set(k, v, update_modified=False)
	doc.reload()
	arrivals = _write_arrivals(doc)
	if doc.docstatus == 0 and arrivals:
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
	return {"plan": doc.name, "procurement_plan": proc.name if proc else None,
	        "raisers": len(raisers), "arrivals": len(arrivals),
	        "plants": plants, "outside_cost": outside_cost}


def _lead_for_party(unit, supplier, plan_doc):
	"""Weeks of notice: the supplier's own, else the protocol's own bench."""
	if supplier:
		return cint(frappe.db.get_value("Supplier", supplier,
		                                "custom_sf_propagation_weeks"))
	try:
		v = frappe.get_cached_doc("Crop Protocol Version", plan_doc.protocol)
		return cint(v.get("weeks_tc_to_first_cut_derived")) or cint(
			v.get("sticking_to_planting_weeks"))
	except Exception:
		frappe.clear_last_message()
		return 0


@frappe.whitelist()
def save_raisers(plan, rows):
	"""Who raises the plants, kept on the plan itself.

	Until now this lived only on the procurement plan, which does not exist
	until the plan has been approved -- so a draft had nowhere to record the
	choice and the Propagation tab read zero on a plan whose units had been
	picked weeks before."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	rows = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "write", doc, throw=True)
	if doc.docstatus == 2:
		frappe.throw(_("{0} is cancelled.").format(doc.name))

	# What a propagator hands over is what goes IN the ground, not what is
	# left standing after establishment -- the same figure its deliveries to
	# the farm are counted in.
	plants_total = (sum(cint(b.get("plants_to_plant")) or cint(b.plants)
	                    for b in (doc.get("plan_blocks") or []) if cint(b.is_new_planting))
	                or cint(doc.get("new_plants_required")))
	kept, plants, cost, units, outsiders = [], 0, 0.0, 0, 0
	for r in rows:
		party = (r.get("party") or r.get("propagation_unit") or r.get("supplier") or "")
		if not party:
			continue
		outside = bool(r.get("supplier")) and not r.get("propagation_unit")
		share = flt(r.get("share_pct"))
		mine = cint(r.get("plants")) or int(round(plants_total * share / 100.0))
		rate = flt(r.get("rate_per_plant"))
		plants += mine
		cost += rate * mine if outside else 0
		units += 0 if outside else 1
		outsiders += 1 if outside else 0
		kept.append({
			"alloc_type": _("Outside Propagator") if outside else _("Own Unit"),
			"party": party, "raises": r.get("raises") or doc.get("entry_stage"),
			"share_pct": share, "plants": mine,
			"rate_per_plant": rate, "amount": rate * mine if outside else 0,
			"start_by": r.get("start_by"), "plants_from": r.get("plants_from"),
			"plants_to": r.get("plants_to"),
			# Kept so the arrivals can say when this raiser has to start, not
			# only when the field wants what they bring.
			"lead_weeks": cint(r.get("lead_weeks")) or _lead_for_party(
				r.get("propagation_unit"), r.get("supplier"), doc),
		})

	if doc.docstatus == 0:
		doc.set("plan_raisers", [])
		for r in kept:
			doc.append("plan_raisers", r)
		_write_arrivals(doc)
		doc.propagation_plants = plants
		doc.propagation_cost = cost
		doc.propagation_units = units
		doc.propagation_outside = outsiders
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
	else:
		frappe.db.delete("Summer Flower Plan Raiser",
		                 {"parent": doc.name, "parentfield": "plan_raisers"})
		for i, r in enumerate(kept):
			frappe.get_doc(dict(r, doctype="Summer Flower Plan Raiser",
			                    parent=doc.name, parenttype=doc.doctype,
			                    parentfield="plan_raisers", idx=i + 1)).db_insert()
		doc.reload()
		_write_arrivals(doc)
		for k, v in (("propagation_plants", plants), ("propagation_cost", cost),
		             ("propagation_units", units), ("propagation_outside", outsiders)):
			doc.db_set(k, v, update_modified=False)
	return {"plan": doc.name, "raisers": len(kept), "plants": plants,
	        "cost": cost, "units": units, "outside": outsiders}


def _arrival_rows(plan_doc):
	"""Week by week, what each raiser hands to the field."""
	from upande_summer_flowers.summer_flowers import planning_api as pa

	name = frappe.db.get_value(
		"Summer Flower Procurement Plan",
		{"production_plan": plan_doc.name, "docstatus": ["<", 2]}, "name")
	out = []
	if name:
		# The procurement plan knows the lead time each raiser works to, so
		# its own schedule is used rather than a second one worked out here.
		for r in (pa.allocation_plan(name) or {}).get("raisers") or []:
			for w in r.get("weeks") or []:
				out.append({
					"week_iso": w["wanted_iso"], "week_start": w["wanted_date"],
					"party": r["party"],
					"alloc_type": (_("Outside Propagator") if r["outside"]
					               else _("Own Unit")),
					"plants_handed": cint(w["plants_handed"]),
					"plants_standing": cint(w["plants_standing"]),
					"units": cint(w["units"]), "start_week": w["start_iso"],
				})
	if out:
		return sorted(out, key=lambda x: (x["week_start"], x["party"]))

	# Nothing raised yet: split what the plantings want by the shares chosen
	# on the plan, so a draft still says who brings what and when.
	rows = [r for r in (plan_doc.get("plan_raisers") or []) if flt(r.share_pct)]
	if not rows:
		return []
	for b in plan_doc.plan_blocks:
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		at = _monday(b.planting_date)
		hand = cint(b.get("plants_to_plant")) or cint(b.plants)
		for r in rows:
			share = flt(r.share_pct) / 100.0
			out.append({
				"week_iso": _iso(at), "week_start": str(at), "party": r.party,
				"alloc_type": r.alloc_type,
				"plants_handed": int(round(hand * share)),
				"plants_standing": int(round(cint(b.plants) * share)),
				"units": 0,
				"start_week": _iso(add_days(at, -7 * cint(r.get("lead_weeks")))),
			})
	merged = {}
	for x in out:
		k = (x["week_start"], x["party"])
		if k in merged:
			merged[k]["plants_handed"] += x["plants_handed"]
			merged[k]["plants_standing"] += x["plants_standing"]
		else:
			merged[k] = x
	return [merged[k] for k in sorted(merged)]


def _write_arrivals(plan_doc):
	"""Keep the weekly arrivals on the plan, draft or submitted."""
	rows = _arrival_rows(plan_doc)
	if plan_doc.docstatus == 0:
		plan_doc.set("plan_arrivals", [])
		for r in rows:
			plan_doc.append("plan_arrivals", r)
		return rows
	frappe.db.delete("Summer Flower Plan Arrival",
	                 {"parent": plan_doc.name, "parentfield": "plan_arrivals"})
	for i, r in enumerate(rows):
		frappe.get_doc(dict(r, doctype="Summer Flower Plan Arrival",
		                    parent=plan_doc.name, parenttype=plan_doc.doctype,
		                    parentfield="plan_arrivals", idx=i + 1)).db_insert()
	return rows


@frappe.whitelist()
def consignments_csv(plan):
	"""The deliveries, as a file somebody can send to a supplier."""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	rows = [["Supplier", "Delivery", "Lands (week)", "Lands (date)", "Units",
	         "Plants", "Order by", "Covers plantings to", "Planting weeks",
	         "Added by hand"]]
	for c in (doc.get("plan_consignments") or []):
		rows.append([c.supplier, cint(c.cycle_no), c.lands_week,
		             str(c.lands_date or ""), cint(c.units), cint(c.plants),
		             c.order_week, c.covers_to, cint(c.planting_weeks),
		             "yes" if cint(c.by_hand) else ""])
	import csv
	import io

	buf = io.StringIO()
	csv.writer(buf).writerows(rows)
	frappe.response["filename"] = "%s-deliveries.csv" % doc.name
	frappe.response["filecontent"] = buf.getvalue()
	frappe.response["type"] = "download"
	frappe.response["doctype"] = None


@frappe.whitelist()
def suggest_supply(plan, houses=None, max_wait_weeks=None):
	"""What the plan would have to be delivered, before anyone is named.

	The farm's question is not "here are my houses, what do they bring" but
	"what has to arrive, when, and how many houses do I need to bring it".
	This answers that from the two things already known -- the weeks the plan
	plants in, and how a consignment is handed over in lifts -- and leaves the
	names blank, because who supplies a season is the farm's decision and not
	an arithmetic result.

	`max_wait_weeks` is how long plants may sit on the bench waiting for their
	planting week. Nought is a delivery for every planting week: no waiting, no
	surplus, the most deliveries. Raising it folds the later weeks into the
	consignment before them, which is fewer deliveries and plants waiting.

	Neither number has to be typed. Left out, both are read off the protocol:
	how many houses can supply this variety, and how often a house comes --
	from which the waiting follows, because a house that comes four times a
	year is a delivery every thirteen weeks whether or not anyone says so.
	"""
	from upande_summer_flowers.summer_flowers.planning_api import _guard

	_guard()
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	frappe.has_permission("Summer Flower Production Plan", "read", doc, throw=True)
	v = frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	stated = supply_defaults(v)

	# What the farm typed on the card wins; what it did not type comes off the
	# protocol; and only where the protocol is silent does the old default
	# stand -- one house, and a delivery for every planting week.
	def _given(x):
		# Nought is an answer -- "a delivery for every planting week" -- and
		# `x or ""` would read it as silence, so the test is for the absence
		# of a value, not for its falseness.
		return x is not None and str(x).strip() not in ("", "None")

	asked_houses = cint(houses) if _given(houses) else 0
	houses = max(1, min(12, asked_houses or stated["suppliers"] or 1))
	wait = max(0, cint(max_wait_weeks)) if _given(max_wait_weeks) \
		else stated["wait_weeks"]

	from upande_summer_flowers.summer_flowers import route_ways, sourcing

	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	form = route_ways.LIFT_FORM.get(entry) or entry or DEFAULT_FORM
	# The route states the notice the entry stage needs; the lab turnaround is
	# the fallback, and ten weeks the last resort.
	lead = cint(decided.get("lead_weeks")) or cint(
		frappe.db.get_single_value("Summer Flower Settings",
		                           "lab_turnaround_weeks")) or 10

	wanted = _wanted_by_week(doc)
	weeks = sorted(wanted)
	if not weeks:
		return {"plan": plan, "form": form, "houses": [], "picks": [],
		        "reason": _("This plan has no planting weeks to deliver to.")}

	# A delivery opens, and carries the planting weeks behind it until one of
	# them would have waited longer than the farm is willing to carry it.
	drops = _drops(weeks, wait)

	span_weeks = max(1, (getdate(weeks[-1]) - getdate(weeks[0])).days // 7 + 1)
	years = max(span_weeks / 52.0, 1.0 / 52.0)
	# Every house delivers against every one of those weeks, each bringing its
	# own share of it. Dealing the weeks out one house at a time instead --
	# this one takes week 28, that one week 29 -- reads neatly and plans
	# wrongly: a house given a quarter of the season still has to cover the
	# WHOLE of the weeks it was dealt, and sizing it on a quarter leaves every
	# one of those weeks short. Sharing each delivery is also how the farm
	# actually arranges cover, so that a week nobody can fill alone is filled
	# between them.
	share = round(100.0 / houses, 4)
	picks = [{
		"supplier": "",
		"share_pct": share if i < houses - 1 else round(100.0 - share * (houses - 1), 4),
		"lead_weeks": lead,
		# How often this house comes. The protocol's figure is the agreement;
		# what the plan worked out is only what these planting weeks needed,
		# and a house does not come less often because one season is short.
		"cycle_count": max(1, stated["cycle_weeks"]
		                   or int(round(len(drops) / years))),
		"weeks": [_iso(d) for d in drops],
	} for i in range(houses)]

	# What those deliveries actually come to, worked out by the same function
	# the card uses, so a suggestion and the plan it becomes cannot differ.
	named = [dict(p, supplier=_("House {0}").format(i + 1))
	         for i, p in enumerate(picks)]
	run = plan_supply(plan, picks=named, form=form)
	return {
		"plan": plan, "form": form, "entry_stage": entry,
		"lead_weeks": lead,
		# Where each number came from, so the card can say so rather than
		# presenting the protocol's agreement as arithmetic of its own. The
		# protocol's OWN rhythm, not the one the card may have overruled.
		"from_protocol": proposal(doc, v),
		"houses_asked": asked_houses,
		"houses_suggested": len(picks),
		"deliveries": len(drops),
		"planting_weeks": len(weeks),
		"max_wait_weeks": wait,
		"cycle_weeks_by_house": [p["cycle_count"] for p in picks],
		# Ready to drop straight into the card: shares, cycles and the weeks
		# themselves, with the names left for the farm to fill in.
		"picks": picks,
		"would_buy": cint(run.get("units_bought")),
		"weeks_short": cint(run.get("weeks_short")),
		"longest_wait_weeks": max(
			[cint(w.get("held_weeks")) for w in (run.get("weeks") or [])] or [0]),
		"stranded": cint(run.get("plants_stranded")),
		"plants_needed": cint(run.get("plants_needed")),
		"preview": run,
	}


@frappe.whitelist()
def adopt_to_protocol(plan):
	"""Write this plan's agreed houses back onto the protocol as the default.

	The arrangement is worked out on the plan, where the demand is. Until now
	it died there: the next plan for the same variety opened on the protocol's
	two blanket figures again, and the farm re-typed what it had already
	settled. The table on the protocol is what every later plan opens on, so
	this is the one place the dashboard is allowed to write to it.

	Only the arrangement travels -- who, how often, how much notice, what
	share. The agronomy is untouched, so no version is taken and nothing needs
	re-approving: these are commercial facts about who brings the material.
	"""
	doc = frappe.get_doc("Summer Flower Production Plan", plan)
	rows = [r for r in (doc.get("plan_supply") or [])
	        if (r.get("supplier") or "").strip()]
	if not rows:
		frappe.throw(_("Nothing to adopt: no supplier is named on this plan."))
	cp = frappe.db.get_value("Crop Protocol Version", doc.protocol, "crop_protocol")
	if not cp:
		frappe.throw(_("This plan's protocol version does not say which Crop "
		               "Protocol it came from, so there is nowhere to write."))
	master = frappe.get_doc("Crop Protocol", cp)
	frappe.has_permission("Crop Protocol", "write", doc=master, throw=True)
	# One row per house, in the order the plan lists them. A house named twice
	# on the plan -- once per cycle -- is one house on the protocol.
	seen, keep = set(), []
	for r in rows:
		name = (r.get("supplier") or "").strip()
		if name in seen:
			continue
		seen.add(name)
		keep.append({"supplier": name,
		             "farm": doc.farm,
		             "cycle_weeks": max(0, min(52, cint(r.get("cycle_count")))),
		             "lead_weeks": cint(r.get("lead_weeks")),
		             "share_pct": flt(r.get("share_pct")),
		             "preferred": 0})
	master.set("custom_sf_suppliers", [])
	for row in keep:
		master.append("custom_sf_suppliers", row)
	master.save()
	frappe.db.commit()
	return {"protocol": cp, "houses": len(keep),
	        "note": _("{0} now opens on {1}: {2}.").format(
		        cp, _("these suppliers") if len(keep) > 1 else _("this supplier"),
		        ", ".join("{0} every {1} wk".format(
			        x["supplier"], x["cycle_weeks"] or "?") for x in keep))}
