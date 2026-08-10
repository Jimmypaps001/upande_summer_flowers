# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Name the customization file that will break the next migrate or install.

frappe.modules.utils.sync_customizations walks every installed app, every module in
it, and every .json in that module's custom folder, and hands each one to
sync_customizations_for_doctype. That function reads three keys straight out of the
dict with no check that they are there:

    doctype           at the top of the function
    custom_fields     after the doctype-exists guard
    property_setters  just below it

Any of the three missing raises KeyError and takes the whole migrate down. Because
the loop never says which file it is holding, the traceback names only frappe's own
line -- on a bench with a hundred-odd customization files across thirty apps that
tells you nothing. This walks the same three loops, in the same order, under the
same gate, and prints the path.

Two gates, not one, because the two failures look different:

    migrate   reads a file only if it carries sync_on_migrate, so a malformed file
              without that flag can sit harmless for months
    install   reads every .json in the folder, flag or no flag

A file can therefore be fatal on a fresh install and invisible on every migrate.
Both are reported, and each says which one it will bite.

What is deliberately NOT called fatal: a file whose doctype is not installed on this
site. sync_customizations_for_doctype guards that case itself -- it prints "DocType
{0} does not exist" and returns -- so it is a quiet no-op, worth knowing about but
not a breakage. Calling it one would be crying wolf.

Dangling document links are reported separately again, as a warning rather than a
break. A customization file shipping a Document Link whose field does not exist in
the database is how "Unknown column 'custom_material_request'" got into an install
here, but the insert itself is unvalidated, so the failure surfaces later and
elsewhere. The link is certainly wrong; asserting exactly when it will bite is more
than the evidence supports.
"""

import json
import os

import frappe

# The keys sync_customizations_for_doctype reads without a guard, in the order it
# reads them. Kept as data because that is the whole contract being checked; if a
# future frappe adds a fourth, this list is the one thing to update.
REQUIRED_KEYS = ("doctype", "custom_fields", "property_setters")

FATAL = ("migrate", "install")


def _inspect(app, module, path):
	"""Return every problem in one customization file, as dicts."""
	found = []
	try:
		rel = os.path.relpath(path, os.path.dirname(frappe.get_app_path(app)))
	except Exception:
		rel = path

	def add(breaks, problem, detail=""):
		found.append(
			{
				"app": app,
				"module": module,
				"path": path,
				"file": rel,
				"breaks": breaks,
				"problem": problem,
				"detail": detail,
			}
		)

	try:
		with open(path) as f:
			raw = f.read()
	except OSError as e:
		add("migrate", "cannot be read", str(e))
		return found

	try:
		data = json.loads(raw)
	except ValueError as e:
		# json.loads runs before the sync_on_migrate gate, so this always breaks.
		add("migrate", "is not valid JSON", str(e))
		return found

	if not isinstance(data, dict):
		# .get() on a list raises AttributeError at the gate itself.
		add("migrate", "is not a JSON object", "found a %s" % type(data).__name__)
		return found

	# Which run reads this file at all.
	gate = "migrate" if data.get("sync_on_migrate") else "install"

	if "doctype" not in data:
		add(
			gate,
			"has no top-level doctype key",
			("keys present: %s" % ", ".join(sorted(data))) if data else "the file is empty",
		)
		# Everything below reads data["doctype"], so there is nothing more to say.
		return found

	doctype = data["doctype"]

	if not frappe.db.exists("DocType", doctype):
		# frappe guards this one itself and returns, so the file is simply ignored.
		add("skipped", "customises a doctype this site has not got", doctype)
		return found

	for key in REQUIRED_KEYS:
		if key not in data:
			add(gate, "has no top-level %s key" % key, "customising %s" % doctype)

	# A Document Link pointing at a field the database has not got. The row inserts
	# unvalidated and fails later, somewhere that never mentions this file.
	for row in data.get("links") or []:
		link_dt = row.get("link_doctype")
		link_field = row.get("link_fieldname")
		if not link_dt or not link_field:
			continue
		if not frappe.db.exists("DocType", link_dt):
			add("link", "links to a doctype this site has not got", "%s -> %s" % (doctype, link_dt))
			continue
		if not frappe.db.has_column(link_dt, link_field):
			add(
				"link",
				"links through a field the database has not got",
				"%s -> %s.%s" % (doctype, link_dt, link_field),
			)

	return found


def _folders():
	"""Yield (app, module, folder) for every custom folder sync_customizations reads.

	Deliberately goes through frappe.get_installed_apps() and app_modules rather than
	globbing the apps directory, so an app sitting on disk but not installed on this
	site is skipped here exactly as it is skipped there.
	"""
	for app in frappe.get_installed_apps():
		for module in frappe.local.app_modules.get(app) or []:
			try:
				folder = frappe.get_app_path(app, module, "custom")
			except Exception:
				continue
			if os.path.isdir(folder):
				yield app, module, folder


def scan():
	"""Walk sync_customizations' own three loops and collect what would break."""
	problems = []
	for app, module, folder in _folders():
		for fname in sorted(os.listdir(folder)):
			if fname.endswith(".json"):
				problems.extend(_inspect(app, module, os.path.join(folder, fname)))
	return problems


def counted():
	"""How many files were looked at, so a clean run reads differently from no run."""
	return sum(
		len([f for f in os.listdir(folder) if f.endswith(".json")]) for _, _, folder in _folders()
	)


def check():
	"""Print the report and return the problems.

	bench --site SITE execute \
	    upande_summer_flowers.summer_flowers.customization_check.check
	"""
	problems = scan()
	total = counted()

	groups = [
		("Will break migrate", [p for p in problems if p["breaks"] == "migrate"]),
		("Will break a fresh install", [p for p in problems if p["breaks"] == "install"]),
		("Dangling document links", [p for p in problems if p["breaks"] == "link"]),
		("Ignored: doctype not on this site", [p for p in problems if p["breaks"] == "skipped"]),
	]
	blocking = groups[0][1]

	print("%d customization file(s) checked." % total)
	if not problems:
		print("Nothing wrong with any of them.")
		return problems

	print("")
	for label, group in groups:
		if not group:
			continue
		print("%s (%d):" % (label, len(group)))
		for p in group:
			print("  %s" % p["file"])
			print("      %s%s" % (p["problem"], (": " + p["detail"]) if p["detail"] else ""))
		print("")

	if not blocking:
		print("Nothing here stops a migrate.")
	return problems


def before_migrate():
	"""Name the file before frappe trips over it.

	sync_customizations runs later in the same migrate and reads three keys out of
	every custom/*.json with no check that they are there. When one is missing it
	dies on `data["doctype"]` with a KeyError and no filename, and the migrate is
	over -- on a bench of a hundred-odd customization files across a dozen apps
	that identifies nothing.

	This runs first and says which file, in which app, and what is wrong with it.
	It refuses the migrate only for the faults that would stop it anyway: the
	migrate was going to fail either way, and failing with the path in the message
	is the whole point.

	Deliberately checks every installed app, not just this one. The file that stops
	the migrate is rarely in the app that notices.
	"""
	problems = scan()
	blocking = [p for p in problems if p["breaks"] == "migrate"]
	if not blocking:
		return

	lines = [
		"%s\n      %s%s" % (p["file"], p["problem"],
		                    (": " + p["detail"]) if p["detail"] else "")
		for p in blocking
	]
	frappe.throw(
		frappe._("sync_customizations would fail on {0} file(s) later in this "
		         "migrate, with a KeyError naming no file. They are:\n\n  {1}\n\n"
		         "Add the missing key, or delete the file if it holds nothing.").format(
			len(blocking), "\n  ".join(lines)),
		title=frappe._("Customization file will break this migrate"),
	)
