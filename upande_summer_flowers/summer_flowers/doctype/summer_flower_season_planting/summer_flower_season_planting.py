# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""One planting of a season, read off its Planting Calendar.

Nothing is typed here and nothing is computed here: the row is a view of the
calendar entry it names, rebuilt whenever the season plan is. See
summer_flower_season_plan.py for why the two documents exist.
"""

from frappe.model.document import Document


class SummerFlowerSeasonPlanting(Document):
	pass
