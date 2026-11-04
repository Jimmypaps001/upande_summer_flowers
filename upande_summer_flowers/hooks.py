app_name = "upande_summer_flowers"
app_title = "Upande Summer Flowers"
app_publisher = "James Kiruga"
app_description = "Summer flower demand, production planning, motherstock and TC scheduling"
app_email = "james@upande.com"
app_license = "mit"

# Apps

# Commented out so the app installs on a bench that does not carry these apps.

# Fixtures

fixtures = [
	{
		# Our own propagation units are warehouses of this type; route cards, the
		"dt": "Warehouse Type",
		"filters": [["name", "in", ["Propagation Unit"]]],
	},
	{
		# All three workflows this module owns, not just the plan's. The other two
		"dt": "Workflow",
		# Crop Protocol Version Approval is deliberately absent. A version is no
		"filters": [["name", "in", [
			"Summer Flower Production Plan Approval",
			"Planting Calendar Approval",
			# The protocol's own approval. Every state change to a protocol goes
			"Crop Protocol Approval",
		]]],
	},
	{
		"dt": "Workflow State",
		"filters": [["name", "in", ["Draft", "Pending Approval", "Approved",
		                            "Rejected", "Active", "Superseded", "Planted",
		                            "Uprooted", "Cancelled"]]],
	},
	{
		"dt": "Workflow Action Master",
		"filters": [["name", "in", ["Submit for Approval", "Approve", "Reject",
		                            "Reopen", "Revise", "Mark Planted",
		                            "Mark Uprooted", "Cancel Planting"]]],
	},
	{
		# The page itself. Like the Packhouse workspace this is modelled on, the
		"dt": "Custom HTML Block",
		"filters": [["name", "in", ["Summer Flowers Navigation"]]],
	},
	{
		# "Summer Flowers Planning" carries no module, so developer_mode cannot
		"dt": "Workspace",
		"filters": [["name", "in", ["Summer Flowers Planning"]]],
	},
	{
		"dt": "Workspace Sidebar",
		"filters": [["title", "in", ["Summer Flowers"]]],
	},
	{
		"dt": "Role",
		"filters": [["name", "in", ["Agriculture Manager", "Agriculture User"]]],
	},
]

# Shown on the /apps screen. The mark is an SVG so it stays sharp from 16px up and
add_to_apps_screen = [
	{
		"name": "upande_summer_flowers",
		"logo": "/assets/upande_summer_flowers/images/summer_flowers.svg",
		"title": "Summer Flowers",
		"route": "/desk/summer-flowers",
	}
]

# Setting app_logo_url would replace the navbar mark for the WHOLE site, not just

# Includes in <head>

# include js, css files in header of desk.html
app_include_js = [
	"/assets/upande_summer_flowers/js/sf_calendar.js",
	# The walkthrough launcher. Included app-wide rather than per doctype because
	"/assets/upande_summer_flowers/js/sf_walkthrough.js",
]

# Crop Protocol belongs to another app, so this is where its form script lives.
doctype_js = {
	"Crop Protocol": [
		"public/js/crop_protocol_walkthrough.js",
		"public/js/crop_protocol_ways.js",
		# Stems per Plant, Lifetime is an input: typing it rewrites every flush
		"public/js/crop_protocol_flush_shares.js",
	],
	# Crop Cycle belongs to upande_agriculture. The plants a cycle stands on were
	"Crop Cycle": "public/js/crop_cycle_planting.js",
}

# Block belongs to upande_propagation, so its list view is customised from here.
doctype_list_js = {"Block": "public/js/block_list.js"}

# Checked before the migrate that would trip over it, so a malformed customization
before_migrate = "upande_summer_flowers.summer_flowers.customization_check.before_migrate"

# after_migrate, not a patch, for the two things a patch cannot do:
after_migrate = [
	"upande_summer_flowers.summer_flowers.protocol_merge.after_migrate",
    "upande_summer_flowers.summer_flowers.tours.sync_form_tours",
	# The plant inspection template and its parameters are records, not schema, so
	"upande_summer_flowers.summer_flowers.setup.plant_quality.install",
	# The propagation units' capacity fields are schema; the figures on them are
	"upande_summer_flowers.summer_flowers.setup.propagation_units.install",
]

# include js, css files in header of web template

# include custom scss in every website theme (without file extension ".scss")

# include js, css files in header of web form

# include js in page

# include js in doctype views

# Svg Icons

# Home Pages

# application home page (will override Website Settings)

# website user home page (by Role)

# Generators

# automatically create page for each record of this doctype

# automatically load and sync documents of this doctype from downstream apps

# Jinja

# add methods and filters to jinja environment

# Installation

# before_install = "upande_summer_flowers.install.before_install"

# Uninstallation

# before_uninstall = "upande_summer_flowers.uninstall.before_uninstall"

# Integration Setup

# before_app_install = "upande_summer_flowers.utils.before_app_install"

# Integration Cleanup

# before_app_uninstall = "upande_summer_flowers.utils.before_app_uninstall"

# Build

# after_build = "upande_summer_flowers.build.after_build"

# Desk Notifications

# notification_config = "upande_summer_flowers.notifications.get_notification_config"

# Permissions

# permission_query_conditions = {

# Class Overrides

override_doctype_class = {
	# Bed, so a bed can live in a block with no greenhouse. The greenhouse path is
	"Bed": "upande_summer_flowers.summer_flowers.bed.SummerFlowerBed",
	"Crop Cycle": "upande_summer_flowers.summer_flowers.crop_cycle.SummerFlowerCropCycle",
}

# Document Events

doc_events = {
	# Crop Protocol is the variety master for every crop. The summer flower
	"Crop Protocol": {
		# Unconditional: it derives the summer-flower flag from crop_type, so it
		"before_validate": "upande_summer_flowers.summer_flowers.crop_protocol"
		                   ".before_validate",
		"validate": "upande_summer_flowers.summer_flowers.crop_protocol.validate",
		# The workflow moves the status; this turns reaching Approved into an actual
		"on_update": "upande_summer_flowers.summer_flowers.crop_protocol.on_update",
	},
	# A warehouse of type Propagation Unit is one of our own propagation units.
	"Warehouse": {
		"validate": "upande_summer_flowers.summer_flowers.setup.propagation_units.mark_unit",
	},
	"Block": {
		"validate": "upande_summer_flowers.summer_flowers.block.validate_block",
		"autoname": "upande_summer_flowers.summer_flowers.bed_naming.block_autoname",
	},
	# Bed is the unit of space, so its area has to be right and it has to have one
	"Bed": {
		"validate": "upande_summer_flowers.summer_flowers.bed.validate_bed",
		# Summer flower beds are in open blocks, not greenhouses, so they cannot be
		"autoname": "upande_summer_flowers.summer_flowers.bed_naming.bed_autoname",
		"before_validate":
			"upande_summer_flowers.summer_flowers.bed_naming.bed_farm_from_block",
	},
	# Material Request IS the crop input order sheet -- there is no parallel
	"Material Request": {
		"validate": "upande_summer_flowers.summer_flowers.material_request"
		            ".validate_input_request",
	},
	# Plant material is bought, so it arrives on a Purchase Receipt or a Purchase
	"Purchase Order": {
		"validate": "upande_summer_flowers.summer_flowers.plant_receipt"
		            ".set_line_context",
	},
	"Purchase Receipt": {
		"validate": "upande_summer_flowers.summer_flowers.plant_receipt"
		            ".set_line_context",
		"on_submit": "upande_summer_flowers.summer_flowers.plant_receipt"
		             ".refresh_plantings",
		"on_cancel": "upande_summer_flowers.summer_flowers.plant_receipt"
		             ".refresh_plantings",
	},
	"Purchase Invoice": {
		"validate": "upande_summer_flowers.summer_flowers.plant_receipt"
		            ".set_line_context",
		# before_submit, not on_submit: an inspection that is missing has to stop
		"before_submit": "upande_summer_flowers.summer_flowers.plant_receipt"
		                 ".require_inspection",
		"on_submit": "upande_summer_flowers.summer_flowers.plant_receipt"
		             ".refresh_plantings",
		"on_cancel": "upande_summer_flowers.summer_flowers.plant_receipt"
		             ".refresh_plantings",
	},
}

# Scheduled Tasks

# scheduler_events = {

# Testing

# before_tests = "upande_summer_flowers.install.before_tests"

# Extend DocType Class

# Overriding Methods

# exempt linked doctypes from being automatically cancelled

# Ignore links to specified DocTypes when deleting documents

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events

# Job Events

# User Data Protection

# user_data_fields = [

# Authentication and authorization

# auth_hooks = [

# Automatically update python controller files with type annotations for this app.

# default_log_clearing_doctypes = {

# Translation

