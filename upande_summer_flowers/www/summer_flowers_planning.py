# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Context for the planning dashboard.

The only thing it asks for is the full width of the window. Frappe's web
template wraps page content in a Bootstrap container unless a page says
otherwise, and this page is a working surface rather than an article: a
fifty-three week table of twelve columns was being squeezed into 1140px with
empty gutters either side of it.

The filename is underscored although the route is hyphenated, because that is
how Frappe looks a page controller up -- the hyphenated name is never imported.
"""


def get_context(context):
	context.full_width = True
