"""Registry index for the 6 V2 Asset Management vertical starter packs."""

from __future__ import annotations

from opendv.sources.starter_packs.verticals_alts import (
    custody_clearing,
    private_credit_direct_lending,
    private_equity_buyout,
)
from opendv.sources.starter_packs.verticals_public import (
    fixed_income_rates,
    multi_asset_ibor,
    public_equities_oms,
)

VERTICAL_BUILDERS = {
    "public_equities_oms": public_equities_oms,
    "fixed_income_rates": fixed_income_rates,
    "multi_asset_ibor": multi_asset_ibor,
    "private_equity_buyout": private_equity_buyout,
    "private_credit_direct_lending": private_credit_direct_lending,
    "custody_clearing": custody_clearing,
}

__all__ = [
    "VERTICAL_BUILDERS",
    "custody_clearing",
    "fixed_income_rates",
    "multi_asset_ibor",
    "private_credit_direct_lending",
    "private_equity_buyout",
    "public_equities_oms",
]
