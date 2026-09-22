import { redirect } from "next/navigation";

/** 旧地址兼容：银行汇总已并入统一「银行」页面的汇总视图，不再维护第二套页面。 */
export default function LegacyBankSummaryPage() {
  redirect("/finance/bank-transactions?view=summary");
}
