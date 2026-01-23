from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import time

# Запуск браузера
driver = webdriver.Chrome()
driver.get("https://my.rieltor.ua/login")

# Авторизація
driver.find_element(By.NAME, "email").send_keys("your_email")
driver.find_element(By.NAME, "password").send_keys("your_password" + Keys.RETURN)

time.sleep(3)  # зачекати на завантаження

# Перехід до створення оголошення
driver.get("https://my.rieltor.ua/offers/create")

# Заповнення полів
driver.find_element(By.NAME, "price").send_keys("100000")
driver.find_element(By.NAME, "rooms").send_keys("3")
driver.find_element(By.NAME, "area").send_keys("75")
driver.find_element(By.NAME, "description").send_keys("Світла квартира у центрі Харкова")

# Завантаження фото
driver.find_element(By.NAME, "photos").send_keys("C:/path/to/photo.jpg")

# Збереження/публікація
driver.find_element(By.XPATH, "//button[contains(text(),'Зберегти')]").click()

time.sleep(5)
driver.quit()