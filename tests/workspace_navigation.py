"""Open the same secondary navigation a dashboard user sees."""
def open_tool(page, label):
    menu = page.locator("#tool-menu")
    if not menu.evaluate("(el) => el.open"):
        menu.locator("summary").click()
    menu.get_by_role("button", name=label, exact=True).click()
