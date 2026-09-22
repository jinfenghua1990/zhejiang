"""1688 订单文件解析器"""
import pandas as pd
import io
from typing import List, Dict, Any


class Alibaba1688Parser:
    """解析 1688 导出的 Excel 文件"""
    
    # 1688 订单字段映射
    FIELD_MAPPING = {
        "订单编号": "order_id",
        "买家公司名": "buyer_company_name",
        "买家会员名": "buyer_member_name",
        "卖家公司名": "seller_company_name",
        "卖家会员名": "seller_member_name",
        "货品总价(元)": "goods_total",
        "运费(元)": "freight",
        "涨价或折扣(元)": "discount",
        "实付款(元)": "actual_payment",
        "订单状态": "order_status",
        "订单创建时间": "order_time",
        "订单付款时间": "pay_time",
    }
    
    @staticmethod
    def parse_excel(file_content: bytes) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        解析 Excel 文件
        
        Returns:
            (orders, headers): 订单列表和表头列表
        """
        try:
            # 读取 Excel 文件
            df = pd.read_excel(io.BytesIO(file_content))
            
            # 获取表头
            headers = df.columns.tolist()
            
            # 检查是否包含必要的字段
            required_fields = ["订单编号", "实付款(元)", "订单状态"]
            missing_fields = [f for f in required_fields if f not in headers]
            
            if missing_fields:
                raise ValueError(f"缺少必要字段: {', '.join(missing_fields)}")
            
            # 解析每一行
            orders = []
            for _, row in df.iterrows():
                order = {}
                for chinese_col, english_col in Alibaba1688Parser.FIELD_MAPPING.items():
                    if chinese_col in headers:
                        value = row[chinese_col]
                        # 处理 NaN 值
                        if pd.isna(value):
                            order[english_col] = None
                        else:
                            order[english_col] = str(value)
                
                # 必须包含订单编号
                if order.get("order_id"):
                    orders.append(order)
            
            return orders, headers
            
        except Exception as e:
            raise ValueError(f"解析 Excel 文件失败: {str(e)}")
