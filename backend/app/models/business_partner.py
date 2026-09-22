"""统一往来单位主档及其与业务事实的可审计关联。

采购、发票、银行流水和销售来源各自保留原始名称、税号和账号；本模块只维护
"这些原始事实归属哪个往来单位"，不复制、更不改写来源事实。这样同一单位的
历史别名可以收敛到一个档案，同时名称相似但证据不足的记录仍可留在待确认队列。
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class BusinessPartner(Base, PkMixin, TimestampMixin):
    """供应商、客户及其他收付款对象共用的唯一主档。"""

    __tablename__ = "business_partners"

    # 旧供应商档案只作为历史入口，不再承担跨财务来源的唯一身份。
    # 仅保留历史 Supplier ID 作为兼容/审计值，不再建立反向 FK。
    # Canonical 依赖方向必须单向：Supplier.partner_id -> BusinessPartner.id。
    legacy_supplier_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    tax_no: Mapped[str] = mapped_column(String(64), default="", index=True)
    contact: Mapped[str] = mapped_column(String(256), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    address: Mapped[str] = mapped_column(String(512), default="")
    bank_name: Mapped[str] = mapped_column(String(128), default="")
    bank_account_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    bank_account_name: Mapped[str] = mapped_column(String(256), default="")
    # 结构化银行账户主档。bank_* 三个旧字段继续镜像“主账户”，保证旧接口/报表兼容。
    # [{bank_name, account_no, account_name, is_primary}]
    bank_accounts: Mapped[list] = mapped_column(JSONB, default=list)
    # supplier / customer / counterparty；角色可以并存，不以菜单位置决定身份。
    roles: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class BusinessPartnerRole(Base, PkMixin, TimestampMixin):
    """一个真实主体可同时扮演 supplier/customer/counterparty 等多个角色。"""

    __tablename__ = "business_partner_roles"
    __table_args__ = (
        UniqueConstraint("partner_id", "role", name="uq_business_partner_role"),
    )

    partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("business_partners.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default="system", nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BusinessPartnerBankAccount(Base, PkMixin, TimestampMixin):
    """往来主体的结构化多银行账户；JSON 字段仅保留兼容，不再作为最终事实源。"""

    __tablename__ = "business_partner_bank_accounts"
    __table_args__ = (
        UniqueConstraint(
            "partner_id", "normalized_account_no",
            name="uq_business_partner_bank_account",
        ),
    )

    partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("business_partners.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bank_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    account_no: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_account_no: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default="system", nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class BusinessPartnerIdentifier(Base, PkMixin, TimestampMixin):
    """往来单位的名称别名、税号、银行账号、客户编码等识别证据。"""

    __tablename__ = "business_partner_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "partner_id", "kind", "normalized_value",
            name="uq_business_partner_identifier_value",
        ),
    )

    partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("business_partners.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # name / alias / tax_no / bank_account / customer_code
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="system")


class BusinessPartnerDuplicateReview(Base, PkMixin, TimestampMixin):
    """人工对“疑似同一主体”两个档案的判断结果（双向各存一行）。"""

    __tablename__ = "business_partner_duplicate_reviews"
    __table_args__ = (
        UniqueConstraint(
            "partner_id", "other_partner_id",
            name="uq_business_partner_duplicate_pair",
        ),
    )

    partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("business_partners.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    other_partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("business_partners.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # same / different
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(128), default="")


class BusinessPartnerLink(Base, PkMixin, TimestampMixin):
    """一条来源业务事实与往来单位的关联（或待确认关联）。"""

    __tablename__ = "business_partner_links"
    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_id", "relation_role",
            name="uq_business_partner_link_source_role",
        ),
    )

    partner_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("business_partners.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # external_purchase_order / alibaba1688_order / inbound_document /
    # tax_invoice / bank_transaction / sales_order 等。
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # supplier / customer / counterparty / seller / buyer，允许同一张发票的两侧分别归档。
    relation_role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_name: Mapped[str] = mapped_column(String(256), default="")
    raw_tax_no: Mapped[str] = mapped_column(String(64), default="")
    raw_account_no: Mapped[str] = mapped_column(String(128), default="")
    # linked / needs_review / ignored；待确认记录不强行归入任何档案。
    status: Mapped[str] = mapped_column(String(24), default="linked", index=True)
    # legacy_supplier / tax_no / bank_account / exact_name / alias / manual / source_created
    match_method: Mapped[str] = mapped_column(String(32), default="")
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    candidate_partner_ids: Mapped[list] = mapped_column(JSONB, default=list)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
