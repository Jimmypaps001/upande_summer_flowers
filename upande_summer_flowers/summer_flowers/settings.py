# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Farm policy that used to be written into the source.

Each reader falls back to the figure the code carried before, so a site that has
set nothing behaves exactly as it did.
"""

import frappe
from frappe.utils import cint, flt

DOCTYPE = "Summer Flower Settings"


def _get(field, default):
	"""The site's figure, or the one the code carried before.

	Asked of the meta first. get_single_value queues "Field X does not exist"
	into the message log BEFORE it raises, so catching the exception is not
	enough -- the message still reaches the browser, and a dashboard carrying a
	red error about a setting nobody has set is worse than the default it
	silently falls back to.
	"""
	try:
		if not frappe.get_meta(DOCTYPE).has_field(field):
			return default
		v = frappe.db.get_single_value(DOCTYPE, field)
	except Exception:
		return default
	return v if v not in (None, "", 0) else default


def season_first_week(default=27):
	"""The ISO week a season starts in."""
	return cint(_get("season_first_week", default))


def bed_sqm_per_ha(default=6667):
	"""Net bed in a hectare of ground: paths and headlands take the rest."""
	return flt(_get("bed_sqm_per_ha", default))


def lead_weeks(default=None):
	"""Supplier lead per bought material, as {stage: weeks}.

	The table in settings wins where it names a stage; anything it does not name
	keeps the figure the code carried.
	"""
	out = dict(default or {})
	try:
		rows = frappe.get_cached_doc(DOCTYPE).get("stage_lead_weeks") or []
	except Exception:
		return out
	for r in rows:
		if r.get("stage"):
			out[r.get("stage")] = cint(r.get("lead_weeks"))
	return out


def second_way(default=None):
	"""Crops bought a second way: {variety: (route, share)}.

	The table wins for a variety it names; the rest keep the figure the code
	carried. Only ever a default -- apply_catalogue leaves a share somebody
	worked out alone.
	"""
	out = dict(default or {})
	try:
		rows = frappe.get_cached_doc(DOCTYPE).get("second_ways") or []
	except Exception:
		return out
	for r in rows:
		if r.get("variety") and r.get("route"):
			out[r.get("variety")] = (r.get("route"), flt(r.get("share_pct")))
	return out


def max_multiplications(default=4):
	"""How many times a motherstock line may be sent back to multiply."""
	return cint(_get("max_multiplications", default))


def propagation_unit_type(default="Propagation Unit"):
	"""The Warehouse Type that marks one of our own propagation units."""
	return _get("propagation_unit_type", default)


def _roles(field, default):
	try:
		rows = frappe.get_cached_doc(DOCTYPE).get(field) or []
	except Exception:
		return tuple(default)
	roles = tuple(r.role for r in rows if r.get("role"))
	return roles or tuple(default)


def approver_roles(default=("Farm Manager", "Agriculture Manager", "System Manager")):
	"""Roles that may approve protocols, blocks, block changes, crop cycles and budgets."""
	return _roles("approver_roles", default)


def target_roles(default=("Production Manager", "Farm Manager", "Agriculture Manager",
                          "System Manager")):
	"""Roles that may set a crop cycle's weekly targets."""
	return _roles("target_roles", default)


def lift_share_tolerance(default=0.5):
	"""How far lift shares may be off 100% and still count as a whole consignment."""
	return flt(_get("lift_share_tolerance_pct", default))


def part_trough_pct(default=25):
	"""How much of a trough is too little to be worth sticking.

	A batch that runs a little over a trough does not open another one for the
	remainder: a nearly empty trough costs a bench, a stick and a lift for a few
	hundred plants. The overflow is taken off an older or an upcoming lift, and
	where neither has room the planting simply goes short.
	"""
	return flt(_get("part_trough_pct", default))
