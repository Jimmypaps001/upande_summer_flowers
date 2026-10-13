# Copyright (c) 2026, James Kiruga and contributors
"""Does recording an actual change the plan the way it should?

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_prop_actuals.main

Everything is rolled back. Nothing here is left on the site.
"""

import traceback

import frappe
from frappe.utils import add_days, cint, getdate, nowdate

PLAN = "SFPROP-2026-00012"
OK, BAD = [], []


def ck(cond, label, detail=""):
	(OK if cond else BAD).append(label)
	print(("  PASS  " if cond else "  FAIL  ") + label + (("   " + str(detail)) if detail else ""))


def main(plan=PLAN):
	try:
		_run(plan)
	except Exception:
		print(traceback.format_exc())
	finally:
		frappe.db.rollback()
		print("\nrolled back")
	print("\n%d passed, %d failed" % (len(OK), len(BAD)))
	for b in BAD:
		print("   FAILED:", b)


def rebuild(name):
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	d.flags.ignore_permissions = True
	d.save()
	return frappe.get_doc("Summer Flower Propagation Plan", name)


def actual(d, **kw):
	a = frappe.new_doc("Summer Flower Propagation Actual")
	a.propagation_plan = d.name
	a.actual_date = kw.pop("on")
	for k, v in kw.items():
		setattr(a, k, v)
	a.flags.ignore_permissions = True
	a.insert()
	a.submit()
	return a


def _run(name):
	from upande_summer_flowers.summer_flowers import propagation_actuals as pa

	today = getdate(nowdate())
	print("=== 1. a plan with nothing recorded against it ===")
	d = rebuild(name)
	ck(cint(d.actuals_recorded) == 0, "no records counted", d.actuals_recorded)
	ck(cint(d.carried_short) == 0, "nothing carried short", d.carried_short)
	ck(not d.replan_note, "no re-plan offered")
	past = [r for r in d.weeks if r.deliver_on and getdate(r.deliver_on) < today]
	ck(bool(past), "the plan has weeks already in the past", len(past))
	ck(all(r.state == "Not Reported" for r in past),
	   "a past week nobody has reported on reads Not Reported, not Missed",
	   "%d of %d" % (sum(1 for r in past if r.state == "Not Reported"), len(past)))
	ck(cint(d.weeks_closed) == 0, "nothing is judged yet", d.weeks_closed)
	ck(not d.reported_to, "no reporting cutoff yet", d.reported_to)
	ck(cint(d.delivered_short) == 0,
	   "silence is not a shortfall", d.delivered_short)
	fut = [r for r in d.weeks if r.deliver_on and getdate(r.deliver_on) >= today]
	ck(all(r.state == "To Come" for r in fut), "future weeks read To Come")
	ck(all(cint(r.variance_plants) == 0 for r in fut),
	   "a week that has not happened carries no variance")

	print("\n=== 2. the baseline is what the unit is held to ===")
	d.freeze_baseline()
	d.flags.ignore_permissions = True
	d.save()
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	base = pa.baseline_of(d)
	ck(bool(base), "baseline frozen")
	first = past[0]
	promised = cint(first.baseline_plants)
	ck(promised > 0, "first closed week promised something", promised)
	# Move the production plan's own figure and the baseline must not follow.
	ck(cint(base["plants"]) == cint(d.total_plants_to_stick),
	   "baseline holds the approved total", base["plants"])

	print("\n=== 3. a delivery that came up short ===")
	got = int(promised * 0.6)
	actual(d, event="Plants To Farm", on=first.deliver_on,
	       against_week=first.deliver_on, plants=got,
	       remarks="test")
	d = rebuild(name)
	row = next(r for r in d.weeks if str(r.deliver_on) == str(first.deliver_on))
	ck(cint(row.actual_plants) == got, "the delivery lands on its week", row.actual_plants)
	ck(row.state == "Part", "a short delivery reads Part", row.state)
	ck(cint(row.variance_plants) == got - promised, "variance is got minus promised",
	   row.variance_plants)
	ck(cint(d.plants_to_farm) == got, "rolled up to the header", d.plants_to_farm)
	ck(cint(d.actuals_recorded) == 1, "one record counted", d.actuals_recorded)
	ck(str(d.reported_to) == str(first.deliver_on),
	   "the reporting cutoff moved to the record", d.reported_to)
	# Only the weeks up to the cutoff are judged, and this is the last of them.
	judged = [r for r in d.weeks if r.deliver_on
	          and getdate(r.deliver_on) <= getdate(d.reported_to)]
	want_short = sum(cint(r.baseline_plants) for r in judged) - got
	ck(cint(d.carried_short) == want_short,
	   "the gap is what the judged weeks are short",
	   "%s vs %s" % (d.carried_short, want_short))
	ck(cint(d.weeks_closed) == len(judged), "and only those weeks are closed",
	   "%s vs %s" % (d.weeks_closed, len(judged)))
	ck(bool(d.replan_note), "a re-plan is offered")

	print("\n=== 4. a consignment that arrived short and late ===")
	ipast = [r for r in d.intake if r.week_start_date
	         and getdate(r.week_start_date) < today]
	ck(bool(ipast), "the plan has consignments in the past", len(ipast))
	con = ipast[0]
	want = cint(con.units)
	actual(d, event="TC Received" if con.form == "Tissue Culture" else "Roots Withdrawn",
	       on=add_days(getdate(con.week_start_date), 9),
	       against_week=con.week_start_date, units=want - 100, reference="TEST-1")
	d = rebuild(name)
	crow = next(r for r in d.intake
	            if str(r.week_start_date) == str(con.week_start_date)
	            and r.form == con.form)
	ck(cint(crow.actual_units) == want - 100, "the consignment lands on its week",
	   crow.actual_units)
	ck(crow.state == "Short", "it reads Short", crow.state)
	ck(cint(crow.days_late) == 9, "nine days late", crow.days_late)
	ck(cint(crow.variance_units) == -100, "short by a hundred", crow.variance_units)
	ck(crow.recorded_from == "Typed In", "and says where it came from",
	   crow.recorded_from)

	print("\n=== 4b. several consignments in one week share that week ===")
	from upande_summer_flowers.summer_flowers.propagation_actuals import apportion
	ck(apportion(100, [1, 1, 1]) == [34, 33, 33], "a split always sums to the whole",
	   apportion(100, [1, 1, 1]))
	ck(sum(apportion(29510, [756, 22673, 3666])) == 29510, "and on real weights")
	ck(apportion(0, [5, 5]) == [0, 0], "nothing splits to nothing")
	ck(sum(apportion(7, [0, 0])) == 7, "even with no weights to go on",
	   apportion(7, [0, 0]))

	# A week fed by more than one consignment must not give each the whole.
	groups = {}
	for r in d.intake:
		if r.week_start_date:
			groups.setdefault((r.form, str(r.week_start_date)), []).append(r)
	multi = {k: v for k, v in groups.items() if len(v) > 1}
	ck(bool(multi), "this plan has weeks fed by several consignments", len(multi))
	if multi:
		k, rows = sorted(multi.items())[0]
		pool = 30000
		for r in rows:
			pass
		a2 = actual(d, event=("TC Received" if k[0] == "Tissue Culture"
		                      else "Roots Withdrawn"),
		            on=k[1], against_week=k[1], units=pool, reference="SPLIT")
		d2 = rebuild(name)
		got_rows = [r for r in d2.intake
		            if r.form == k[0] and str(r.week_start_date) == k[1]]
		total = sum(cint(r.actual_units) for r in got_rows)
		ck(total == pool,
		   "the week's %d consignments share its %d units, not take %d each"
		   % (len(got_rows), pool, pool), total)
		ck(all(cint(r.actual_units) < pool for r in got_rows)
		   if len(got_rows) > 1 else True,
		   "so no single consignment claims the lot",
		   [cint(r.actual_units) for r in got_rows])
		a2.flags.ignore_permissions = True
		a2.cancel()
		d = rebuild(name)

	print("\n=== 5. a loss ===")
	actual(d, event="Loss", on=first.deliver_on, plants=5000,
	       form="Plants", reason="Hardening mortality")
	d = rebuild(name)
	ck(cint(d.plants_lost) == 5000, "losses rolled up", d.plants_lost)
	ck("Hardening mortality" in (d.actuals_note or ""),
	   "the reason is on the plan", (d.actuals_note or "")[:90])
	ck(cint(d.plants_to_farm) == got,
	   "a loss is not a delivery", d.plants_to_farm)

	print("\n=== 6. the re-plan, and what it refuses to do ===")
	res = pa.propose(d)
	ck(res is not None, "a proposal was built")
	band = pa._band(d)
	fwd = {str(r.deliver_on): cint(r.baseline_plants or r.plants_to_stick)
	       for r in d.weeks if r.deliver_on and getdate(r.deliver_on) >= today}
	over = [c for c in res["cover"]
	        if c["plants"] > int(fwd.get(c["week"], 0) * band)]
	ck(not over, "no week is loaded past its variance band", over[:3])
	ck(all(getdate(c["week"]) >= today for c in res["cover"]),
	   "nothing is proposed for a week that has already gone")
	ck(res["covered"] + res["still_short"] == res["short"],
	   "covered plus still short is the whole gap",
	   "%s + %s vs %s" % (res["covered"], res["still_short"], res["short"]))
	head = res["headroom"]
	ck(res["covered"] <= cint(head["roots_plants"]) + cint(head["tc_plants"]),
	   "nothing is covered from material that does not exist",
	   "%s vs %s" % (res["covered"], head))

	ck(res["covered"] == 0 and res["still_short"] == res["short"],
	   "with nothing to cover it, nothing is covered")
	try:
		pa.accept(name)
		ck(False, "accepting a proposal with no cover is refused")
	except frappe.ValidationError:
		frappe.clear_last_message()
		ck(True, "accepting a proposal with no cover is refused")

	print("\n=== 7. accepting it moves the forward weeks ===")
	_accept_on("SFPROP-2026-00014")

	print("\n=== 8. cancelling an actual takes the variance back ===")
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	a = frappe.get_all("Summer Flower Propagation Actual",
	                   filters={"propagation_plan": name, "event": "Plants To Farm",
	                            "docstatus": 1}, pluck="name")
	doc = frappe.get_doc("Summer Flower Propagation Actual", a[0])
	doc.flags.ignore_permissions = True
	doc.cancel()
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	row = next(r for r in d.weeks if str(r.deliver_on) == str(first.deliver_on))
	ck(cint(row.actual_plants) == 0, "the delivery is gone", row.actual_plants)
	# Not back to unreported: the loss was recorded on that same week, so the
	# unit HAS reported on it, and a reported week with no delivery is missed.
	ck(row.state == "Missed",
	   "a week the unit reported on, with no delivery left, is Missed", row.state)
	ck(str(d.reported_to) == str(first.deliver_on),
	   "the loss still holds the cutoff there", d.reported_to)
	ck(cint(d.plants_to_farm) == 0, "the header followed it", d.plants_to_farm)

	print("\n=== 9. actuals survive a rebuild ===")
	d = rebuild(name)
	ck(cint(d.plants_lost) == 5000, "the loss is still there after a rebuild",
	   d.plants_lost)
	crow = next(r for r in d.intake
	            if str(r.week_start_date) == str(con.week_start_date)
	            and r.form == con.form)
	ck(cint(crow.actual_units) == want - 100,
	   "and so is the consignment", crow.actual_units)

	print("\n=== 10. the season key, not the plan name ===")
	other = frappe.get_all("Summer Flower Propagation Plan",
	                       filters={"variety": d.variety,
	                                "name": ["!=", name]},
	                       fields=["name", "season_start_year"])
	ck(bool(other), "the same variety has other seasons", other)
	mine = frappe.db.count("Summer Flower Propagation Actual",
	                       {"variety": d.variety,
	                        "season_start_year": cint(d.season_start_year),
	                        "docstatus": 1})
	ck(cint(d.actuals_recorded) == mine,
	   "this season sees exactly its own records", "%s vs %s" % (d.actuals_recorded, mine))
	for o in other:
		od = frappe.get_doc("Summer Flower Propagation Plan", o.name)
		od.flags.ignore_permissions = True
		od.save()
		theirs = frappe.db.count("Summer Flower Propagation Actual",
		                         {"variety": od.variety,
		                          "season_start_year": cint(od.season_start_year),
		                          "docstatus": 1})
		# Same variety, different season. It must see its own and nothing of
		# this one's -- 00014 keeps the single delivery section 7 left on it.
		ck(cint(od.actuals_recorded) == theirs,
		   "season %s sees only its own (%d), none of this season's %d"
		   % (o.season_start_year, theirs, cint(d.actuals_recorded)),
		   od.actuals_recorded)
		ck(cint(od.actuals_recorded) < cint(d.actuals_recorded) or theirs == 0
		   or o.season_start_year == 2028,
		   "and the two do not share a pool", o.season_start_year)


def _accept_on(name):
	"""Accepting needs material to cover the gap with. This plan has it.

	SFPROP-2026-00012 has nothing: no roots on the shelf and no lab down for
	more than the order needs, which is why the proposal there covers nothing
	and refuses to be accepted. The acceptance path still has to be exercised,
	so it is exercised where there is something to accept.
	"""
	from upande_summer_flowers.summer_flowers import propagation_actuals as pa

	d = rebuild(name)
	wk = next(r for r in d.weeks if r.deliver_on and cint(r.plants_to_stick))
	promised = cint(r_promised(d, wk))
	got = int(promised * 0.5)
	actual(d, event="Plants To Farm", on=wk.deliver_on,
	       against_week=wk.deliver_on, plants=got)
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	ck(cint(d.carried_short) == promised - got,
	   "%s carries the gap" % name, d.carried_short)
	head = pa._headroom(d)
	ck(cint(head["tc_plants"]) > 0,
	   "the labs are down for more than the order needs", head["tc_plants"])

	res = pa.propose(d)
	ck(res["covered"] > 0, "so the gap can be covered", res["covered"])
	before = {str(r.deliver_on): cint(r.from_bought) for r in d.weeks}
	out = pa.accept(name)
	ck(cint(out["accepted"]) == len(res["cover"]),
	   "one record per covered week", out["accepted"])

	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	hit = 0
	for c in res["cover"]:
		r = next(x for x in d.weeks if str(x.deliver_on) == c["week"])
		if cint(r.uplift_plants) == c["plants"] and \
				cint(r.from_bought) == before[c["week"]] + c["plants"]:
			hit += 1
	ck(hit == len(res["cover"]), "every covered week carries its uplift",
	   "%d of %d" % (hit, len(res["cover"])))
	ck(cint(d.plants_to_farm) == got,
	   "an uplift is a decision, not a delivery", d.plants_to_farm)
	ck(cint(d.replan_cover) > 0, "the plan says how much was taken in",
	   d.replan_cover)

	# And cancelling the uplifts takes it back out again.
	for n in out["names"]:
		doc = frappe.get_doc("Summer Flower Propagation Actual", n)
		doc.flags.ignore_permissions = True
		doc.cancel()
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	back = sum(cint(r.uplift_plants) for r in d.weeks)
	ck(back == 0, "cancelling the uplifts undoes the re-plan", back)


def r_promised(d, wk):
	return cint(wk.baseline_plants) or cint(wk.plants_to_stick)
