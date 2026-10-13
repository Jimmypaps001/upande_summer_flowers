# Copyright (c) 2026, James Kiruga and contributors
"""The half of the actuals that is read rather than typed.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_prop_stock.main

Roots are an Item and propagation units are Warehouses, so a transfer into a
unit IS the withdrawal and nobody should type it twice. This moves real stock
and checks the plan reads it -- and that a typed record of the same movement is
kept but not counted. Everything is rolled back.
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


def receipt(item, warehouse, qty, on, company):
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Receipt"
	se.purpose = "Material Receipt"
	se.company = company
	se.set_posting_time = 1
	se.posting_date = on
	se.posting_time = "09:00:00"
	se.append("items", {"item_code": item, "qty": qty, "t_warehouse": warehouse,
	                    "basic_rate": 1, "allow_zero_valuation_rate": 1})
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	return se


def transfer_out(item, warehouse, to, qty, on, company):
	"""Plants leaving the unit for the farm.

	A transfer, not an issue: the plants are not consumed when they leave the
	propagation unit, they are moved to the farm that will plant them -- and a
	Material Issue is blocked site-wide by the PPE guard anyway.
	"""
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Transfer"
	se.purpose = "Material Transfer"
	se.company = company
	se.set_posting_time = 1
	se.posting_date = on
	se.posting_time = "11:00:00"
	se.append("items", {"item_code": item, "qty": qty, "s_warehouse": warehouse,
	                    "t_warehouse": to, "allow_zero_valuation_rate": 1})
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	return se


def elsewhere(company, units):
	"""Any warehouse in the company that is not a propagation unit."""
	for w in frappe.get_all("Warehouse",
	                        filters={"company": company, "is_group": 0},
	                        pluck="name"):
		if w not in units:
			return w
	return None


def rebuild(name):
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	d.flags.ignore_permissions = True
	d.save()
	return frappe.get_doc("Summer Flower Propagation Plan", name)


def _run(name):
	from upande_summer_flowers.summer_flowers import propagation_actuals as pa

	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	version = frappe.get_cached_doc("Crop Protocol Version", d.protocol)
	items = pa.route_items(version)
	units = pa.unit_warehouses()
	print("route items:", items)
	print("units:", units)
	ck(bool(units), "there are propagation unit warehouses", units)
	root_item = items.get("Roots")
	ck(bool(root_item), "the route names a root item", root_item)
	if not (units and root_item):
		return

	# Every root item on this site is disabled, so no stock can be moved
	# against one and the ledger half of the actuals is unreachable until
	# somebody enables them. Enabled here inside the rollback so the path can
	# be exercised; set_root_item.main --kwargs "{'enable': 1, 'apply': 1}"
	# does it for real.
	ck(cint(frappe.db.get_value("Item", root_item, "disabled")) == 1,
	   "the root item is disabled on this site (reported, then worked around)",
	   root_item)
	frappe.db.set_value("Item", root_item, "disabled", 0)
	frappe.db.set_value("Item", d.variety, "disabled", 0)

	unit = next((w for w in units
	             if frappe.db.get_value("Warehouse", w, "company") == d.company),
	            units[0])
	company = frappe.db.get_value("Warehouse", unit, "company")
	print("using", unit, "of", company, "| plan company", d.company)

	# A consignment week already in the past, so the movement falls in the span.
	con = next(r for r in d.intake
	           if r.form == "Roots" and r.week_start_date
	           and getdate(r.week_start_date) < getdate(nowdate()))
	when = getdate(con.week_start_date)
	want = cint(con.units)
	print("consignment: %s roots due %s" % (want, when))

	print("\n=== 1. a transfer into the unit is the withdrawal ===")
	se = receipt(root_item, unit, want - 7, when, company)
	d = rebuild(name)
	row = next(r for r in d.intake
	           if r.form == "Roots" and str(r.week_start_date) == str(when))
	ck(cint(row.actual_units) == want - 7,
	   "the plan read it off the ledger without anyone typing it",
	   row.actual_units)
	ck(row.recorded_from == "Stock Ledger", "and says so", row.recorded_from)
	ck(row.state == "Short", "seven roots short", row.variance_units)
	ck(cint(d.roots_withdrawn) == want - 7, "rolled up", d.roots_withdrawn)
	ck("Roots Withdrawn" in (d.actuals_note or ""),
	   "the plan names the ledger as the source for roots")

	print("\n=== 2. the same movement typed in as well ===")
	a = frappe.new_doc("Summer Flower Propagation Actual")
	a.propagation_plan = d.name
	a.event = "Roots Withdrawn"
	a.actual_date = when
	a.against_week = when
	a.units = 999999
	a.reference = "typed duplicate"
	a.flags.ignore_permissions = True
	a.insert()
	a.submit()
	d = rebuild(name)
	row = next(r for r in d.intake
	           if r.form == "Roots" and str(r.week_start_date) == str(when))
	ck(cint(row.actual_units) == want - 7,
	   "the ledger still wins; the typed figure does not land",
	   row.actual_units)
	ck("not counted" in (d.actuals_note or ""),
	   "and the plan says the typed record was not counted out loud")
	ck(frappe.db.get_value("Summer Flower Propagation Actual", a.name,
	                       "docstatus") == 1,
	   "the typed record is kept, not deleted")

	print("\n=== 3. plants leaving the unit are the hand-over ===")
	wk = next((r for r in d.weeks if r.deliver_on
	           and getdate(r.deliver_on) < getdate(nowdate())
	           and cint(r.plants_to_stick)), None)
	ck(bool(wk), "a delivery week in the past", wk and wk.deliver_on)
	if wk:
		out = int(cint(wk.plants_to_stick) * 0.8)
		dest = elsewhere(company, units)
		ck(bool(dest), "a farm warehouse to send them to", dest)
		receipt(d.variety, unit, out, add_days(getdate(wk.deliver_on), -2), company)
		transfer_out(d.variety, unit, dest, out, wk.deliver_on, company)
		d = rebuild(name)
		row = next(r for r in d.weeks
		           if str(r.deliver_on) == str(wk.deliver_on))
		ck(cint(row.actual_plants) == out,
		   "plants issued out of the unit are what the farm got",
		   row.actual_plants)
		ck(row.recorded_from == "Stock Ledger", "read, not typed",
		   row.recorded_from)
		ck(row.state == "Part", "and the week is part delivered", row.state)
		ck(cint(d.plants_to_farm) == out, "rolled up", d.plants_to_farm)
		# This week is fed by tissue culture as well as roots, and no tissue
		# culture has been reported, so the cohort is not whole. Refusing to
		# quote a survival rate here is the right answer: the arithmetic would
		# say 468%.
		ck(cint(d.actual_survival_pct) == 0,
		   "survival is NOT quoted off a half-reported cohort",
		   d.actual_survival_pct)
		ck("Not yet measurable" in (d.survival_basis or ""),
		   "and the plan says why", (d.survival_basis or "")[:70])

		print("\n=== 3b. a week whose consignments are all reported ===")
		fed = [r for r in d.intake if str(r.deliver_on) == str(wk.deliver_on)]
		print("   %d consignments feed %s: %s" % (
			len(fed), wk.deliver_on, [(r.form, cint(r.units)) for r in fed]))
		for r in fed:
			if cint(r.actual_units):
				continue
			a = frappe.new_doc("Summer Flower Propagation Actual")
			a.propagation_plan = d.name
			a.event = ("Roots Withdrawn" if r.form == "Roots" else "TC Received")
			a.actual_date = r.week_start_date
			a.against_week = r.week_start_date
			a.units = cint(r.units)
			a.flags.ignore_permissions = True
			a.insert(); a.submit()
		d = rebuild(name)
		row = next(r for r in d.weeks if str(r.deliver_on) == str(wk.deliver_on))
		ck(cint(d.actual_survival_pct) > 0,
		   "now the cohort is whole, survival is quoted",
		   round(float(d.actual_survival_pct), 1))
		ck(float(d.actual_survival_pct) < 200,
		   "and it is a believable number, not 468%",
		   round(float(d.actual_survival_pct), 1))
		ck("fully reported" in (d.survival_basis or ""),
		   "on a stated basis", (d.survival_basis or "")[:90])

	print("\n=== 4. cancelling the stock entry takes it back ===")
	se.flags.ignore_permissions = True
	se.cancel()
	d = rebuild(name)
	row = next(r for r in d.intake
	           if r.form == "Roots" and str(r.week_start_date) == str(when))
	ck(cint(row.actual_units) == 999999,
	   "with no ledger entry left, the typed record is counted after all",
	   row.actual_units)
	ck(row.recorded_from == "Typed In", "and it says so now", row.recorded_from)
