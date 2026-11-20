"""Try the column drop again, now the routes state their own losses.

drop_the_retired_loss_and_supply_columns refuses while any route still says
nothing, because it would be throwing away the only copy of a figure. On a
site where the carry-over found nothing to carry it rightly refused -- and,
having been recorded as run, would never try again. carry_the_losses_the_
versions_kept has since given the routes their figures, so it is asked once
more. Where it already dropped the columns this finds nothing and says so.
"""

from upande_summer_flowers.patches.drop_the_retired_loss_and_supply_columns import (
	execute as drop_them,
)


def execute():
	drop_them()
