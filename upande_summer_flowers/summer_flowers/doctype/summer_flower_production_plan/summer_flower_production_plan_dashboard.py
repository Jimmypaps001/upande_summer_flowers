# Copyright (c) 2026, James Kiruga and contributors
"""Connections for a production plan.

Everything here is a document that points AT the plan through its own
`production_plan` field, so the links table finds them by that one fieldname.
The demand register is the other way round -- the plan points at it -- so it is
reached through the plan's own field, which is what internal_links is for.

Grouped the way the season runs rather than alphabetically: what was asked
for, what it commits us to buy, what it plans, and who raises it.
"""

from frappe import _


def get_data():
	return {
		"fieldname": "production_plan",
		"internal_links": {"Summer Flower Market Demand": "market_demand"},
		"transactions": [
			{
				"label": _("Demand"),
				"items": ["Summer Flower Market Demand"],
			},
			{
				"label": _("Buy"),
				"items": ["Summer Flower Procurement Plan"],
			},
			{
				"label": _("Plan"),
				"items": [
					"Summer Flower Season Plan",
					"Summer Flower Budget",
				],
			},
			{
				"label": _("Raise"),
				"items": [
					"Summer Flower Propagation Plan",
					"Summer Flower Motherstock Plan",
					"Summer Flower Motherstock Batch",
					"Trough Setting",
				],
			},
		],
	}
