# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Does the Eryngium chain tally, and is the protocol actually being followed?

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_eryngium_chain.main

Every number this checks against is read from the protocol version in force for
that variety and farm. Nothing is hard-coded: not the survival rate, not the
grade split, not the lift weeks, not the TC/Roots share. If a figure here looks
wrong, the protocol is where it is wrong, which is the point -- a check that
carries its own copy of the numbers only ever proves itself right.

It reads and reports; it writes nothing.
"""

import frappe
from frappe.utils import cint, flt

PASS, FAIL, WARN = [], [], []


def ck(label, cond, extra=""):
	(PASS if cond else FAIL).append(label)
	print(("  ok   " if cond else "  FAIL ") + label
	      + ((" -- " + str(extra)) if extra else ""), flush=True)


def warn(label, extra=""):
	WARN.append(label)
	print("  warn " + label + ((" -- " + str(extra)) if extra else ""), flush=True)


def current_version(variety, farm):
	"""The protocol in force for this variety at this farm, by the doctype's
	own rule -- is_current, then Active -- never a name built by hand."""
	v = frappe.db.get_value("Crop Protocol Version",
	                        {"variety": variety, "farm": farm, "is_current": 1}, "name")
	if v:
		return v
	return frappe.db.get_value("Crop Protocol Version",
	                           {"variety": variety, "farm": farm,
	                            "version_status": "Active"}, "name")


def check_protocol(name):
	"""A protocol version is self-consistent, or the plans built on it cannot be."""
	d = frappe.get_doc("Crop Protocol Version", name)
	print("\n--- protocol %s (%s, current=%s) ---"
	      % (d.name, d.version_status, d.is_current), flush=True)

	# lifts: every form's waves must account for the whole consignment
	by_form = {}
	for L in (d.lifts or []):
		by_form.setdefault(L.form, []).append(L)
	if not by_form:
		ck("%s defines lifts" % d.name, False,
		   "no lifts: nothing says when a consignment becomes workable")
	else:
		ck("%s defines lifts" % d.name, True, "%d across %d form(s)"
		   % (len(d.lifts), len(by_form)))
		for form, rows in by_form.items():
			total = sum(flt(r.share_pct) for r in rows)
			ck("  %s lifts account for the whole consignment" % form,
			   abs(total - 100) < 0.01, "%.2f%% over %d lift(s)" % (total, len(rows)))
			nos = [cint(r.lift_no) for r in rows]
			ck("  %s lift numbers are distinct" % form, len(set(nos)) == len(nos), nos)
			weeks = [cint(r.weeks_after_arrival) for r in rows]
			ck("  %s lifts are in time order" % form, weeks == sorted(weeks), weeks)

	# grades: the split of what reaches the farm
	grades = d.grade_allocation or []
	if not grades:
		ck("%s allocates grades" % d.name, False,
		   "no grade rows: nothing says what a dispatch is graded into")
	else:
		total = sum(flt(g.allocation_pct) for g in grades)
		ck("%s grade allocation totals 100%%" % d.name, abs(total - 100) < 0.01,
		   "%.2f%% over %d grade(s)" % (total, len(grades)))
		ck("%s grade_total_pct agrees with its own rows" % d.name,
		   abs(flt(d.grade_total_pct) - total) < 0.01,
		   "header %.2f vs rows %.2f" % (flt(d.grade_total_pct), total))

	# the route: where the material comes from
	shares = [(r.stage, flt(r.share_pct)) for r in (d.material_route or [])
	          if flt(r.share_pct)]
	if shares:
		total = sum(s for _, s in shares)
		ck("%s route shares total 100%%" % d.name, abs(total - 100) < 0.01,
		   ", ".join("%s %.2f%%" % (st, s) for st, s in shares))

	# a survival rate is what turns plants stuck into plants delivered
	ck("%s states a rooting success rate" % d.name,
	   flt(d.rooting_success_pct) > 0, "%s%%" % d.rooting_success_pct)

	# every form the route names must have lifts, or its material has no timing
	route_forms = {r.stage for r in (d.material_route or []) if r.stage in ("TC", "Roots")}
	lift_forms = {("TC" if f == "Tissue Culture" else f) for f in by_form}
	missing = route_forms - lift_forms
	ck("%s has lifts for every form its route uses" % d.name, not missing,
	   "missing: %s" % sorted(missing) if missing else "")
	return d


def main():
	variety_like = "%Eryngium%"
	print("=== 1. the protocol versions in force ===", flush=True)
	plans = frappe.get_all("Summer Flower Propagation Plan",
	                       filters={"variety": ["like", variety_like]},
	                       fields=["name", "variety", "farm", "season", "status",
	                               "protocol", "total_plants_to_stick",
	                               "tc_plants_required", "cuttings_per_plant"])
	print("  %d Eryngium propagation plan(s)" % len(plans), flush=True)

	seen = set()
	for p in plans:
		cur = current_version(p.variety, p.farm)
		if cur and cur not in seen:
			seen.add(cur)
	for name in sorted(seen):
		check_protocol(name)

	print("\n=== 2. is each plan on the protocol in force? ===", flush=True)
	for p in plans:
		cur = current_version(p.variety, p.farm)
		status = frappe.db.get_value("Crop Protocol Version", p.protocol,
		                             "version_status") if p.protocol else None
		ck("%s is on the current protocol" % p.name, p.protocol == cur,
		   "plan uses %s (%s), current is %s" % (p.protocol or "none", status or "-", cur))
		if p.protocol and p.protocol != cur:
			old = frappe.get_doc("Crop Protocol Version", p.protocol)
			new = frappe.get_doc("Crop Protocol Version", cur) if cur else None
			if new:
				print("       lifts  %d -> %d | grades %d -> %d"
				      % (len(old.lifts or []), len(new.lifts or []),
				         len(old.grade_allocation or []), len(new.grade_allocation or [])),
				      flush=True)

	print("\n=== 3. do the plan's own numbers tally? ===", flush=True)
	for p in plans:
		d = frappe.get_doc("Summer Flower Propagation Plan", p.name)
		rows = sum(cint(w.plants_to_stick) for w in d.weeks)
		ck("%s week rows sum to its header" % p.name,
		   rows == cint(d.total_plants_to_stick),
		   "rows %s vs header %s" % (rows, d.total_plants_to_stick))

		# the gap the protocol asks for, taken from the protocol, not assumed
		proto = frappe.db.get_value("Crop Protocol Version", d.protocol,
		                            ["sticking_to_planting_weeks", "weeks_on_tray",
		                             "weeks_on_pot", "hardening_weeks"],
		                            as_dict=True) if d.protocol else None
		if proto:
			gap = cint(proto.sticking_to_planting_weeks) or (
				cint(proto.weeks_on_tray) + cint(proto.weeks_on_pot)
				+ cint(proto.hardening_weeks))
			if gap:
				off = [(w.year, w.week_no,
				        round((w.deliver_on - w.week_start_date).days / 7.0, 1))
				       for w in d.weeks if w.week_start_date and w.deliver_on
				       and abs((w.deliver_on - w.week_start_date).days / 7.0 - gap) > 0.01]
				ck("%s delivers %s weeks after sticking, as the protocol says" % (p.name, gap),
				   not off, "%d week(s) disagree: %s" % (len(off), off[:4]) if off else "")
			else:
				warn("%s protocol states no sticking-to-planting gap" % p.name)

	print("\n=== 4. does the planting calendar match the plan? ===", flush=True)
	for p in plans:
		d = frappe.get_doc("Summer Flower Propagation Plan", p.name)
		cal = frappe.get_all("Planting Calendar",
		                     filters={"variety": p.variety, "farm": p.farm},
		                     fields=["name", "planting_year", "planting_week", "plants",
		                             "plants_received", "crop_protocol_version",
		                             "propagation_batch", "crop_cycle", "calendar_status"])
		plan_weeks = {(cint(w.year), cint(w.week_no)): cint(w.plants_to_stick)
		              for w in d.weeks if cint(w.plants_to_stick)}
		if not cal:
			ck("%s has planting calendar rows to deliver into" % p.name, False,
			   "none for %s at %s -- %d plan week(s), %s plants have nowhere to land"
			   % (p.variety, p.farm, len(plan_weeks), sum(plan_weeks.values())))
			continue
		cal_weeks = {}
		for r in cal:
			k = (cint(r.planting_year), cint(r.planting_week))
			cal_weeks[k] = cal_weeks.get(k, 0) + cint(r.plants)
		ck("%s every plan week has a calendar row" % p.name,
		   not (set(plan_weeks) - set(cal_weeks)),
		   "%d missing" % len(set(plan_weeks) - set(cal_weeks)))
		differ = [(k, plan_weeks[k], cal_weeks[k])
		          for k in set(plan_weeks) & set(cal_weeks) if plan_weeks[k] != cal_weeks[k]]
		ck("%s plan and calendar agree week by week" % p.name, not differ,
		   "%d differ: %s" % (len(differ), differ[:3]) if differ else "")
		ck("%s calendar rows cite a protocol version" % p.name,
		   all(r.crop_protocol_version for r in cal),
		   "%d without" % sum(1 for r in cal if not r.crop_protocol_version))

	print("\n=== 5. does the farm end join up? ===", flush=True)
	cal = frappe.get_all("Planting Calendar", filters={"variety": ["like", variety_like]},
	                     fields=["name", "propagation_batch", "crop_cycle",
	                             "plants", "plants_received", "seedling_source"])
	if not cal:
		ck("Eryngium reaches a planting calendar at all", False,
		   "0 rows -- the farm half of the chain does not exist yet")
	else:
		inhouse = [r for r in cal if r.seedling_source == "In-house Propagation"]
		ck("in-house rows name their propagation batch",
		   all(r.propagation_batch for r in inhouse),
		   "%d of %d without" % (sum(1 for r in inhouse if not r.propagation_batch),
		                         len(inhouse)))
		ck("calendar rows reach a crop cycle",
		   all(r.crop_cycle for r in cal),
		   "%d of %d without" % (sum(1 for r in cal if not r.crop_cycle), len(cal)))

	print("\n%d passed, %d failed, %d warnings" % (len(PASS), len(FAIL), len(WARN)),
	      flush=True)
	for f in FAIL:
		print("  failed:", f, flush=True)
	return not FAIL
