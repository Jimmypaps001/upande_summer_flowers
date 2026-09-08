# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Bring a site's data in line with the protocol merge (6fb2cbc).

The commit ships fields and rules. It cannot ship any of the following, because
a customization file adds and overrides but never deletes, and never touches a
row:

    Custom Fields shadowing a field agriculture defines natively
    crop_type's old values, which the new Select does not offer
    the discriminators derived from crop_type
    variety and variety_item, which are the same Item under two labels

Called from two places, on purpose.

The patch runs it once, which is enough for a site whose upande_agriculture is
current. A site whose agriculture is older has no crop_type at all -- marginpar
had a Crop Protocol of 27 fields from before the family was absorbed onto disk
-- and there is nothing useful to do there until agriculture is deployed. A
patch that returned early would be recorded as run and never come back, so
after_migrate calls it too: every function here is idempotent, so the site that
could not be fixed this time is fixed on the next migrate, and the site that was
already right reports nothing.

after_migrate is also the only phase that can drop a shadowing Custom Field,
because frappe.migrate syncs customisations after patches, not before -- so on
the migrate that creates the shadow, a patch has already been and gone.
"""

import frappe


# The old Select's values, and the two-way switch each becomes. Chrysanthemums
# go to the summer flower side: they are a cut flower, not a rose, and the fine
# grouping is on the variety Item either way.
CROP_TYPE = {
	"Spray Roses": "Roses",
	"Standard Roses": "Roses",
	"Rose": "Roses",
	"Spray Rose": "Roses",
	"Chrysanthemums": "Summer Flowers",
	"Summer Flower": "Summer Flowers",
}

# Fields this app once added to Crop Protocol and agriculture later defined
# natively. Both rows survive, so frappe.get_meta returns each field twice and
# the form asks the same question twice -- "Variety" and "Crop Type" appeared
# twice each. The natives are the ones to keep.
DUPLICATED = ("breeder", "crop_type", "variety_item")


def reconcile():
	if not frappe.db.table_exists("Crop Protocol"):
		return

	drop_duplicate_custom_fields()
	report_stranded_protocols()
	settle_crop_cycle_variety()

	if not frappe.db.has_column("Crop Protocol", "crop_type"):
		# marginpar was in exactly this state: a Crop Protocol of 27 fields from
		# before upande_agriculture absorbed the family onto disk, with no
		# crop_type, no variety_item and no protocol_name. Every field on the
		# form shows or hides on an eval of crop_type, so there is nothing this
		# patch can usefully do and a great deal it would break by trying.
		print("Crop Protocol on this site has no crop_type field, so the "
		      "Roses / Summer Flowers switch does not exist and no form will "
		      "populate. upande_agriculture here is older than this app expects: "
		      "deploy agriculture first, then migrate again. Nothing changed.")
		return

	# settle_variety first: rescue_unmapped_crop_type derives from the variety
	# Item's group, and on a site coming from an older agriculture variety_item
	# is empty until this fills it. Getting this order wrong on marginpar left
	# all 13 protocols with no crop_type and nothing to work it out from.
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
		# the column belongs to the DocField, so deleting the Custom Field takes
		# the duplicate off the form and leaves every value where it is.
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
	"""Anything the map did not cover still has to land on one of the two.

	This is the failure that would be hardest to diagnose: every field on the
	form now shows or hides on an eval of crop_type, so a record holding a value
	that is neither "Roses" nor "Summer Flowers" -- "Other", "Fillers", or
	nothing at all -- opens with an empty form and no clue why. crop_type is
	mandatory now too, so it cannot be saved without choosing.

	Two derivations before giving up, in order of how much they are worth
	trusting: the flag this app has always maintained, then the variety Item's
	own group, which agreed with crop_type on 236 of 237 records here. Anything
	left is named rather than guessed at.
	"""
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
	"""Summer flower protocols left behind on the retired doctype.

	The app reads Crop Protocol now. It does not move records, and
	protocol_split.move() only runs the other way, so anything still sitting on
	Summer Flower Protocol is invisible to planning rather than wrong. Naming it
	is the whole job here: protocol_split.move_back() does the copy, when
	somebody who can see both is satisfied.
	"""
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
	"""custom_is_summer_flower and custom_sf_crop_class now follow crop_type.

	before_validate keeps them right from here on, but only for a document that
	is saved, and a protocol nobody edits would keep whatever it was last given.
	"""
	if not frappe.db.has_column("Crop Protocol", "custom_is_summer_flower"):
		return
	# Only where crop_type actually says something. A protocol whose crop_type
	# is still unresolved is not a rose, it is unanswered, and writing 0 and
	# "Rose" onto it destroys the very flag rescue_unmapped_crop_type reads --
	# which is how marginpar's 13 Aster protocols came to be labelled roses.
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
	"""One variety, filled both ways.

	variety is what is typed and what the naming rule uses; variety_item is
	hidden and filled from it, because upande_agriculture reads that fieldname.
	"""
	for col in ("variety", "variety_item"):
		if not frappe.db.has_column("Crop Protocol", col):
			# One of the pair is not on this site, so there is nothing to
			# reconcile it with. An old agriculture has variety and no
			# variety_item; the current one is the other way round until this
			# app's customisations sync.
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
	"""Crop Cycle asked "Variety" twice too.

	custom_sf_variety is hidden now and filled from the native field, because
	crop_cycle_api filters on it directly.
	"""
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
# natively, or defines natively on an older build. Both rows survive, so
# frappe.get_meta returns the field twice and the form asks the same question
# twice -- "Variety", "Crop Type" and "Farm" each appeared twice at some point.
# The native is always the one to keep: the column belongs to the DocField, so
# dropping the Custom Field takes the duplicate off the form and leaves every
# value where it is.
SHADOWABLE = ("variety", "farm", "variety_item", "crop_type", "breeder",
              "weeks_to_pinch", "weeks_between_flushes", "total_weeks_in_ground",
              "yield_stems_per_ha")


def drop_shadowed_custom_fields(doctype="Crop Protocol"):
	"""Drop a Custom Field of ours that duplicates a native DocField.

	frappe.core.doctype.doctype.check_fieldname_conflicts only looks for clashes
	with methods and properties, not with an existing DocField, so a shadowing
	Custom Field is created in silence. That is how three of them came to exist
	on karenroses, and what would happen to variety and farm on a site whose
	agriculture still defines them.

	Deliberately narrow: only fields this app ships, only where a DocField of the
	same name exists, and only Custom Fields belonging to this module -- never
	somebody else's customisation and never one added by hand in the desk.
	"""
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
