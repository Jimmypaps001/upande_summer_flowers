# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""One growth stage of a summer flower crop.

A copy of Crop Protocol Growth Stage as it stood, owned here from now on. That
table belongs to upande_agriculture and has been reshaped twice under this app --
the boundary pair days_from/days_to/weeks gave way to a mandatory days_to_harvest,
and then to three fields with no stage_order at all. Each time, code that wrote
the columns it expected wrote into nothing.
"""

from frappe.model.document import Document


class SummerFlowerGrowthStage(Document):
	pass
