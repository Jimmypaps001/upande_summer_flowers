"""Put right the lift and loss figures on active crop protocol versions.

Two things were found wrong across the active versions:

  Losses. Each stage's loss comes from its way's figures (rooting success,
  field establishment, order loss). Where the ways table does not line up
  with the route -- a row missing, a row for a way the route no longer has --
  no way answers for the stage and it loses nothing, even though the version
  states its rooting success. The plan then plants as if nothing dies.

  Lifts. Where plants are lifted in waves the plan plants from the lifts, but
  the stage weeks were left at a default (roots: 7 weeks on the bench, first
  lift at 16). Anything reading the stage weeks -- when to order roots, when
  the bench is ready -- was working to the wrong week. Lift rows with no way
  named were also shared between both ways in.

The repair does not edit a version in place: an active version is the record
of what was approved. It amends it -- a new version with the figures put
right, active from tomorrow, the old one superseded -- the same way any other
correction to an active version is made.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers import crop_protocol as cp

VERSION = "Crop Protocol Version"
FORM_OF_ENTRY = {"TC": "Tissue Culture", "Roots": "Roots", "Seeds": "Seeds"}
REASON = "Lift weeks and losses put right: ways lined up with the route"


def _kind(stage):
	return frappe.get_cached_value("Material Stage", stage, "kind") if stage else None


def _route_stages():
	"""{material route: (its stages)}"""
	out = {}
	for name in frappe.get_all("Material Route", pluck="name"):
		out[name] = tuple(frappe.get_all("Material Route Stage", filters={"parent": name},
		                                 pluck="stage", order_by="idx"))
	return out


def _route_for(names, ways, routes):
	"""The material route a chain of stages is, preferring one the ways already name."""
	for w in ways:
		if w.route and routes.get(w.route) == tuple(names):
			return w.route
	used = {}
	for r in frappe.get_all("Crop Protocol Way", fields=["route"]):
		used[r.route] = used.get(r.route, 0) + 1
	matches = [r for r, st in routes.items() if st == tuple(names)]
	matches.sort(key=lambda r: (r == "Test", -used.get(r, 0), r))
	return matches[0] if matches else None


def _stated(chain, which):
	"""The survival % a chain's stages already state, or 0 where they state none."""
	for st, _steps in chain:
		if which == "establishment" and _kind(st.stage) == "End" and flt(st.loss_pct):
			return 100.0 - flt(st.loss_pct)
		if which == "rooting" and st.stage in cp.ROOTING_LOSSES and flt(st.loss_pct):
			return 100.0 - flt(st.loss_pct)
	return 0.0


def _snapshot(v):
	chains = cp.route_chains(v)
	return {
		"ways": [(w.route, flt(w.share_pct), flt(w.rooting_success_pct),
		          flt(w.field_establishment_pct)) for w in (v.get("ways") or [])],
		"chains": [[(st.stage, cp.block_weeks(st, steps), flt(st.loss_pct)) for st, steps in c]
		           for c in chains],
		"rooting_success_pct": flt(v.get("rooting_success_pct")),
		"field_establishment_pct": flt(v.get("field_establishment_pct")),
	}


def fix(v):
	"""Put the figures right on an unsaved/draft version. Returns what changed."""
	routes = _route_stages()
	chains = cp.route_chains(v)
	ways = list(v.get("ways") or [])
	notes = []

	# 1. One way per chain, in the chain's order, each naming its route.
	rows = []
	for chain in chains:
		names = [st.stage for st, _s in chain]
		entry = chain[0][0]
		route = _route_for(names, ways, routes)
		had = next((w for w in ways if route and w.route == route), None)
		row = {
			"route": route,
			"bought": 1 if _kind(entry.stage) == "Bought" else 0,
			"share_pct": flt(had.share_pct) if had and flt(had.share_pct) else flt(entry.get("share_pct")),
			"lead_weeks": cint(had.lead_weeks) if had and cint(had.lead_weeks) else cint(entry.get("lead_weeks")),
			"plants_per_unit": flt(had.plants_per_unit) if had and flt(had.plants_per_unit) else 1.0,
			"rate": flt(had.rate) if had and flt(had.rate) else flt(entry.get("rate")),
			"order_loss_pct": flt(had.order_loss_pct) if had and had.order_loss_pct is not None
			else flt(entry.get("loss_pct")),
			# A way nobody gave a figure keeps what its stages already lose; only
			# where they lose nothing does it take the version's own figure.
			"rooting_success_pct": (flt(had.rooting_success_pct) if had and flt(had.rooting_success_pct)
			                        else _stated(chain, "rooting") or flt(v.get("rooting_success_pct"))),
			"field_establishment_pct": (flt(had.field_establishment_pct)
			                            if had and flt(had.field_establishment_pct)
			                            else _stated(chain, "establishment")
			                            or flt(v.get("field_establishment_pct"))),
		}
		if not had:
			notes.append(_("way added for {0}").format(route or " → ".join(names)))
		rows.append(row)
	gone = [w.route for w in ways if w.route not in [r["route"] for r in rows]]
	if gone:
		notes.append(_("way(s) the route no longer has removed: {0}").format(", ".join(gone)))
	total = sum(r["share_pct"] for r in rows)
	if len(rows) == 1:
		rows[0]["share_pct"] = 100.0
	elif total <= 0:
		for r in rows:
			r["share_pct"] = round(100.0 / len(rows), 2)
	elif abs(total - 100.0) > 0.01:
		for r in rows:
			r["share_pct"] = round(r["share_pct"] * 100.0 / total, 2)
		notes.append(_("shares added to {0}%, scaled to 100%").format(round(total, 2)))
	v.set("ways", rows)
	for chain, row in zip(chains, rows):
		chain[0][0].share_pct = row["share_pct"]

	# 2. Lift rows say which way they belong to.
	by_form = {FORM_OF_ENTRY.get(c[0][0].stage): r["route"] for c, r in zip(chains, rows)}
	for lift in v.get("lifts") or []:
		if not lift.route and by_form.get(lift.form):
			lift.route = by_form[lift.form]
			notes.append(_("lift {0} ({1}) given to {2}").format(lift.lift_no, lift.form, lift.route))

	# 3. Losses, the plan's own rule, now that every chain has its way.
	cp.set_route_quantities(v, native=True)

	# 4. A lifted way is ready on the bench at its first lift.
	chains = cp.route_chains(v)
	for chain, row in zip(chains, rows):
		names = [st.stage for st, _s in chain]
		lifts = [x for x in (v.get("lifts") or []) if x.route == row["route"]]
		if not lifts:
			lifts = [x for x in (v.get("lifts") or []) if not x.route] if len(chains) == 1 else []
		if not lifts:
			continue
		first = min(cint(x.weeks_after_arrival) for x in lifts)
		bench = [(st, steps) for st, steps in chain
		         if _kind(st.stage) not in ("Bought", "Taken from own crop", "End")]
		if not bench or bench[-1][1]:
			continue
		have = sum(cp.block_weeks(st, steps) for st, steps in bench)
		if have != first:
			last = bench[-1][0]
			last.weeks = max(0, cint(last.weeks) + first - have)
			notes.append(_("{0}: {1} on the bench was {2} weeks, the first lift is at {3}").format(
				row["route"], last.stage, have, first))
			if len(chains) == 1:
				v.sticking_to_planting_weeks = first
	return notes


def needs_fixing(v):
	"""What fix() would change on this version, without changing it."""
	probe = frappe.copy_doc(v)
	before = _snapshot(v)
	notes = fix(probe)
	after = _snapshot(probe)
	return notes if (notes or before != after) else [], before, after


def _tell_propagation(old, new):
	"""The propagation protocols follow: the new version's in, the old one's retired."""
	try:
		from upande_propagation import summer_flowers as prop
	except ImportError:
		return
	if prop.available():
		prop.sync_version(new)
		prop.sync_version(old)


@frappe.whitelist()
def repair(apply=0, versions=None):
	"""Find, and with apply=1 amend, every active version whose lifts or losses are wrong."""
	frappe.only_for(("System Manager", "Agriculture Manager"))
	names = frappe.parse_json(versions) if versions else frappe.get_all(
		VERSION, filters={"version_status": "Active"}, pluck="name")
	out = []
	for name in names:
		v = frappe.get_doc(VERSION, name)
		notes, before, after = needs_fixing(v)
		if not notes and before == after:
			continue
		row = {"version": name, "notes": notes, "before": before, "after": after}
		if cint(apply):
			start = add_days(getdate(nowdate()), 1)
			if v.effective_from and getdate(v.effective_from) >= start:
				start = add_days(getdate(v.effective_from), 1)
			new = frappe.get_doc(VERSION, v.create_amendment(change_reason=REASON,
			                                                 effective_from=start))
			fix(new)
			new.flags.ignore_permissions = True
			new.save()
			new.reload()
			new.on_activate()
			_tell_propagation(name, new.name)
			row["new_version"] = new.name
			row["saved"] = _snapshot(frappe.get_doc(VERSION, new.name))
		out.append(row)
	return out
