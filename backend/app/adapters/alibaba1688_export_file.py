"""1688 订单导出文件的解析器。

支持 1688 卖家中心导出的标准 Excel 格式，自动识别表头并提取订单数据。
"""
from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any


# 1688 订单导出的标准字段映射
FIELD_MAPPING = {
    "订单编号": "external_order_id",
    # 本系统「采购订单」导出模板的列名：导出→修改→重新导入可直接识别。
    "订单号": "external_order_id",
    "买家公司名": "buyer_company_name",
    "买家会员名": "buyer_member_name",
    "卖家公司名": "seller_company_name",
    "供应商/工厂": "seller_company_name",
    "卖家会员名": "seller_member_name",
    "货品总价(元)": "goods_total",
    "订单金额": "goods_total",
    "运费(元)": "freight",
    "涨价或折扣(元)": "discount",
    "实付款(元)": "actual_payment",
    "实付金额": "actual_payment",
    "订单状态": "order_status",
    "采购状态": "order_status",
    "订单备注": "order_remark",
    "买家留言": "order_remark",
    "买家备注": "order_remark",
    "备注": "order_remark",
    # 1688 官方买家订单导出使用“订单创建时间/订单付款时间”；保留短表头兼容其他导出版本。
    "下单时间": "order_time",
    "下单日期": "order_time",
    "订单创建时间": "order_time",
    "付款时间": "pay_time",
    "订单付款时间": "pay_time",
}


@dataclass
class ParsedAlibaba1688Export:
    status: str = "parsed"
    sheet_name: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    error_summary: str = ""


def _parse_xlsx(content: bytes) -> ParsedAlibaba1688Export:
    """解析 XLSX 格式的 1688 订单导出文件。"""
    result = ParsedAlibaba1688Export()
    
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as z:
            # 读取共享字符串
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in z.namelist():
                with z.open("xl/sharedStrings.xml") as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    ns = {"ss": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                    for si in root.findall(".//ss:si", ns):
                        texts = []
                        for t in si.findall(".//ss:t", ns):
                            if t.text:
                                texts.append(t.text)
                        shared_strings.append("".join(texts))
            
            # 读取工作表
            sheet_path = "xl/worksheets/sheet1.xml"
            if sheet_path not in z.namelist():
                result.status = "failed"
                result.error_summary = "找不到工作表 sheet1"
                return result
            
            with z.open(sheet_path) as f:
                tree = ET.parse(f)
                root = tree.getroot()
                ns = {"ss": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                
                rows = root.findall(".//ss:row", ns)
                if not rows:
                    result.status = "failed"
                    result.error_summary = "工作表为空"
                    return result
                
                # 解析第一行作为表头
                header_row = rows[0]
                header_cells = header_row.findall("ss:c", ns)
                headers = []
                for cell in header_cells:
                    inline_str = cell.find(".//ss:is/ss:t", ns)
                    if inline_str is not None and inline_str.text:
                        headers.append(inline_str.text.strip())
                
                if not headers:
                    result.status = "failed"
                    result.error_summary = "找不到表头"
                    return result
                
                result.headers = headers
                
                # 检查是否包含关键表头（支持 1688 官方导出与本系统导出模板两套列名）
                has_order_no = any(h in headers for h in ("订单编号", "订单号"))
                has_payment = any(h in headers for h in ("实付款(元)", "实付金额"))
                has_status = any(h in headers for h in ("订单状态", "采购状态"))
                missing = []
                if not has_order_no:
                    missing.append("订单编号/订单号")
                if not has_payment:
                    missing.append("实付款(元)/实付金额")
                if not has_status:
                    missing.append("订单状态/采购状态")
                if missing:
                    result.status = "failed"
                    result.error_summary = f"缺少必要表头: {', '.join(missing)}"
                    return result
                
                # 解析数据行
                for row_idx, row in enumerate(rows[1:], start=1):
                    cells = row.findall("ss:c", ns)
                    row_data: dict[str, Any] = {}
                    
                    for cell in cells:
                        cell_ref = cell.get("r", "")
                        # 提取列字母（如 A1 -> A）
                        col_letter = "".join(c for c in cell_ref if c.isalpha())
                        col_index = _col_letter_to_index(col_letter)

                        if col_index >= len(headers):
                            continue

                        header = headers[col_index]
                        field_name = FIELD_MAPPING.get(header)
                        if not field_name:
                            continue

                        # 提取值：inlineStr 直读；否则是共享字符串引用（Excel/openpyxl
                        # 重存的标准写法），按索引还原文本。
                        inline_str = cell.find(".//ss:is/ss:t", ns)
                        value_elem = cell.find("ss:v", ns)

                        if inline_str is not None and inline_str.text:
                            value = inline_str.text.strip()
                        elif value_elem is not None and value_elem.text:
                            value = value_elem.text.strip()
                            if cell.get("t") == "s":
                                try:
                                    value = shared_strings[int(value)].strip()
                                except (ValueError, IndexError):
                                    pass
                            elif cell.get("t") == "str" or cell.get("t") == "inlineStr":
                                pass
                        else:
                            value = ""
                        
                        row_data[field_name] = value
                    
                    # 必须有订单编号
                    if row_data.get("external_order_id"):
                        result.rows.append(row_data)
                
                if not result.rows:
                    result.status = "failed"
                    result.error_summary = "未找到有效订单数据"
                    return result
                
    except zipfile.BadZipFile:
        result.status = "failed"
        result.error_summary = "无效的 ZIP/XLSX 文件"
    except Exception as e:
        result.status = "failed"
        result.error_summary = f"解析失败: {str(e)[:200]}"
    
    return result


def _col_letter_to_index(col_letter: str) -> int:
    """将列字母（如 A, B, AA）转换为索引（0, 1, 26）。"""
    result = 0
    for char in col_letter.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def parse_alibaba1688_export(content: bytes, original_name: str, max_rows: int = 10000) -> ParsedAlibaba1688Export:
    """解析 1688 订单导出文件。"""
    lower_name = original_name.lower()
    
    if lower_name.endswith(".xlsx"):
        result = _parse_xlsx(content)
    else:
        result = ParsedAlibaba1688Export()
        result.status = "failed"
        result.error_summary = f"不支持的文件格式: {original_name}，仅支持 .xlsx"
        return result
    
    # 限制行数
    if len(result.rows) > max_rows:
        result.rows = result.rows[:max_rows]
        result.error_summary = f"文件过大，仅导入前 {max_rows} 行"
    
    return result
