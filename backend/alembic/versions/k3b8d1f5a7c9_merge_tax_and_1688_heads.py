"""merge the tax ledger and 1688 import migration branches

Revision ID: k3b8d1f5a7c9
Revises: j2a7c9e4f6b8, ccb1acb478e6
Create Date: 2026-09-02

The 1688 file-import migration and the tax-invoice ledger were created from
the same shop-order schema revision.  This no-op merge keeps both migrations
and restores a single upgrade head without rewriting either branch.
"""

from typing import Sequence


revision: str = "k3b8d1f5a7c9"
down_revision: tuple[str, str] = ("j2a7c9e4f6b8", "ccb1acb478e6")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
