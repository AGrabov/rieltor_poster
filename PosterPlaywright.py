from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # Авторизація
        page.goto("https://my.rieltor.ua/login")
        page.fill("input[name='email']", "your_email")
        page.fill("input[name='password']", "your_password")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        # Перехід до створення оголошення
        page.goto("https://my.rieltor.ua/offers/create")

        # Знаходимо всі поля, де class містить "required"
        required_fields = page.query_selector_all(
            "input[class*='required'], textarea[class*='required'], select[class*='required']"
        )

        # Заповнюємо знайдені поля
        for idx, el in enumerate(required_fields):
            tag = el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "input":
                el.fill(f"Test value {idx}")
            elif tag == "textarea":
                el.fill("Test description")
            elif tag == "select":
                options = el.query_selector_all("option")
                if len(options) > 1:
                    value = options[1].get_attribute("value")
                    el.select_option(value)

        # Завантаження фото
        photo_input = page.query_selector("input[type='file']")
        if photo_input:
            photo_input.set_input_files("C:/path/to/photo.jpg")

        # Збереження/публікація
        save_btn = page.query_selector("button:has-text('Зберегти')")
        if save_btn:
            save_btn.click()

        page.wait_for_timeout(5000)
        browser.close()

if __name__ == "__main__":
    main()