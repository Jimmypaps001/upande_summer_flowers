# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""One cohort's plant material: how many, by when, and how it is being got.

The method-agnostic line between a production plan and the documents that fulfil
it. Whether a cohort is bought or propagated changes which document it resolves
into and nothing else -- the dates, the quantities and the reconciliation are the
same either way, so they are worked out once, here, rather than twice in two
places that then drift.
"""

from frappe.model.document import Document


class PlantingMaterialRequirement(Document):
	pass
