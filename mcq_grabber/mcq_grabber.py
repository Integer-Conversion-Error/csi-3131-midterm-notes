import json
import time
import os
import csv
import argparse
from datetime import datetime
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

def setup_driver():
    """Sets up the Selenium WebDriver."""
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    # options.add_argument('--headless')  # Run in headless mode for automation
    driver = webdriver.Chrome(service=service, options=options)
    return driver

def login(driver, config):
    """Logs into ZyBooks using credentials from the config file."""
    driver.get(config['link'])
    try:
        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="email"]'))
        )
        email_field.send_keys(config['username'])

        password_field = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
        password_field.send_keys(config['password'])

        signin_button = driver.find_element(By.CSS_SELECTOR, 'button.signin-button')
        signin_button.click()

        # Wait for the sign-in button to become stale, indicating a page change
        WebDriverWait(driver, 20).until(EC.staleness_of(signin_button))
        print("Login successful!")
        return True
    except TimeoutException:
        print("Login failed. Timed out waiting for page elements.")
        return False
    except Exception as e:
        print(f"An error occurred during login: {e}")
        return False

def scrape_mcq(question_element, writer, max_options=5):
    """Scrapes a multiple-choice question with options in separate columns."""
    try:
        question_text_div = question_element.find_element(By.CSS_SELECTOR, '.text div')
        question_text = question_text_div.text.strip()

        choices = question_element.find_elements(By.CSS_SELECTOR, '.question-choices .zb-radio-button')
        options = [choice.text.strip() for choice in choices]
        
        correct_answer = "Not found"
        explanation = "Not found"

        for i, choice in enumerate(choices):
            try:
                choice.click()
                time.sleep(0.5)  # Wait for feedback to appear

                # Check for correct feedback
                correct_feedback = question_element.find_element(By.CSS_SELECTOR, '.correct')
                if correct_feedback.is_displayed():
                    correct_answer = chr(ord('A') + i)
                    explanation_div = correct_feedback.find_element(By.CSS_SELECTOR, 'div')
                    explanation = explanation_div.text.strip()
                    break  # Found correct answer
            except NoSuchElementException:
                # This choice was incorrect, continue to the next one
                continue
            except Exception as e:
                print(f"Error clicking or checking choice: {e}")

        # Create the row with separated options
        row_data = [question_text]
        row_data.extend(options)
        # Pad with empty strings if there are fewer options than max_options
        row_data.extend([''] * (max_options - len(options)))
        row_data.extend([correct_answer, explanation])
        
        writer.writerow(row_data)
        print(f"Scraped MCQ: {question_text[:30]}...")

    except Exception as e:
        print(f"Error scraping MCQ: {e}")

def scrape_fitb(question_element, writer):
    """Scrapes a fill-in-the-blanks question."""
    try:
        question_text_div = question_element.find_element(By.CSS_SELECTOR, '.text div')
        question_text = question_text_div.text.strip().replace("__________", "[BLANK]")

        show_answer_button = question_element.find_element(By.CSS_SELECTOR, '.show-answer-button')
        # Click twice to reveal the answer
        show_answer_button.click()
        time.sleep(0.2)
        show_answer_button.click()

        # Wait for the answer to be visible
        forfeit_div = WebDriverWait(question_element, 5).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, '.forfeit'))
        )
        
        answer = forfeit_div.find_element(By.CSS_SELECTOR, '.forfeit-answer').text.strip()
        explanation = forfeit_div.find_element(By.CSS_SELECTOR, 'div[id]').text.strip()

        writer.writerow([question_text, answer, explanation])
        print(f"Scraped FITB: {question_text[:30]}...")

    except Exception as e:
        print(f"Error scraping FITB: {e}")

def save_unknown_question(question_element, chapter, section, unknown_dir):
    """Saves the HTML of an unknown question type."""
    try:
        html_content = question_element.get_attribute('outerHTML')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(unknown_dir, f"ch{chapter}_sec{section}_{timestamp}.html")
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"Saved unknown question to {filename}")
    except Exception as e:
        print(f"Error saving unknown question: {e}")

def scrape_practice_exercises(driver, writer):
    """Scrapes practice exercises from the page."""
    try:
        exercises = driver.find_elements(By.CSS_SELECTOR, '.exercise-content-resource .exercise-question')
        print(f"Found {len(exercises)} practice exercises.")
        for exercise in exercises:
            try:
                question_text = exercise.find_element(By.CSS_SELECTOR, '.question .text').text.strip()
                answer_text = ""
                
                try:
                    solution_button = exercise.find_element(By.CSS_SELECTOR, '.solution-button button')
                    solution_button.click()
                    time.sleep(0.5) # Wait for answer to reveal

                    answer_div = exercise.find_element(By.CSS_SELECTOR, '.answer div')
                    answer_text = answer_div.text.strip()
                except NoSuchElementException:
                    print(f"No solution button found for: {question_text[:30]}...")
                
                writer.writerow([question_text, answer_text])
                print(f"Scraped Practice Exercise: {question_text[:30]}...")
            except Exception as e:
                print(f"Error scraping a practice exercise: {e}")
    except Exception as e:
        print(f"Error finding practice exercises: {e}")


def main():
    parser = argparse.ArgumentParser(description="Scrape questions from ZyBooks.")
    parser.add_argument('--start-chapter', type=int, default=8, help="The chapter to start scraping from.")
    parser.add_argument('--end-chapter', type=int, default=14, help="The chapter to end scraping at (non-inclusive).")
    args = parser.parse_args()

    # Create output directories
    base_dir = 'mcq_grabber'
    questions_dir = os.path.join(base_dir, 'zybook_questions')
    unknown_dir = os.path.join(base_dir, 'unknown_question')
    os.makedirs(questions_dir, exist_ok=True)
    os.makedirs(unknown_dir, exist_ok=True)

    # Load config
    with open(os.path.join(base_dir, 'zybook_config.json')) as f:
        config = json.load(f)

    driver = setup_driver()
    
    if not login(driver, config):
        driver.quit()
        return

    base_url = config['link']
    
    mcq_csv_path = os.path.join(questions_dir, 'mcq.csv')
    fitb_csv_path = os.path.join(questions_dir, 'fitb.csv')
    practice_csv_path = os.path.join(questions_dir, 'practice_exercises.csv')

    with open(mcq_csv_path, 'w', newline='', encoding='utf-8') as mcq_file, \
         open(fitb_csv_path, 'w', newline='', encoding='utf-8') as fitb_file, \
         open(practice_csv_path, 'w', newline='', encoding='utf-8') as practice_file:
        
        mcq_writer = csv.writer(mcq_file)
        fitb_writer = csv.writer(fitb_file)
        practice_writer = csv.writer(practice_file)
        
        # Write headers
        mcq_header = ['Question'] + [f'Option {chr(ord("A") + i)}' for i in range(5)] + ['Correct Answer', 'Explanation']
        mcq_writer.writerow(mcq_header)
        fitb_writer.writerow(['Question', 'Answer', 'Explanation'])
        practice_writer.writerow(['Question', 'Answer'])

        current_chapter = args.start_chapter
        while current_chapter < args.end_chapter:
            current_section = 1
            last_section_found = 0
            while True:
                section_url = f"{base_url}/chapter/{current_chapter}/section/{current_section}"
                print(f"Navigating to {section_url}")
                driver.get(section_url)
                
                try:
                    # Wait for content or 404 page
                    WebDriverWait(driver, 10).until(
                        EC.any_of(
                            EC.presence_of_element_located((By.CSS_SELECTOR, '.question-set-question')),
                            EC.presence_of_element_located((By.CSS_SELECTOR, '.not-found-section'))
                        )
                    )
                    
                    # Check for 404
                    if driver.find_elements(By.CSS_SELECTOR, '.not-found-section'):
                        print(f"Section {current_section} not found for chapter {current_chapter}.")
                        last_section_found = current_section -1
                        break
                    
                    last_section_found = current_section

                    questions = driver.find_elements(By.CSS_SELECTOR, '.question-set-question')
                    print(f"Found {len(questions)} questions in Chapter {current_chapter}, Section {current_section}.")

                    for question in questions:
                        # Determine question type
                        if question.find_elements(By.CSS_SELECTOR, '.question-choices'):
                            scrape_mcq(question, mcq_writer)
                        elif question.find_elements(By.CSS_SELECTOR, '.show-answer-button'):
                            scrape_fitb(question, fitb_writer)
                        else:
                            save_unknown_question(question, current_chapter, current_section, unknown_dir)
                        time.sleep(1) # Small delay between questions

                except TimeoutException:
                    print(f"Timed out waiting for content on {section_url}. Assuming end of sections for this chapter.")
                    last_section_found = current_section -1
                    break
                except Exception as e:
                    print(f"An error occurred on {section_url}: {e}")

                current_section += 1
            
            # Try to scrape practice exercises
            if last_section_found > 0:
                practice_section_url = f"{base_url}/chapter/{current_chapter}/section/{last_section_found + 2}"
                print(f"Attempting to find practice exercises at: {practice_section_url}")
                try:
                    driver.get(practice_section_url)
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, '.section-content-resources-container'))
                    )
                    scrape_practice_exercises(driver, practice_writer)
                except TimeoutException:
                    print("No practice exercises found for this chapter.")
                except Exception as e:
                    print(f"An error occurred while scraping practice exercises: {e}")
                    # If the session is invalid, we need to stop.
                    if "invalid session id" in str(e):
                        print("Browser session lost. Exiting.")
                        driver.quit()
                        return

            current_chapter += 1

    print("Scraping complete.")
    driver.quit()

if __name__ == '__main__':
    main()
