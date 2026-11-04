# Copyright (c) 2026, James Kiruga and contributors
"""Mobile API -- every endpoint the Upande Propagation app calls."""

import frappe
from frappe import _

from upande_summer_flowers.summer_flowers import crop_protocol as _protocol
from upande_summer_flowers.summer_flowers import plant_receipt as _receipt
from upande_summer_flowers.summer_flowers import planning_api as _planning
from upande_summer_flowers.summer_flowers import tc_supply as _supply


def _guard():
	"""Signed in, and not as Guest. Every endpoint below starts here."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)


# --------------------------------------------------------------------- scope
@frappe.whitelist()
def propagation_units():
	"""The warehouses that are propagation units, for the one-time setup screen."""
	_guard()
	return frappe.get_all(
		"Warehouse",
		filters={"custom_sf_is_propagation_unit": 1, "disabled": 0},
		pluck="name",
		order_by="name asc",
	)


@frappe.whitelist()
def propagation_plans():
	"""Propagation plans this phone can record against, newest season first."""
	_guard()
	return frappe.get_all(
		"Summer Flower Propagation Plan",
		filters={"status": ["!=", "Rejected"]},
		fields=["name", "variety", "farm", "season", "status"],
		order_by="season_start_year desc, modified desc",
	)


@frappe.whitelist()
def production_plans(variety=None, farm=None):
	"""Every production plan in scope, each labelled by crop and coverage."""
	_guard()
	return _planning.plans(variety=variety, farm=farm)


# ---------------------------------------------------------------- recording
@frappe.whitelist()
def propagation_detail(propagation_plan=None, plan=None, variety=None, farm=None):
	"""The plan as the unit works to it: consignments due, weeks, what is recorded."""
	_guard()
	return _planning.propagation_detail(
		plan=plan, propagation_plan=propagation_plan, variety=variety, farm=farm)


@frappe.whitelist()
def record_propagation_actual(propagation_plan, event, actual_date, units=0,
                              plants=0, against_week=None, reference=None,
                              supplier=None, warehouse=None, reason=None,
                              remarks=None):
	"""Write down one thing that happened in the unit."""
	_guard()
	return _planning.record_propagation_actual(
		propagation_plan=propagation_plan, event=event, actual_date=actual_date,
		units=units, plants=plants, against_week=against_week,
		reference=reference, supplier=supplier, warehouse=warehouse,
		reason=reason, remarks=remarks)


# ----------------------------------------------------------------- protocol
@frappe.whitelist()
def protocol_detail(version=None, plan=None, variety=None, farm=None):
	"""The protocol sheet. A plan names its own pinned version; else the one in force."""
	_guard()
	return _planning.protocol_detail(
		version=version, variety=variety, farm=farm, plan=plan)


@frappe.whitelist()
def save_protocol(version, changes, grades=None, change_reason=None,
                  effective_from=None):
	"""Apply protocol edits. An Active version is amended into a Draft, never edited."""
	_guard()
	return _planning.save_protocol(
		version=version, changes=changes, grades=grades,
		change_reason=change_reason, effective_from=effective_from)


@frappe.whitelist()
def submit_protocol_for_approval(protocol):
	"""Hand the protocol up. Approving is an Agriculture Manager's, in the desk."""
	_guard()
	return _protocol.submit_for_approval(protocol)


# -------------------------------------------------------------- motherstock
@frappe.whitelist()
def motherstock_line(plan=None, variety=None, farm=None):
	"""The motherstock behind a plan: the decision, and what it gives."""
	_guard()
	return _planning.motherstock_line(plan=plan, variety=variety, farm=farm)


@frappe.whitelist()
def motherstock_batches(variety=None, farm=None):
	"""The batches actually bought, and when."""
	_guard()
	return _planning.motherstock_batches(variety=variety, farm=farm)


# ----------------------------------------------------------------- arrivals
@frappe.whitelist()
def tc_suppliers(search=None, all=0):
	"""The labs this site can order from."""
	_guard()
	return _supply.tc_suppliers(search=search, all=all)


@frappe.whitelist()
def open_plant_orders(supplier=None, variety=None, farm=None, limit=40):
	"""Orders with plant material still to come."""
	_guard()
	return _receipt.open_plant_orders(
		supplier=supplier, variety=variety, farm=farm, limit=limit)


@frappe.whitelist()
def draft_plant_receipt(purchase_order, lines, posting_date=None,
                        rejected_warehouse=None, supplier_delivery_note=None,
                        bill_no=None, bill_date=None):
	"""What arrived, as a draft receipt. Stock moves when the office submits it."""
	_guard()
	return _receipt.draft_plant_receipt(
		purchase_order=purchase_order, lines=lines, posting_date=posting_date,
		rejected_warehouse=rejected_warehouse,
		supplier_delivery_note=supplier_delivery_note,
		bill_no=bill_no, bill_date=bill_date)


# ------------------------------------------------------------ the unit calendar
@frappe.whitelist()
def propagation_varieties(farm=None, season=None):
	"""Varieties that have a propagation plan, for the filter on the plans list."""
	_guard()
	return _planning.propagation_varieties(farm=farm, season=season)


@frappe.whitelist()
def propagation_schedule(propagation_plan=None, plan=None, variety=None,
                         farm=None, from_date=None, weeks=None):
	"""One variety's plan as dated work: order TC, stick, root, pot, harden, deliver."""
	_guard()
	return _planning.propagation_schedule(
		propagation_plan=propagation_plan, plan=plan, variety=variety,
		farm=farm, from_date=from_date, weeks=weeks)


@frappe.whitelist()
def delivery_schedule(farm=None, variety=None, from_date=None, weeks=None,
                      include_past=None):
	"""What the unit owes the farms: plants, farm, week, and what has gone."""
	_guard()
	return _planning.delivery_schedule(
		farm=farm, variety=variety, from_date=from_date, weeks=weeks,
		include_past=include_past)


@frappe.whitelist()
def farm_plan(farm, variety=None, from_date=None, weeks=None, include_past=None):
	"""What a farm expects to receive, week by week, and who raises each lot."""
	_guard()
	return _planning.farm_plan(farm=farm, variety=variety, from_date=from_date,
	                           weeks=weeks, include_past=include_past)


@frappe.whitelist()
def motherstock_plan(variety=None, farm=None, plan=None, propagation_plan=None):
	"""The motherstock line behind a variety: when it is cut, when multiplied."""
	_guard()
	return _planning.motherstock_plan(
		variety=variety, farm=farm, plan=plan, propagation_plan=propagation_plan)
