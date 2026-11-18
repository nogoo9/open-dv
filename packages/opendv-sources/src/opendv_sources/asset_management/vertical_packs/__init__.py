"""Vertical Starter Packs for Asset Management domains.

Defines the 6 specialized composable packs:
1. Public Equities / OMS (public_equities_oms)
2. Fixed Income / Rates (fixed_income_rates)
3. Multi-Asset / IBOR (multi_asset_ibor)
4. Private Equity / Buyout (private_equity_buyout)
5. Private Credit / Direct Lending (private_credit_direct_lending)
6. Custody / Clearing (custody_clearing)
"""

from __future__ import annotations

from opendv_sources.asset_management.vertical_packs.custody_clearing import (
    get_custody_clearing_concepts,
    get_custody_clearing_pack,
    get_custody_clearing_relationships,
)
from opendv_sources.asset_management.vertical_packs.fixed_income_rates import (
    get_fixed_income_rates_concepts,
    get_fixed_income_rates_pack,
    get_fixed_income_rates_relationships,
)
from opendv_sources.asset_management.vertical_packs.multi_asset_ibor import (
    get_multi_asset_ibor_concepts,
    get_multi_asset_ibor_pack,
    get_multi_asset_ibor_relationships,
)
from opendv_sources.asset_management.vertical_packs.private_credit_direct_lending import (
    get_private_credit_direct_lending_concepts,
    get_private_credit_direct_lending_pack,
    get_private_credit_direct_lending_relationships,
)
from opendv_sources.asset_management.vertical_packs.private_equity_buyout import (
    get_private_equity_buyout_concepts,
    get_private_equity_buyout_pack,
    get_private_equity_buyout_relationships,
)
from opendv_sources.asset_management.vertical_packs.public_equities_oms import (
    get_public_equities_oms_concepts,
    get_public_equities_oms_pack,
    get_public_equities_oms_relationships,
)

__all__ = [
    "get_custody_clearing_concepts",
    "get_custody_clearing_pack",
    "get_custody_clearing_relationships",
    "get_fixed_income_rates_concepts",
    "get_fixed_income_rates_pack",
    "get_fixed_income_rates_relationships",
    "get_multi_asset_ibor_concepts",
    "get_multi_asset_ibor_pack",
    "get_multi_asset_ibor_relationships",
    "get_private_credit_direct_lending_concepts",
    "get_private_credit_direct_lending_pack",
    "get_private_credit_direct_lending_relationships",
    "get_private_equity_buyout_concepts",
    "get_private_equity_buyout_pack",
    "get_private_equity_buyout_relationships",
    "get_public_equities_oms_concepts",
    "get_public_equities_oms_pack",
    "get_public_equities_oms_relationships",
]
