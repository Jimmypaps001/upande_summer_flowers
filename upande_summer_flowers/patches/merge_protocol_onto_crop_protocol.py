# Copyright (c) 2026, James Kiruga and contributors
"""Bring a site's data in line with the protocol merge (6fb2cbc)."""

from upande_summer_flowers.summer_flowers.protocol_merge import reconcile


def execute():
	reconcile()
