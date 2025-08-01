import os
import json
import csv
import re
import google.generativeai as genai
import time
import concurrent.futures
from datetime import timedelta
import threading

# Define the structure of a flashcard
class Flashcard:
    def __init__(self, question, option_a, option_b, option_c, option_d, option_e, answer, explanation):
        self.question = question
        self.option_a = option_a
        self.option_b = option_b
        self.option_c = option_c
        self.option_d = option_d
        self.option_e = option_e
        self.answer = answer
        self.explanation = explanation

    def to_csv_row(self):
        return [
            self.question,
            self.option_a,
            self.option_b,
            self.option_c,
            self.option_d,
            self.option_e,
            self.answer,
            self.explanation
        ]

# Define the structure of a front-back flashcard
class FB_Flashcard:
    def __init__(self, front, back):
        self.front = front
        self.back = back

    def to_csv_row(self):
        return [
            self.front,
            self.back
        ]

# Load API key from config.json
try:
    with open('config.json', 'r') as config_file:
        config = json.load(config_file)
        api_key = config.get('gemini_api_key')
        if not api_key or api_key == "YOUR_GEMINI_API_KEY":
            raise ValueError("Gemini API key not found or not set in config.json")
        genai.configure(api_key=api_key)
except FileNotFoundError:
    print("Error: config.json not found. Please create it and add your Gemini API key.")
    exit()
except ValueError as e:
    print(f"Error: {e}")
    exit()

# --- Configuration ---
# Specify the directories (chapters) to process
DIRECTORIES_TO_PROCESS = ['ch8', 'ch9', 'ch10', 'ch11', 'ch12', 'ch13'] # Add more chapters as needed, e.g., 'ch8', 'ch9'

# --- Gemini API Call ---
def generate_flashcard_from_text(text_content, max_retries=10, initial_backoff=2):
    """
    Uses the Gemini API to generate a flashcard from the given text,
    with retries and exponential backoff for rate limiting.
    """
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
    Your task is to act as a diligent student creating study flashcards. From the text provided below, generate a comprehensive set of multiple-choice questions that cover the material from various relevant angles.

    **Your goal is to be concise but exhaustive with regards to the important details.** Create questions for every important fact, all definitions, and all concepts. While you should avoid simple, repetitive rephrasing of the same question (e.g., "what is a deadlock?" and "define deadlock"), you are encouraged to create a few, distinct questions about a single topic if it helps test different aspects of that topic.

    Ignore any instructional or conversational text such as "Things to learn" or section titles. Focus only on the core information presented.
    The output must be a JSON array, where each object in the array is a flashcard. **It is critical that the output is a perfectly valid JSON array, with all special characters properly escaped.**
    Each JSON object must have the following exact keys: "question", "options", "answer", "explanation".
    - "question": The question text.
    - "options": A JSON object with five keys: "A", "B", "C", "D", "E".
    - "answer": The capital letter of the correct option (e.g., "C").
    - "explanation": A brief explanation of why the answer is correct.

    Text to analyze:
    ---
    {text_content}
    ---

    JSON Output:
    """
    retries = 0
    backoff_time = initial_backoff
    
    while retries < max_retries:
        try:
            response = model.generate_content(prompt)
            # The response might be in a markdown code block, so we clean it up
            cleaned_response = response.text.strip().replace('```json', '').replace('```', '').strip()
            
            # Attempt to fix common JSON errors before parsing
            # 1. Escape unescaped backslashes (common with LaTeX)
            # This regex finds backslashes that are not part of a valid JSON escape sequence and escapes them.
            cleaned_response = re.sub(r'\\([^"\\/bfnrtu])', r'\\\\\1', cleaned_response)
            # 2. Remove trailing commas from objects and arrays, which are invalid in strict JSON.
            cleaned_response = re.sub(r',\s*([\}\]])', r'\1', cleaned_response)

            # Validate JSON and its structure
            flashcards_data = json.loads(cleaned_response)

            if not isinstance(flashcards_data, list):
                raise ValueError("JSON response from API is not a list.")

            generated_flashcards = []
            required_keys = ["question", "options", "answer", "explanation"]
            option_keys = ["A", "B", "C", "D", "E"]

            for card_data in flashcards_data:
                if not all(key in card_data for key in required_keys):
                    raise ValueError(f"A flashcard object in the JSON response is missing required keys. Object: {card_data}")
                if not all(key in card_data["options"] for key in option_keys):
                    raise ValueError(f"A flashcard object in the JSON response is missing required option keys. Object: {card_data}")
                
                generated_flashcards.append(Flashcard(
                    question=card_data.get('question', ''),
                    option_a=card_data.get('options', {}).get('A', ''),
                    option_b=card_data.get('options', {}).get('B', ''),
                    option_c=card_data.get('options', {}).get('C', ''),
                    option_d=card_data.get('options', {}).get('D', ''),
                    option_e=card_data.get('options', {}).get('E', ''),
                    answer=card_data.get('answer', ''),
                    explanation=card_data.get('explanation', '')
                ))
            
            return generated_flashcards
        except genai.types.generation_types.StopCandidateException as e:
            # This can happen with safety settings, etc. Unlikely to be fixed by a retry.
            print(f"Content generation stopped, likely due to safety filters or other non-retriable issue: {e}")
            return None
        except json.JSONDecodeError as e:
            retries += 1
            print(f"    - WARNING: Failed to parse JSON response. Error: {e}. Retrying... (Attempt {retries}/{max_retries})")
            print(f"    - Problematic Text: {cleaned_response}")
            time.sleep(backoff_time)
            backoff_time *= 2
        except Exception as e:
            # Check if the error message indicates a rate limit issue (429)
            is_rate_limit_error = "429" in str(e)

            if is_rate_limit_error and retries < max_retries:
                retries += 1
                print(f"Rate limit hit. Retrying in {backoff_time} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(backoff_time)
                backoff_time *= 2  # Exponential backoff
            else:
                # For other errors, log and fail
                print(f"An error occurred while calling Gemini API or parsing its response: {e}")
                print(f"Problematic response text: {response.text if 'response' in locals() else 'N/A'}")
                return None # Do not retry for non-rate-limit errors

    print(f"Failed to generate flashcard after {max_retries} retries.")
    return None

# --- Main Processing Logic ---
def process_directories(directories):
    """
    Iterates through specified directories, reads each .tex file, generates flashcards,
    and writes them to a corresponding .csv file in a mirrored directory structure.
    """
    for dir_name in directories:
        if not os.path.isdir(dir_name):
            print(f"Warning: Directory '{dir_name}' not found. Skipping.")
            continue

        print(f"Processing directory: {dir_name}...")
        
        # Find all .tex files in the directory
        tex_files = [f for f in os.listdir(dir_name) if f.endswith('.tex')]

        for file_name in tex_files:
            file_path = os.path.join(dir_name, file_name)
            print(f"  - Reading file: {file_path}")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if not content.strip():
                        print(f"    - Skipping empty file.")
                        continue
                    
                    print(f"    - Generating flashcards for {file_name}...")
                    flashcards = generate_flashcard_from_text(content)
                    
                    if not flashcards:
                        print(f"    - No flashcards generated for {file_name}. Skipping CSV creation.")
                        continue

                    # Create the output directory structure
                    output_dir = os.path.join('csv', dir_name)
                    os.makedirs(output_dir, exist_ok=True)

                    # Determine output CSV file path
                    base_name = os.path.splitext(file_name)[0]
                    csv_file_path = os.path.join(output_dir, f"{base_name}.csv")
                    
                    print(f"    - Writing {len(flashcards)} flashcards to {csv_file_path}...")
                    with open(csv_file_path, 'w', newline='', encoding='utf-8') as csvfile:
                        writer = csv.writer(csvfile)
                        writer.writerow(['Question', 'Option A', 'Option B', 'Option C', 'Option D', 'Option E', 'Answer', 'Explanation'])
                        for card in flashcards:
                            writer.writerow(card.to_csv_row())
                    print(f"    - Successfully created {csv_file_path}")

                    # A delay to avoid overwhelming the API
                    time.sleep(2)
            
            except Exception as e:
                print(f"    - An error occurred while processing file {file_path}: {e}")

def combine_chapter_csvs(directories):
    """
    Combines all section CSVs in a chapter's directory into a single CSV file.
    """
    print("\n--- Combining CSV files for each chapter ---")
    for dir_name in directories:
        chapter_csv_dir = os.path.join('csv', dir_name)
        if not os.path.isdir(chapter_csv_dir):
            print(f"No CSV directory found for {dir_name}. Skipping combination.")
            continue

        # Find all individual section CSV files
        section_csv_files = [f for f in os.listdir(chapter_csv_dir) if f.endswith('.csv') and f.startswith('section')]
        
        if not section_csv_files:
            print(f"No section CSV files found in {chapter_csv_dir} to combine.")
            continue

        combined_csv_path = os.path.join(chapter_csv_dir, f"{dir_name}_all_sections.csv")
        print(f"Combining {len(section_csv_files)} files into {combined_csv_path}...")

        try:
            with open(combined_csv_path, 'w', newline='', encoding='utf-8') as outfile:
                writer = csv.writer(outfile)
                
                # Process each file and append its content (without the header)
                for file_name in sorted(section_csv_files):
                    file_path = os.path.join(chapter_csv_dir, file_name)
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        reader = csv.reader(infile)
                        next(reader)  # Skip header row
                        
                        # Write the rest of the rows
                        for row in reader:
                            writer.writerow(row)
            
            print(f"Successfully created {combined_csv_path}")
        except IOError as e:
            print(f"Error combining CSV files for {dir_name}: {e}")


def combine_all_chapters(directories):
    """
    Combines all chapter CSVs into a single master CSV file.
    """
    print("\n--- Combining all chapters into a master CSV file ---")
    master_csv_path = os.path.join('csv', 'all_chapters_combined.csv')
    
    # Collect all the per-chapter combined CSV files
    chapter_csv_files = []
    for dir_name in directories:
        chapter_csv_path = os.path.join('csv', dir_name, f"{dir_name}_all_sections.csv")
        if os.path.exists(chapter_csv_path):
            chapter_csv_files.append(chapter_csv_path)

    if not chapter_csv_files:
        print("No chapter CSVs found to combine into a master file.")
        return

    print(f"Combining {len(chapter_csv_files)} chapter files into {master_csv_path}...")
    try:
        with open(master_csv_path, 'w', newline='', encoding='utf-8') as outfile:
            writer = csv.writer(outfile)
            
            # Write the header only once from the first file
            first_file = True
            for file_path in sorted(chapter_csv_files):
                with open(file_path, 'r', encoding='utf-8') as infile:
                    reader = csv.reader(infile)
                    if first_file:
                        try:
                            header = next(reader)
                            writer.writerow(header)
                            first_file = False
                        except StopIteration:
                            # Handle empty file
                            continue
                    else:
                        try:
                            next(reader)  # Skip header for subsequent files
                        except StopIteration:
                            continue
                    
                    # Write the rest of the rows
                    for row in reader:
                        writer.writerow(row)
        
        print(f"Successfully created {master_csv_path}")
    except IOError as e:
        print(f"Error combining all chapters: {e}")


# --- Gemini API Call for FB ---
def generate_fb_flashcard_from_text(text_content, max_retries=10, initial_backoff=2):
    """
    Uses the Gemini API to generate front-back flashcards from the given text,
    with retries and exponential backoff for rate limiting.
    """
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
    Your task is to act as a diligent student creating study flashcards. From the text provided below, generate a comprehensive set of front-and-back flashcards that cover the material from various relevant angles.

    **Your goal is to be concise but exhaustive with regards to the important details.** Create flashcards for every important fact, all definitions, and all concepts. The "front" should be a question or a term, and the "back" should be the answer or definition.

    Ignore any instructional or conversational text such as "Things to learn" or section titles. Focus only on the core information presented.
    The output must be a JSON array, where each object in the array is a flashcard. **It is critical that the output is a perfectly valid JSON array, with all special characters properly escaped.**
    Each JSON object must have the following exact keys: "front", "back".
    - "front": The question or term for the front of the card.
    - "back": The answer or definition for the back of the card.

    Text to analyze:
    ---
    {text_content}
    ---

    JSON Output:
    """
    retries = 0
    backoff_time = initial_backoff
    
    while retries < max_retries:
        try:
            response = model.generate_content(prompt)
            # The response might be in a markdown code block, so we clean it up
            cleaned_response = response.text.strip().replace('```json', '').replace('```', '').strip()
            
            # Attempt to fix common JSON errors before parsing
            cleaned_response = re.sub(r'\\([^"\\/bfnrtu])', r'\\\\\1', cleaned_response)
            cleaned_response = re.sub(r',\s*([\}\]])', r'\1', cleaned_response)

            # Validate JSON and its structure
            flashcards_data = json.loads(cleaned_response)

            if not isinstance(flashcards_data, list):
                raise ValueError("JSON response from API is not a list.")

            generated_flashcards = []
            required_keys = ["front", "back"]

            for card_data in flashcards_data:
                if not all(key in card_data for key in required_keys):
                    raise ValueError(f"A flashcard object in the JSON response is missing required keys. Object: {card_data}")
                
                generated_flashcards.append(FB_Flashcard(
                    front=card_data.get('front', ''),
                    back=card_data.get('back', '')
                ))
            
            return generated_flashcards
        except genai.types.generation_types.StopCandidateException as e:
            print(f"Content generation stopped, likely due to safety filters or other non-retriable issue: {e}")
            return None
        except json.JSONDecodeError as e:
            retries += 1
            print(f"    - WARNING: Failed to parse JSON response. Error: {e}. Retrying... (Attempt {retries}/{max_retries})")
            print(f"    - Problematic Text: {cleaned_response}")
            time.sleep(backoff_time)
            backoff_time *= 2
        except Exception as e:
            is_rate_limit_error = "429" in str(e)

            if is_rate_limit_error and retries < max_retries:
                retries += 1
                print(f"Rate limit hit. Retrying in {backoff_time} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(backoff_time)
                backoff_time *= 2
            else:
                print(f"An error occurred while calling Gemini API or parsing its response: {e}")
                print(f"Problematic response text: {response.text if 'response' in locals() else 'N/A'}")
                return None

    print(f"Failed to generate flashcard after {max_retries} retries.")
    return None

# --- Main Processing Logic for FB ---
def process_single_file_fb(file_path):
    """
    Reads a single .tex file, generates FB flashcards, and writes them to a CSV.
    Returns the time taken and the number of flashcards generated.
    """
    start_time = time.time()
    print(f"  - Processing file: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                print(f"    - Skipping empty file: {file_path}")
                return 0, 0

        flashcards = generate_fb_flashcard_from_text(content)

        if not flashcards:
            print(f"    - No FB flashcards generated for {os.path.basename(file_path)}. Skipping CSV creation.")
            return time.time() - start_time, 0

        dir_name = os.path.basename(os.path.dirname(file_path))
        output_dir = os.path.join('csv', dir_name)
        os.makedirs(output_dir, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(file_path))[0]
        csv_file_path = os.path.join(output_dir, f"{base_name}.fb.csv")

        with open(csv_file_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Front', 'Back'])
            for card in flashcards:
                writer.writerow(card.to_csv_row())

        return time.time() - start_time, len(flashcards)

    except Exception as e:
        print(f"    - An error occurred while processing file {file_path}: {e}")
        return time.time() - start_time, 0

def process_directories_fb(directories, max_workers=25):
    """
    Uses a thread pool to process .tex files in parallel, with ETA estimation.
    """
    print("\n--- Processing directories for FB flashcards (in parallel) ---")
    
    all_files_to_process = []
    for dir_name in directories:
        if not os.path.isdir(dir_name):
            print(f"Warning: Directory '{dir_name}' not found. Skipping.")
            continue
        tex_files = [os.path.join(dir_name, f) for f in os.listdir(dir_name) if f.endswith('.tex')]
        all_files_to_process.extend(tex_files)

    if not all_files_to_process:
        print("No .tex files found to process.")
        return

    total_files = len(all_files_to_process)
    print(f"Found {total_files} files to process across {len(directories)} directories.")

    start_time = time.time()
    processed_files = 0
    # Use a lock for thread-safe printing
    print_lock = threading.Lock()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(process_single_file_fb, file_path): file_path for file_path in all_files_to_process}

        for future in concurrent.futures.as_completed(future_to_file):
            file_path = future_to_file[future]
            try:
                elapsed_time, card_count = future.result()
                
                with print_lock:
                    processed_files += 1
                    
                    # Calculate ETA based on wall-clock time
                    current_run_time = time.time() - start_time
                    avg_time_per_file = current_run_time / processed_files
                    remaining_files = total_files - processed_files
                    eta_seconds = avg_time_per_file * remaining_files
                    
                    print(f"\n--- Progress Update ---")
                    print(f"Finished: {os.path.basename(file_path)} in {elapsed_time:.2f}s ({card_count} cards created)")
                    print(f"Completed: {processed_files}/{total_files} files")
                    print(f"Total Elapsed Time: {timedelta(seconds=int(current_run_time))}")
                    print(f"Estimated Time Remaining (ETA): {timedelta(seconds=int(eta_seconds))}")
                    print(f"-----------------------\n")

            except Exception as exc:
                with print_lock:
                    print(f"'{file_path}' generated an exception: {exc}")

    total_run_time = time.time() - start_time
    print(f"\n--- Finished processing all files in {timedelta(seconds=int(total_run_time))} ---")

def combine_chapter_csvs_fb(directories):
    """
    Combines all section FB CSVs in a chapter's directory into a single CSV file.
    """
    print("\n--- Combining FB CSV files for each chapter ---")
    for dir_name in directories:
        chapter_csv_dir = os.path.join('csv', dir_name)
        if not os.path.isdir(chapter_csv_dir):
            print(f"No CSV directory found for {dir_name}. Skipping FB combination.")
            continue

        section_csv_files = [f for f in os.listdir(chapter_csv_dir) if f.endswith('.fb.csv') and f.startswith('section')]
        
        if not section_csv_files:
            print(f"No section FB CSV files found in {chapter_csv_dir} to combine.")
            continue

        combined_csv_path = os.path.join(chapter_csv_dir, f"{dir_name}_all_sections.fb.csv")
        print(f"Combining {len(section_csv_files)} FB files into {combined_csv_path}...")

        try:
            with open(combined_csv_path, 'w', newline='', encoding='utf-8') as outfile:
                writer = csv.writer(outfile)
                writer.writerow(['Front', 'Back'])
                
                for file_name in sorted(section_csv_files):
                    file_path = os.path.join(chapter_csv_dir, file_name)
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        reader = csv.reader(infile)
                        try:
                            next(reader)
                        except StopIteration:
                            continue
                        
                        for row in reader:
                            writer.writerow(row)
            
            print(f"Successfully created {combined_csv_path}")
        except IOError as e:
            print(f"Error combining FB CSV files for {dir_name}: {e}")

def combine_all_chapters_fb(directories):
    """
    Combines all chapter FB CSVs into a single master CSV file.
    """
    print("\n--- Combining all FB chapters into a master CSV file ---")
    master_csv_path = os.path.join('csv', 'all_chapters_combined.fb.csv')
    
    chapter_csv_files = []
    for dir_name in directories:
        chapter_csv_path = os.path.join('csv', dir_name, f"{dir_name}_all_sections.fb.csv")
        if os.path.exists(chapter_csv_path):
            chapter_csv_files.append(chapter_csv_path)

    if not chapter_csv_files:
        print("No chapter FB CSVs found to combine into a master file.")
        return

    print(f"Combining {len(chapter_csv_files)} FB chapter files into {master_csv_path}...")
    try:
        with open(master_csv_path, 'w', newline='', encoding='utf-8') as outfile:
            writer = csv.writer(outfile)
            writer.writerow(['Front', 'Back'])
            
            for file_path in sorted(chapter_csv_files):
                with open(file_path, 'r', encoding='utf-8') as infile:
                    reader = csv.reader(infile)
                    try:
                        next(reader)
                    except StopIteration:
                        continue
                    
                    for row in reader:
                        writer.writerow(row)
        
        print(f"Successfully created {master_csv_path}")
    except IOError as e:
        print(f"Error combining all FB chapters: {e}")


if __name__ == "__main__":
    # To run the original multiple-choice flashcard generator:
    # process_directories(DIRECTORIES_TO_PROCESS)
    # combine_chapter_csvs(DIRECTORIES_TO_PROCESS)
    # combine_all_chapters(DIRECTORIES_TO_PROCESS)

    # To run the new front-back flashcard generator:
    process_directories_fb(DIRECTORIES_TO_PROCESS)
    combine_chapter_csvs_fb(DIRECTORIES_TO_PROCESS)
    combine_all_chapters_fb(DIRECTORIES_TO_PROCESS)
    
    print("\nProcessing complete.")
