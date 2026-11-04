# Copyright (c) 2026, James Kiruga and Contributors

# import frappe
from frappe.tests import IntegrationTestCase


# On IntegrationTestCase, the doctype test records and all
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class IntegrationTestFieldActivity(IntegrationTestCase):
	"""Integration tests for FieldActivity."""

	pass
