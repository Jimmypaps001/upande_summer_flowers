# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The simulator and the documents have to agree.

Every figure on this dashboard exists twice: once worked out live so a change can
be tried before it is committed, and once written onto a document when it is. That
is the design -- a what-if that had to save first would be no use -- but it is also
the failure this module has hit more than any other. The order-by date disagreed
with the TC journey; the cutting loss was applied twice in one path and once in the
other; the recommended quantity kept still while the pool it raised moved; per
hectare meant one thing on the snapshot and another on the card.

None of those were caught by anything, because nothing compared the two. This does.
It is deliberately not a test of whether the arithmetic is right -- the protocol
decides that, and it changes. It is a test that the two roads to the same number
arrive at the same place.

Every case runs against whatever this site actually has: an approved protocol
version, a submitted plan, its propagation plan. Where a site has none, the case
says so and skips rather than inventing a fixture, because a fixture would prove
the fixture.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt

from upande_summer_flowers.summer_flowers.doctype.crop_protocol_version.crop_protocol_version import (
	BED_SQM_PER_HA,
)
from upande_summer_flowers.summer_flowers.planning_api import (
	plan_whatif,
	propagation_detail,
	protocol_detail,
)


def _live_version():
	return frappe.db.get_value("Crop Protocol Version", {"version_status": "Active"}, "name")


def _live_plan():
	return frappe.db.get_value(
		"Summer Flower Production Plan", {"docstatus": ["<", 2]}, "name",
		order_by="creation desc")


class IntegrationTestSimulationParity(IntegrationTestCase):
	"""Two roads to one number, walked and compared."""

	def test_per_hectare_is_one_hectare(self):
		"""plants/ha, stems/ha and the density must describe the same hectare.

		The snapshot stores these and the sheet works them out. When the basis moved
		from 10,000 m2 of bed to BED_SQM_PER_HA of ground, the stored ones stayed on
		the old basis -- so a card could show a density quoted against one hectare
		beside a yield quoted against another, and the two looked equally official.
		"""
		version = _live_version()
		if not version:
			self.skipTest("no active protocol version on this site")

		sheet = protocol_detail(version=version)
		d, f = sheet["derived"], sheet["fields"]
		density = flt(f.get("plants_per_sqm_net"))
		if not density:
			self.skipTest("protocol carries no density")

		self.assertAlmostEqual(
			d["plants_per_ha"], density * BED_SQM_PER_HA, places=2,
			msg="plants per hectare is not the density times the bed in a hectare")

		# The life yield has to be the same hectare as the density above it.
		per_plant = flt(d.get("total_stems_per_plant_life"))
		if per_plant and d["plants_per_ha"]:
			self.assertAlmostEqual(
				d["stems_per_ha_life"] / d["plants_per_ha"], per_plant, places=2,
				msg="stems per hectare is quoted against a different hectare "
				    "from plants per hectare")

	def test_cycles_move_the_order_not_the_pool(self):
		"""The build-up lever divides the buy; it does not change what is grown.

		The pool the plan needs is fixed by the plan. Cycles decide how many
		plantlets it takes to raise that pool and how long it takes -- so doubling
		the rounds halves the order and lengthens the lead, and leaves the pool
		alone. For a while the order stood still while the pool moved, which is the
		same bug read from the other end.
		"""
		plan = _live_plan()
		if not plan:
			self.skipTest("no production plan on this site")

		runs = {}
		for cycles in (1, 2, 4):
			r = plan_whatif(plan=plan, cycles=cycles)
			if not r.get("plan") or not r.get("recommended_tc"):
				self.skipTest("plan has no propagation plan to size against")
			runs[cycles] = r

		pools = {c: r["mother_plants_from_order"] for c, r in runs.items()}
		self.assertEqual(
			len(set(pools.values())), 1,
			f"the pool moved with the cycles: {pools}")

		# Twice the rounds, half the plantlets, within a plantlet of rounding.
		self.assertAlmostEqual(
			runs[1]["recommended_tc"] / 2, runs[2]["recommended_tc"], delta=1,
			msg="two build-up rounds did not halve the order")
		self.assertAlmostEqual(
			runs[1]["recommended_tc"] / 4, runs[4]["recommended_tc"], delta=1,
			msg="four build-up rounds did not quarter the order")

		# A longer build-up is a longer lead, so the order is placed earlier.
		self.assertLess(
			str(runs[4]["order_date"]), str(runs[1]["order_date"]),
			"four rounds did not need ordering earlier than one")

	def test_recommended_order_raises_the_pool_the_plan_asked_for(self):
		"""What the sheet recommends must raise what the propagation plan needs.

		These are computed by different code on different documents: the
		recommendation runs through the version's tc_plants_for, the requirement is
		stored on the propagation plan. They are only allowed to differ by the
		rounding up of a plantlet.
		"""
		plan = _live_plan()
		if not plan:
			self.skipTest("no production plan on this site")
		r = plan_whatif(plan=plan)
		prop = propagation_detail(plan=plan)
		if not r.get("plan") or not prop.get("propagation_plan"):
			self.skipTest("plan has no propagation plan")

		needed = cint(prop["totals"].get("mother_plants"))
		if not needed:
			self.skipTest("propagation plan needs no new mother plants")

		raised = flt(r["mother_plants_from_order"])
		self.assertGreaterEqual(
			raised, needed - 1,
			f"the recommended order raises {raised:,.0f} mother plants against "
			f"{needed:,} required")
		# And it must not be wildly over: a whole extra cycle's worth would mean the
		# loss allowance or the factor is being applied twice, which it has been.
		self.assertLess(
			raised, needed * 1.5,
			f"the recommended order raises {raised:,.0f} for a pool of {needed:,} "
			"-- a loss or a factor is being applied more than once")

	def test_weekly_cuttings_foot_to_the_stated_total(self):
		"""The propagation weeks have to add up to the total stated above them.

		The dashboard now foots this table on screen, so a total that came from
		somewhere else would be visibly wrong. It came from somewhere else.
		"""
		plan = _live_plan()
		if not plan:
			self.skipTest("no production plan on this site")
		prop = propagation_detail(plan=plan)
		if not prop.get("propagation_plan") or not prop.get("weeks"):
			self.skipTest("plan has no propagation weeks")

		summed = sum(cint(w["cuttings_required"]) for w in prop["weeks"])
		stated = cint(prop["totals"]["cuttings_required"])
		self.assertEqual(
			summed, stated,
			f"the weeks add to {summed:,} and the total says {stated:,}")

		plants = sum(cint(w["plants_to_stick"]) for w in prop["weeks"])
		self.assertEqual(
			plants, cint(prop["totals"]["plants_to_stick"]),
			"the weeks' plants to stick do not add to the stated total")


def report():
	"""Run the parity checks against this site and print what they found.

	The same assertions as the test case, reachable without the test runner:

	    bench --site SITE execute \
	        upande_summer_flowers.summer_flowers.test_simulation_parity.report

	A site holding real data should not have testing switched on just to answer
	"do these two figures still agree", and a check nobody can run is a check
	nobody runs.
	"""
	import unittest

	suite = unittest.TestLoader().loadTestsFromTestCase(IntegrationTestSimulationParity)
	res = unittest.TextTestRunner(verbosity=2).run(suite)
	print("\n%d checks, %d failed, %d errored, %d skipped"
	      % (res.testsRun, len(res.failures), len(res.errors), len(res.skipped)))
	for t, why in res.skipped:
		print("  skipped: %s -- %s" % (t._testMethodName, why))
	for t, tb in res.failures + res.errors:
		print("  %s\n%s" % (t._testMethodName, tb.strip().split("\n")[-1]))
	frappe.db.rollback()
	return not (res.failures or res.errors)
