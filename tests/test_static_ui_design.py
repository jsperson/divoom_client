from pathlib import Path

STATIC_HTML = Path("src/divoom_client/web/static/index.html")


def test_studio_ui_declares_refined_design_system_tokens():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert "--surface-glass" in html
    assert "--accent-primary" in html
    assert "font-feature-settings" in html
    assert "Divoom Studio visual refresh" in html


def test_import_control_uses_styled_file_button_not_raw_browser_chrome():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert "class=\"file-input\"" in html
    assert "class=\"file-button" in html
    assert "Choose file" in html


def test_design_checks_are_summarized_not_repeated_warning_badges():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert "function summarizeWarnings" in html
    assert "issue-summary" in html
    assert "×" in html
