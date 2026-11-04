# Copyright (c) 2026, James Kiruga and contributors
"""Bring a site's data in line with the protocol merge (6fb2cbc)."""

import frappe


# The old Select's values, and the two-way switch each becomes. Chrysanthemums
CROP_TYPE = {
	"Spray Roses": "Roses",
	"Standard Roses": "Roses",
	"Rose": "Roses",
	"Spray Rose": "Roses",
	"Chrysanthemums": "Summer Flowers",
	"Summer Flower": "Summer Flowers",
}

# Fields this app once added to Crop Protocol and agriculture later defined
DUPLICATED = ("breeder", "crop_type", "variety_item")


def reconcile():
	if not frappe.db.table_exists("Crop Protocol"):
		return

	drop_duplicate_custom_fields()
	report_stranded_protocols()
	settle_crop_cycle_variety()

	if not frappe.db.has_column("Crop Protocol", "crop_type"):
		# marginpar was in exactly this state: a Crop Protocol of 27 fields from
		print("Crop Protocol on this site has no crop_type field, so the "
		      "Roses / Summer Flowers switch does not exist and no form will "
		      "populate. upande_agriculture here is older than this app expects: "
		      "deploy agriculture first, then migrate again. Nothing changed.")
		return

	# settle_variety first: rescue_unmapped_crop_type derives from the variety
	settle_variety()
	move_crop_type_to_the_switch()
	rescue_unmapped_crop_type()
	derive_discriminators()


def drop_duplicate_custom_fields():
	for fieldname in DUPLICATED:
		name = frappe.db.get_value("Custom Field",
		                           {"dt": "Crop Protocol", "fieldname": fieldname}, "name")
		if not name:
			continue
		# Only ever safe because agriculture defines the same fieldname natively:
		if not frappe.db.exists("DocField", {"parent": "Crop Protocol",
		                                     "fieldname": fieldname}):
			continue
		frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True,
		                  delete_permanently=True)
		print("dropped duplicate Custom Field Crop Protocol-%s" % fieldname)


def move_crop_type_to_the_switch():
	for old, new in CROP_TYPE.items():
		n = frappe.db.count("Crop Protocol", {"crop_type": old})
		if not n:
			continue
		frappe.db.sql("""update `tabCrop Protocol` set crop_type = %s
		                 where crop_type = %s and name is not null""", (new, old))
		print("crop_type: %d x %r -> %r" % (n, old, new))


SWITCH = ("Roses", "Summer Flowers")


def rescue_unmapped_crop_type():
	"""Anything the map did not cover still has to land on one of the two."""
	has_item = frappe.db.has_column("Crop Protocol", "variety_item")
	rows = frappe.db.sql("""select name, crop_type,
	                               %s as variety_item,
	                               ifnull(custom_is_summer_flower, 0) as flag
	                        from `tabCrop Protocol`
	                        where ifnull(crop_type, '') not in %%(ok)s"""
	                     % ("variety_item" if has_item else "null"),
	                     {"ok": SWITCH}, as_dict=True)
	if not rows:
		return
	has_flag = frappe.db.has_column("Crop Protocol", "custom_is_summer_flower")
	stuck = []
	for r in rows:
		guess = None
		if has_flag and r.flag:
			guess = "Summer Flowers"
		elif has_flag and r.crop_type:
			# A value was typed and the flag says it is not a summer flower.
			guess = "Roses"
		if not guess and r.variety_item:
			group = frappe.db.get_value("Item", r.variety_item, "item_group") or ""
			if "Rose" in group:
				guess = "Roses"
			elif group:
				guess = "Summer Flowers"
		if not guess:
			stuck.append((r.name, r.crop_type))
			continue
		frappe.db.set_value("Crop Protocol", r.name, "crop_type", guess,
		                    update_modified=False)
		print("crop_type %r -> %r on %s" % (r.crop_type, guess, r.name))
	for name, was in stuck:
		print("Crop Protocol %s has crop_type %r and nothing to derive one from -- "
		      "its form will be empty until someone picks Roses or Summer Flowers"
		      % (name, was))


def report_stranded_protocols():
	"""Summer flower protocols left behind on the retired doctype."""
	if not frappe.db.table_exists("Summer Flower Protocol"):
		return
	names = frappe.get_all("Summer Flower Protocol", pluck="name")
	stranded = [n for n in names if not frappe.db.exists("Crop Protocol", n)]
	if not stranded:
		return
	print("%d protocol(s) are still only on Summer Flower Protocol and the app no "
	      "longer reads that doctype. Run "
	      "upande_summer_flowers.summer_flowers.protocol_split.move_back(dry_run=0) "
	      "to copy them onto Crop Protocol:" % len(stranded))
	for n in stranded[:20]:
		print("    " + n)
	if len(stranded) > 20:
		print("    ... and %d more" % (len(stranded) - 20))


def derive_discriminators():
	"""custom_is_summer_flower and custom_sf_crop_class now follow crop_type."""
	if not frappe.db.has_column("Crop Protocol", "custom_is_summer_flower"):
		return
	# Only where crop_type actually says something. A protocol whose crop_type
	decided = "crop_type in ('Roses', 'Summer Flowers')"
	wrong = decided + """ and custom_is_summer_flower !=
	         if(crop_type = 'Summer Flowers', 1, 0)"""
	n = frappe.db.sql("select count(*) from `tabCrop Protocol` where " + wrong)[0][0]
	if n:
		frappe.db.sql("""update `tabCrop Protocol`
		                 set custom_is_summer_flower = if(crop_type = 'Summer Flowers', 1, 0)
		                 where name is not null and """ + wrong)
		print("custom_is_summer_flower derived from crop_type on %d protocol(s)" % n)
	if frappe.db.has_column("Crop Protocol", "custom_sf_crop_class"):
		klass = decided + """ and ifnull(custom_sf_crop_class, '') !=
		         if(crop_type = 'Summer Flowers', 'Summer Flower', 'Rose')"""
		n = frappe.db.sql("select count(*) from `tabCrop Protocol` where " + klass)[0][0]
		if n:
			frappe.db.sql("""update `tabCrop Protocol`
			                 set custom_sf_crop_class = if(crop_type = 'Summer Flowers',
			                                               'Summer Flower', 'Rose')
			                 where name is not null and """ + klass)
			print("custom_sf_crop_class derived from crop_type on %d protocol(s)" % n)


def settle_variety():
	"""One variety, filled both ways."""
	for col in ("variety", "variety_item"):
		if not frappe.db.has_column("Crop Protocol", col):
			# One of the pair is not on this site, so there is nothing to
			return
	for target, source in (("variety", "variety_item"), ("variety_item", "variety")):
		gap = "ifnull(`%s`, '') = '' and ifnull(`%s`, '') != ''" % (target, source)
		n = frappe.db.sql("select count(*) from `tabCrop Protocol` where " + gap)[0][0]
		if not n:
			continue
		frappe.db.sql("update `tabCrop Protocol` set `%s` = `%s` where name is not null and %s"
		              % (target, source, gap))
		print("%s filled from %s on %d protocol(s)" % (target, source, n))


def settle_crop_cycle_variety():
	"""Crop Cycle asked "Variety" twice too."""
	if not frappe.db.table_exists("Crop Cycle"):
		return
	if not frappe.db.has_column("Crop Cycle", "custom_sf_variety"):
		return
	gap = "ifnull(custom_sf_variety, '') = '' and ifnull(variety, '') != ''"
	n = frappe.db.sql("select count(*) from `tabCrop Cycle` where " + gap)[0][0]
	if not n:
		return
	frappe.db.sql("update `tabCrop Cycle` set custom_sf_variety = variety "
	              "where name is not null and " + gap)
	print("Crop Cycle custom_sf_variety filled from variety on %d cycle(s)" % n)


# Fields this app once added to Crop Protocol and agriculture later defined
SHADOWABLE = ("variety", "farm", "variety_item", "crop_type", "breeder",
              "weeks_to_pinch", "weeks_between_flushes", "total_weeks_in_ground",
              "yield_stems_per_ha")


def drop_shadowed_custom_fields(doctype="Crop Protocol"):
	"""Drop a Custom Field of ours that duplicates a native DocField."""
	if not frappe.db.table_exists(doctype):
		return
	for fieldname in SHADOWABLE:
		if not frappe.db.exists("DocField", {"parent": doctype, "fieldname": fieldname}):
			continue
		name = frappe.db.get_value("Custom Field",
		                           {"dt": doctype, "fieldname": fieldname,
		                            "module": "Summer Flowers"}, "name")
		if not name:
			continue
		frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True,
		                  delete_permanently=True)
		print("dropped Custom Field %s-%s: agriculture defines it natively here"
		      % (doctype, fieldname))
		frappe.clear_cache(doctype=doctype)


def after_migrate():
	drop_shadowed_custom_fields()
	reconcile()
