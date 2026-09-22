"""吉客云 Web Adapter 的 raw 数据层（V1 六类核心数据）。

- 销售订单 / 销售明细：按下单时间窗口整体替换（与参考项目一致，避免明细无唯一业务键）。
- 采购入库主单 / 明细：doc_id / rec_id 幂等 upsert。
- 总库存 / 分仓库存：全量快照（单事务 DELETE+INSERT，失败回滚不清空旧库存）。
- ``raw`` 保留原始响应行，接口字段变化时可回溯重映射。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)
QUANTITY = Numeric(18, 4)


class JkyWebSalesOrder(Base, PkMixin, TimestampMixin):
    """销售订单主档（网页导出）。UNIQUE(trade_no) 幂等 upsert。"""

    __tablename__ = "jky_web_sales_orders"

    trade_no: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    trade_status: Mapped[str] = mapped_column(String(64), default="", index=True)
    settle_status: Mapped[str] = mapped_column(String(64), default="")
    shop_name: Mapped[str] = mapped_column(String(256), default="", index=True)  # 销售渠道（店铺）
    shop_cate_name: Mapped[str] = mapped_column(String(128), default="")  # 渠道分类
    source_trade_no: Mapped[str] = mapped_column(String(128), default="", index=True)  # 网店订单号
    trade_type: Mapped[str] = mapped_column(String(64), default="")
    trade_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    pay_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handle_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consign_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 发货时间
    warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    plat_warehouse_code: Mapped[str] = mapped_column(String(64), default="")
    logistic_name: Mapped[str] = mapped_column(String(128), default="")
    logistic_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    trade_count: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 货品数量
    goods_summary: Mapped[str] = mapped_column(Text, default="")  # 货品摘要
    merge_remarks: Mapped[str] = mapped_column(Text, default="")
    payment: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 应收合计
    real_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 实付金额
    customer_code: Mapped[str] = mapped_column(String(128), default="")
    customer_account: Mapped[str] = mapped_column(String(256), default="")
    customer_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    city: Mapped[str] = mapped_column(String(64), default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JkyWebSalesOrderItem(Base, PkMixin, TimestampMixin):
    """销售订单明细（销售单明细账导出）：SKU 级事实表。

    明细行无稳定外部唯一键，采用「按下单时间窗口整体替换」保证重复同步幂等。
    """

    __tablename__ = "jky_web_sales_order_items"
    __table_args__ = (
        Index("ix_jky_web_sales_items_trade_no", "trade_no"),
        Index("ix_jky_web_sales_items_goods_no", "goods_no"),
        Index("ix_jky_web_sales_items_trade_time", "trade_time"),
        Index("ix_jky_web_sales_items_shop", "shop_name"),
    )

    trade_no: Mapped[str] = mapped_column(String(128), nullable=False)
    source_trade_no: Mapped[str] = mapped_column(String(128), default="")
    shop_name: Mapped[str] = mapped_column(String(256), default="")
    shop_cate_name: Mapped[str] = mapped_column(String(128), default="")
    goods_no: Mapped[str] = mapped_column(String(128), default="")
    goods_name: Mapped[str] = mapped_column(Text, default="")
    barcode: Mapped[str] = mapped_column(String(128), default="", index=True)
    spec_name: Mapped[str] = mapped_column(String(256), default="")  # 规格
    brand_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    cate_name: Mapped[str] = mapped_column(String(256), default="")
    warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    logistic_name: Mapped[str] = mapped_column(String(128), default="")
    sell_count: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 数量
    sell_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 单价
    discount_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 优惠
    discount_rate: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 折扣
    sell_total: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 销售金额
    cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 成本金额
    after_share_unit_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 分摊后单价
    share_favourable_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 分摊金额
    after_share_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 分摊后金额
    other_share_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 费用分摊
    gross_profit: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 毛利
    gross_profit_rate: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 毛利率
    price1: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 零售价
    price6: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 含税价
    price7: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 不含税价
    trade_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pay_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer_code: Mapped[str] = mapped_column(String(128), default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JkyWebTotalStock(Base, PkMixin, TimestampMixin):
    """总库存快照：UNIQUE(sku_key) 全量替换。"""

    __tablename__ = "jky_web_total_stock"

    sku_key: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    sku_id: Mapped[str] = mapped_column(String(64), default="")
    goods_id: Mapped[str] = mapped_column(String(64), default="")
    goods_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(Text, default="")
    spec_name: Mapped[str] = mapped_column(String(256), default="")
    barcode: Mapped[str] = mapped_column(String(128), default="", index=True)
    brand_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    cate_name: Mapped[str] = mapped_column(String(256), default="")
    unit_name: Mapped[str] = mapped_column(String(64), default="")
    warehouse_id: Mapped[str] = mapped_column(String(64), default="")
    warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    current_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 库存数量
    locking_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 占用库存
    can_use_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 可用库存
    order_able_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    yesterday_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    week_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    threeday_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 近30天销量
    total_sale_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    price1: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 零售价
    price6: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 含税价
    price7: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 不含税价
    # 成本价 / 库存金额由分仓库存同步后聚合回填（allStockSkuList 不返回成本）
    cost_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cost_value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JkyWebWarehouseStock(Base, PkMixin, TimestampMixin):
    """分仓库存快照：UNIQUE(warehouse_key, sku_key) 全量替换。"""

    __tablename__ = "jky_web_warehouse_stock"
    __table_args__ = (
        UniqueConstraint("warehouse_key", "sku_key", name="uq_jky_web_wh_stock"),
        Index("ix_jky_web_wh_stock_goods_no", "goods_no"),
        Index("ix_jky_web_wh_stock_warehouse", "warehouse_name"),
    )

    warehouse_key: Mapped[str] = mapped_column(String(192), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(64), default="")
    warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    sku_key: Mapped[str] = mapped_column(String(192), nullable=False)
    sku_id: Mapped[str] = mapped_column(String(64), default="")
    goods_id: Mapped[str] = mapped_column(String(64), default="")
    goods_no: Mapped[str] = mapped_column(String(128), default="")
    goods_name: Mapped[str] = mapped_column(Text, default="")
    spec_name: Mapped[str] = mapped_column(String(256), default="")
    barcode: Mapped[str] = mapped_column(String(128), default="", index=True)
    brand_name: Mapped[str] = mapped_column(String(256), default="")
    cate_name: Mapped[str] = mapped_column(String(256), default="")
    current_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    can_use_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    locking_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    cost_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 当前成本价
    cost_value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 库存金额
    in_quantity_sum: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 今日入库
    out_quantity_sum: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 今日出库
    yesterday_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    week_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    threeday_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    purchasing_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 采购在途
    allocate_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 调拨在途
    sales_return_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 退货在途
    last_stock_in_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JkyWebStockinOrder(Base, PkMixin, TimestampMixin):
    """采购入库主单（inouttypes=101）。UNIQUE(doc_id) 幂等 upsert。"""

    __tablename__ = "jky_web_stockin_orders"

    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    goodsdoc_no: Mapped[str] = mapped_column(String(128), default="", index=True)  # 入库单号
    out_bill_no: Mapped[str] = mapped_column(String(128), default="")  # 外部单号
    in_out_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    inout_type: Mapped[str] = mapped_column(String(16), default="")  # 入库类型编码（101 采购入库）
    inout_type_name: Mapped[str] = mapped_column(String(64), default="")
    warehouse_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    warehouse_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    bill_no: Mapped[str] = mapped_column(String(128), default="", index=True)  # 关联单号
    source_bill_no: Mapped[str] = mapped_column(String(128), default="", index=True)  # 来源单号
    supplier_name: Mapped[str] = mapped_column(String(256), default="", index=True)  # 供应商
    supplier_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    logistic_name: Mapped[str] = mapped_column(String(128), default="")
    logistic_no: Mapped[str] = mapped_column(String(128), default="")
    company_name: Mapped[str] = mapped_column(String(256), default="")
    total_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)  # 数量合计
    cost_total_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 入库成本金额
    has_tax_total_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 含税金额
    tax_total_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 税额
    red_status: Mapped[str] = mapped_column(String(16), default="")  # 红冲状态
    remark: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JkyWebStockinItem(Base, PkMixin, TimestampMixin):
    """采购入库明细。UNIQUE(rec_id) 幂等 upsert；后续与 1688 订单关联。"""

    __tablename__ = "jky_web_stockin_items"
    __table_args__ = (
        Index("ix_jky_web_stockin_items_doc_id", "doc_id"),
        Index("ix_jky_web_stockin_items_goods_no", "goods_no"),
        Index("ix_jky_web_stockin_items_order_no", "order_no"),
    )

    rec_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    goodsdoc_no: Mapped[str] = mapped_column(String(128), default="")
    order_no: Mapped[str] = mapped_column(String(128), default="")  # 采购单号
    goods_id: Mapped[str] = mapped_column(String(64), default="")
    goods_no: Mapped[str] = mapped_column(String(128), default="")
    goods_name: Mapped[str] = mapped_column(Text, default="")
    sku_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    spec_name: Mapped[str] = mapped_column(String(256), default="")  # 规格
    barcode: Mapped[str] = mapped_column(String(128), default="")
    brand_name: Mapped[str] = mapped_column(String(256), default="")
    cate_name: Mapped[str] = mapped_column(String(256), default="")
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    unit_name: Mapped[str] = mapped_column(String(64), default="")
    cost_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 入库成本单价
    cost_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 入库成本金额
    with_tax_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 含税单价
    with_tax_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 含税金额
    tax_rate: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 税率
    batch_no: Mapped[str] = mapped_column(String(128), default="")  # 批次
    production_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiration_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shelf_life: Mapped[str] = mapped_column(String(64), default="")  # 保质期
    warehouse_id: Mapped[str] = mapped_column(String(64), default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
