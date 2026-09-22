from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_foreign_finance_has_one_query_scoped_workspace_contract():
    route_table = source("frontend/src/lib/workspace/route-table.tsx")
    tab_store = source("frontend/src/lib/workspace/tab-store.tsx")
    host = source("frontend/src/components/workspace/workspace-host.tsx")
    navigation = source("frontend/src/lib/navigation.ts")
    finance_page = source("frontend/src/app/finance/page.tsx")
    legacy_page = source("frontend/src/app/foreign-trade/finance/page.tsx")
    workbench = source("frontend/src/app/foreign-trade/workbench.tsx")

    assert 'workspaceFor: (search) => isForeignTradeFinanceRoute("/finance", search) ? "foreign" : "domestic"' in route_table
    assert "workspace: routeWorkspace(tab.pathname, tab.search)" in tab_store
    assert "const { pathname, search, entry, workspace } = resolved;" in tab_store
    assert "active.workspace === resolved.workspace" in host
    assert "isForeignTradeFinanceRoute(pathname, search)" in navigation

    assert "useSearchParams" in finance_page
    assert "window.location.search" not in finance_page
    assert 'syncWorkspaceUrl(query ? `${pathname}?${query}` : pathname, "replace")' in finance_page

    assert 'syncWorkspaceUrl("/finance?scope=foreign_trade", "replace")' in legacy_page
    assert 'mode="finance"' not in legacy_page
    assert '"finance" |' not in workbench
    assert 'finance: "收款与利润"' not in workbench


def test_jackyun_receiving_legacy_page_only_targets_canonical_panel():
    legacy = source("frontend/src/app/supply-chain/receiving/jackyun/page.tsx")
    receiving = source("frontend/src/app/supply-chain/receiving/page.tsx")
    route_table = source("frontend/src/lib/workspace/route-table.tsx")

    assert 'syncWorkspaceUrl("/supply-chain/receiving?panel=jackyun", "replace")' in legacy
    assert "JackyunPanel" not in legacy
    assert 'searchParams.get("panel") === "jackyun"' in receiving
    assert '"/supply-chain/receiving/jackyun": "/supply-chain/receiving?panel=jackyun"' in route_table
