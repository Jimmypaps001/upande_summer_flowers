# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""One step on the way from bought material to a plant in the ground.

A crop's route is an ordered list of these rather than a name from a fixed list,
because the farm's nine routes are nine paths through the same small set of
stages -- TC to motherstock to plants; TC to roots to propagation to plants;
roots of our own into cooling and straight out; plants bought ready to go in.
Written as an enum, a tenth route is a code change and the lead time and the
losses get spelled out nine times over. Written as stages, a route is data: the
lead time to a planted bed is the sum of the weeks along it, and what has to be
started with is what survives the losses back up it.
"""

from frappe.model.document import Document


class CropMaterialStage(Document):
	pass
