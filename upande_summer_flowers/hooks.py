app_name = "upande_summer_flowers"
app_title = "Upande Summer Flowers"
app_publisher = "James Kiruga"
app_description = "Summer flower demand, production planning, motherstock and TC scheduling"
app_email = "james@upande.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext", "upande_core"]

# Fixtures
# ------------------
# The approval workflow and its roles travel with the app so a fresh site gets a
# working demand -> plan -> approve -> budget chain without manual setup.

fixtures = [
	{
		"dt": "Workflow",
		"filters": [["name", "in", ["Summer Flower Production Plan Approval"]]],
	},
	{
		"dt": "Workflow State",
		"filters": [["name", "in", ["Draft", "Pending Approval", "Approved", "Rejected"]]],
	},
	{
		"dt": "Workflow Action Master",
		"filters": [["name", "in", ["Submit for Approval", "Approve", "Reject", "Reopen"]]],
	},
	# The Workspace itself is NOT a fixture: it belongs to the module, so
	# developer_mode exports it to summer_flowers/workspace/ and migrate syncs it
	# from there. Shipping it both ways gives two sources of truth that drift --
	# and the one that matters is the `content` block layout, which decides what
	# the page actually draws.
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
# needs no external request, which the dashboards' CSP would block anyway.
add_to_apps_screen = [
	{
		"name": "upande_summer_flowers",
		"logo": "/assets/upande_summer_flowers/images/summer_flowers.svg",
		"title": "Summer Flowers",
		"route": "/summer-flowers-planning",
	}
]

# Setting app_logo_url would replace the navbar mark for the WHOLE site, not just
# this module, so it is deliberately left alone on a multi-module bench.
# app_logo_url = "/assets/upande_summer_flowers/images/summer_flowers.svg"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/upande_summer_flowers/css/upande_summer_flowers.css"
# app_include_js = "/assets/upande_summer_flowers/js/upande_summer_flowers.js"

# include js, css files in header of web template
# web_include_css = "/assets/upande_summer_flowers/css/upande_summer_flowers.css"
# web_include_js = "/assets/upande_summer_flowers/js/upande_summer_flowers.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "upande_summer_flowers/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "upande_summer_flowers/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "upande_summer_flowers.utils.jinja_methods",
# 	"filters": "upande_summer_flowers.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "upande_summer_flowers.install.before_install"
# after_install = "upande_summer_flowers.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "upande_summer_flowers.uninstall.before_uninstall"
# after_uninstall = "upande_summer_flowers.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "upande_summer_flowers.utils.before_app_install"
# after_app_install = "upande_summer_flowers.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "upande_summer_flowers.utils.before_app_uninstall"
# after_app_uninstall = "upande_summer_flowers.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "upande_summer_flowers.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "upande_summer_flowers.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Class Overrides
# ---------------
# Crop Cycle lives in upande_agriculture and carries live records for other crops.
# The subclass adds the summer flower lifecycle behind the
# custom_is_summer_flower_cycle flag and, for those cycles only, names them per
# block-planting instead of per greenhouse -- the native `field:greenhouse` rule
# would otherwise collapse every block in a greenhouse into one record.

override_doctype_class = {
	"Crop Cycle": "upande_summer_flowers.summer_flowers.crop_cycle.SummerFlowerCropCycle",
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Block": {
		"validate": "upande_summer_flowers.summer_flowers.block.validate_block",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"upande_summer_flowers.tasks.all"
# 	],
# 	"daily": [
# 		"upande_summer_flowers.tasks.daily"
# 	],
# 	"hourly": [
# 		"upande_summer_flowers.tasks.hourly"
# 	],
# 	"weekly": [
# 		"upande_summer_flowers.tasks.weekly"
# 	],
# 	"monthly": [
# 		"upande_summer_flowers.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "upande_summer_flowers.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "upande_summer_flowers.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "upande_summer_flowers.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "upande_summer_flowers.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["upande_summer_flowers.utils.before_request"]
# after_request = ["upande_summer_flowers.utils.after_request"]

# Job Events
# ----------
# before_job = ["upande_summer_flowers.utils.before_job"]
# after_job = ["upande_summer_flowers.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"upande_summer_flowers.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

