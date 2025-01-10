from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import argparse
import random
import time
import re
import os

def setup_chrome(download_dir):
    """Configure Chrome to use existing profile"""
    chrome_options = Options()
    chrome_options.add_argument('--user-data-dir=C:\\Users\\joseph\\AppData\\Local\\Google\\Chrome\\User Data')
    chrome_options.add_argument('--profile-directory=Default')
    # Set download directory
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    return chrome_options

def wait_for_login(driver, success_element):
    """Wait for user to complete login manually"""
    try:
        WebDriverWait(driver, timeout=300).until(  # 5 minute timeout
            EC.presence_of_element_located((By.CSS_SELECTOR, success_element))
        )
        return True
    except Exception as e:
        print("Login timeout or error:", e)
        return False

def get_collection_links(driver):
    driver.execute_script("""
    const el = document.querySelector("#collections");
    el.focus();
    el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    el.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
""")
    time.sleep(5)
    # The specific pattern we're looking for
    # Find all collection links
    collection_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='collection']")

    links = [link.get_attribute('href') for link in collection_links]
    for x in links:
        print(x)

    return links

def scroll_to_bottom(driver):
    # Initial height
    last_height = driver.execute_script("return document.body.scrollHeight")
    
    while True:
        # Check if loading animation exists
        loading_elements = driver.find_elements(By.CSS_SELECTOR, ".tiktok-qmnyxf-SvgContainer")
        if len(loading_elements) == 0:
            # Scroll to bottom
            driver.execute_script("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});")
            
            # Random delay between 1.3 and 2.1 seconds (like in the JS)
            time.sleep(random.uniform(1.3, 2.1))
            
            # Calculate new scroll height
            new_height = driver.execute_script("return document.body.scrollHeight")
            
            # If heights are the same, we've reached the bottom
            if new_height == last_height:
                # Wait one final time to see if more content loads
                time.sleep(3.5)
                
                loading_elements = driver.find_elements(By.CSS_SELECTOR, ".tiktok-qmnyxf-SvgContainer")
                if len(loading_elements) == 0 and new_height == last_height:
                    break
            
            last_height = new_height
        else:
            # Wait for content to load
            time.sleep(1)

def wait_for_download(collection_url, download_dir, timeout=60):
    """Wait for the collection's txt file to appear in downloads"""
    # Extract collection name from URL
    collection_name = collection_url.split('/')[-1].split('-')[0]
    collection_name = collection_name.replace('%20', ' ')  # Replace URL encoding for spaces
    
    seconds = 0
    while seconds < timeout:
        # Check for txt files that start with the collection name
        for filename in os.listdir(download_dir):
            if filename.startswith(collection_name) and filename.endswith('.txt'):
                return True
        time.sleep(1)
        seconds += 1
    
    return False

def download_collection(driver, collection_url):
    print(f"downloading {collection_url}...")
    driver.get(collection_url)
    time.sleep(5)
    
    # Read and execute the script
    with open("video_download.js", "r") as f:
        dl_script = f.read()
    
    driver.execute_script(dl_script)
    
    # Wait for specific txt file
    downloads_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
    downloads_dir = "C:\\Users\\joseph\\Desktop\\code\\tt\\collection_files"
    if wait_for_download(collection_url, downloads_dir):
        print("Download completed!")
    else:
        print("Download timed out!")

def main():
    parser = argparse.ArgumentParser(description='Open TikTok profile page')
    parser.add_argument('username', help='TikTok username (without @)')
    args = parser.parse_args()

    # os.system("taskkill /f /im chrome.exe")  # Close any existing Chrome windows
    
    driver = webdriver.Chrome(options=setup_chrome("C:\\Users\\joseph\\Desktop\\code\\tt\\collection_files"))
    driver.get("https://tiktok.com")
    
    if wait_for_login(driver, "#app-header > div > div.css-q8q040-DivHeaderRightContainer.e8m7uf60 > div.css-1deszxq-DivHeaderInboxContainer.e1xroc440 > sup"):
        print("Login successful!")
        print("Navigating to collections page...")

        driver.get(f"https://tiktok.com/@{args.username}")
        time.sleep(5)
        driver.find_element(By.CSS_SELECTOR, "#main-content-others_homepage > div > div.css-833rgq-DivShareLayoutMain.ee7zj8d4 > div.css-1pbyc88-FeedTabWrapper.e1jjp0pq7 > div.css-1dw5iuh-DivVideoFeedTab.e1jjp0pq0 > p.css-1wncxfu-PFavorite.e1jjp0pq3").click()
        time.sleep(3)
        driver.execute_script("""
    const el = document.querySelector("#collections");
    el.focus();
    el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    el.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
""")
        time.sleep(5)
        scroll_to_bottom(driver)
        time.sleep(10)
        collections = get_collection_links(driver)
        for collection in collections:
            download_collection(driver, collection)
        print("Downloads complete!!")
        time.sleep(6)
    else:
        print("Login failed or timed out")
        
    driver.quit()

if __name__ == "__main__":
    main()

# document.querySelector('#collections').click()
# https://www.tiktok.com/@_jdeck_/collection/byu-7418411649947781931