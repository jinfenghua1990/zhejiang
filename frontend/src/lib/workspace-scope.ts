export type SearchParamReader = {
  get: (name: string) => string | null;
};

/** 统一判断“当前地址是否来自外贸工作台的共享财务入口”。 */
export function isForeignTradeFinanceRoute(
  pathname: string,
  search?: SearchParamReader | null,
): boolean {
  return pathname === "/finance" && search?.get("scope") === "foreign_trade";
}
