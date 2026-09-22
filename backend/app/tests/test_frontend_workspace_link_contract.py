from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workspace_link_preserves_native_browser_escape_hatches():
    link = source("frontend/src/components/workspace/workspace-link.tsx")

    assert "syncWorkspaceUrl(href" in link
    assert "event.preventDefault()" in link
    assert "event.metaKey" in link
    assert "event.ctrlKey" in link
    assert "event.shiftKey" in link
    assert "event.altKey" in link
    assert 'target !== "_self"' in link
    assert "download !== undefined" in link
    assert 'href.startsWith("/") && !href.startsWith("//")' in link


def test_high_frequency_navigation_uses_workspace_link_not_next_link():
    for path in (
        "frontend/src/components/sidebar.tsx",
        "frontend/src/components/top-bar.tsx",
        "frontend/src/app/finance/page.tsx",
    ):
        content = source(path)
        assert 'from "@/components/workspace/workspace-link"' in content
        assert 'from "next/link"' not in content
