# Copyright (c) 2026, James Kiruga and contributors
"""Connections for a production plan.

Everything else in the Connections tab is a document that points AT the plan,
and the links table says so. The demand register is the other way round -- the
plan points at it -- so it is reached through the plan's own field, which is
what internal_links is for.
"""

from frappe import _


def get_data():
	return {
		"fieldname": "production_plan",
		"internal_links": {"Summer Flower Market Demand": "market_demand"},
		"transactions": [
			{"label": _("Demand"), "items": ["Summer Flower Market Demand"]},
		],
	}
