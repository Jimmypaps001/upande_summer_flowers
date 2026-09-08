# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Bring a site's data in line with the protocol merge (6fb2cbc).

The work lives in summer_flowers.protocol_merge, because after_migrate runs it
too. A patch alone is not enough: a site whose upande_agriculture predates
crop_type has nothing this can usefully do until agriculture is deployed, and a
patch that returned early would be recorded as run and never come back.
"""

from upande_summer_flowers.summer_flowers.protocol_merge import reconcile


def execute():
	reconcile()
