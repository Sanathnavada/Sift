import os
import shutil
import re

# --- 1. Master Configuration ---

# SET YOUR MODE: "CAMERA" or "PHONE"
MODE = "CAMERA"

# SET TO True TO SIMULATE. SET TO False TO COPY FILES.
DRY_RUN = False 

# --- 2. Folder Paths ---

# SET YOUR SOURCE FOLDER FOR CAMERA (e.g., "SAN0XXXX.JPG" files)
SOURCE_DIR_CAMERA = r"E:\Sanath's Camera\2026.1.1 Jan skandagiri"

# SET YOUR SOURCE FOLDER FOR PHONE (e.g., "IMG_XXXX.jpg" files)
SOURCE_DIR_PHONE = r"F:\Sanath's Camera\2025.12.2 Dec Waynad, Kannur\Kerala trip"

# SET THE NAME OF THE NEW FOLDER TO BE CREATED
DEST_DIR_NAME = "Selected_Photos"

# --- 3. Number Lists ---

# PASTE YOUR CAMERA NUMBER LIST HERE
# RAW_CAMERA_STRING = """
# 3655,74
# 3707,24,51,57,74,79,83,88
# 3803,15,33,45,49,57,63,94,97
# 3906,11,18,22,28,30,33,35,41,46,62,63,68,81
# 4001,02,06,11,18,36,40,45,46,55,66,68
# 4116,20,25,27,32,34,35,42,43,50,52,53,58,60,75,87,93,95,97,98
# 4218,24,26,35,36,37,42,51,57,73,91
# 4303,04,22,35,39,51,62,65,75,92,99
# 4418,21,22,33,61,62,70,73,87,93
# 4505,10,13,16,22,24,25,32,39,49,51,63,73,75,80,87,92,92,95,98
# 4617,24,36,39,42,44,49,51,58,62,63,65,66,69,72,75,78,90,91,99
# 4700,01,04,06,14,15,25,39,66,71,75,77,85,98
# 4806,10,17,26,29,31,45,47,51,54,59,64,66,70,72,80,85,92
# 4900,01,04,25,28,33,34,36,41,48,58,61,66,69,83,84,87,90
# 5010,,19,47,50,54,58,62,70,90,94,97
# 5102,05,15,16,,48,49,58,62,66,76,84,91,93,99
# 5206,10,13,19,34,54,61,64,70,73,74,83,88,95,97
# 5309,19,22,23,24,27,28,32,37,,42,46,53,56,61,76,78,81,85,88,93,97
# 5401,11,15,20,30,45,49,61,70,83,86
# 5504,06,14,34,36,39,53,57,63,75,80,84,87,89,90,91,95
# 5601,03,14,18,21,24,27,36,40,53,59,60,72,77,84,93
# 5712,14,18,21,28,38,45,60,70,71,84,92,96,99
# 5807,27,39,45,50,54,57,63,64,86,96
# 5905,11,23,25,30,33,40,44,47,49,51,59,64,67,71,87,92,94,98
# 6001,07,33,38,43,56,65,71,75,79,93
# 6103,11,12,14,22,24,31,33,36,42,51,54,66,67,78,83,91,93
# 6204,14,22,40,48,51,52,57,60,76,77,82,88,94,97
# 6302,04,08,12,20,24,30,37,43,50,54,57,65,68,77,94
# 6411,15,16,26,30,33,36,42,46,51,60,64,66,69,70,72,79,82,86,91,99
# 6500,07,09,10,15,20,24,29,34,41,46,56,58,63,67,87,96
# 6600,02,07,18,25,27,39,45,50,56,70,74,85,91,93,95,97
# 6701,09,10,24,34,41,47,55,65,68,78,86,89,92,94
# 6803,13,17,34,38,39,40,44,54,62,64,65,68,72,88,98,99
# 6904,17,22,25,32,49
# """

# # PASTE YOUR PHONE NUMBER LIST HERE
# RAW_PHONE_STRING = """
# 4255,4456,1641,3745,3933,4026,5636,5713,0344,0502,0506,0517,0559,0921,4557,0137,
# 0339,1705,1713,2630,3626,4344,4511,4601,4637,4740,4848,5236,5239,5256,5305,5319,5415
# 5516,5555,,2249,2325,2922,5556,5613,5631,3044,1438,1448,1505,1529,2228,1525
# """


# PASTE YOUR CAMERA NUMBER LIST HERE
RAW_CAMERA_STRING = """
8138,68,98
8201,04,08,16,25,32,35,38,52,73,77,80,284
8301,05,17,27,34,51,67,74,82,89,93,98
"""

# PASTE YOUR PHONE NUMBER LIST HERE
# RAW_PHONE_STRING = """
# 3524,2212,2139,4442,4713,4256,3833,3827,3701,3659,3532,2741,3537,3518,3140,3035,2346,2316,2054,2233,2058,2038,2030,1932,5647,5621,3220,5049,5307,3202,3924,4039,3921,3644,3621
# """

RAW_PHONE_STRING = """
5326,5656,5703,5744,0132,0413,0638,2356,2014,2829,2851,2901,2916,2919,3953,4112,4238,4422,4529,4609,5553,5643,4722,4827,5321,5627,5211,4501,0029,0128,0209,0412,0717,2522,2956,3038,3834,4420,4425,4510,4526,5047,5340,5550,5607,5634,5936,0146,0151,0234,1044,1616,4857,3708,3834,4205,2802,5606,0107,0818,2541,2600,2733,955,0232,1529,1631,1709,2115,2737,3418,4807
"""

# -------------------------- SCRIPT --------------------------
# -------------------------- SCRIPT --------------------------

def parse_numbers_phone(raw_string):
    """
    Parses numbers for PHONE mode.
    Finds all 4-digit numbers and returns a LIST, preserving duplicates.
    """
    print("--- Starting Number Parsing (PHONE Mode) ---")
    
    # Use regex to find all 4-digit number sequences.
    all_found_tokens = re.findall(r'\d{4}', raw_string)
    
    print(f"  [INFO] Ignored all non-numeric text and extra commas.")
    print(f"\n--- Parsing Complete ---")
    print(f"Found {len(all_found_tokens)} numbers to process (including duplicates from your list).")
    # Return a LIST, not a set, to process duplicates
    return all_found_tokens 

def parse_numbers_camera(raw_string):
    """
    Intelligently parses the raw string for CAMERA mode.
    Returns a SET of unique 4-digit numbers.
    """
    print("--- Starting Number Parsing (CAMERA Mode) ---")
    parsed_numbers = set()
    current_prefix = ""
    line_number = 0

    for line in raw_string.strip().splitlines():
        line_number += 1
        line_content = line.strip()
        
        tokens = line_content.split(',')
        
        if not line_content or all(not t.strip() for t in tokens):
            print(f"  [L{line_number:02d}] INFO: Skipping blank line.")
            continue
            
        first_token_processed = False
        
        for token in tokens:
            token = token.strip()
            
            if not token:
                if first_token_processed:
                    print(f"  [L{line_number:02d}] INFO: Ignoring empty value (extra comma).")
                continue
                
            if not token.isdigit():
                print(f"  [L{line_number:02d}] ERROR: Ignoring non-numeric value: '{token}'")
                continue
            
            if not first_token_processed:
                if len(token) == 4:
                    current_prefix = token[:2]
                    if token in parsed_numbers:
                        print(f"  [L{line_number:02d}] DUPLICATE: Ignoring duplicate 4-digit number: {token}")
                    else:
                        parsed_numbers.add(token)
                    first_token_processed = True
                else:
                    print(f"  [L{line_number:02d}] ERROR: Line does not start with a 4-digit number. Skipping line: '{line_content}'")
                    break 
            else:
                if len(token) == 2:
                    full_num = current_prefix + token
                    if full_num in parsed_numbers:
                        print(f"  [L{line_number:02d}] DUPLICATE: Ignoring duplicate number: {full_num}")
                    else:
                        parsed_numbers.add(full_num)
                        
                elif len(token) == 3:
                    num1_suffix = token[:2]
                    num2_suffix = token[1:]
                    print(f"  [L{line_number:02d}] WARNING: Interpreting 3-digit typo '{token}' as '{num1_suffix}' and '{num2_suffix}'.")
                    
                    full_num1 = current_prefix + num1_suffix
                    if full_num1 in parsed_numbers:
                        print(f"    [L{line_number:02d}] DUPLICATE: Ignoring duplicate (from typo): {full_num1}")
                    else:
                        parsed_numbers.add(full_num1)
                        
                    full_num2 = current_prefix + num2_suffix
                    if full_num2 in parsed_numbers:
                        print(f"    [L{line_number:02d}] DUPLICATE: Ignoring duplicate (from typo): {full_num2}")
                    else:
                        parsed_numbers.add(full_num2)

                elif len(token) == 4:
                    print(f"  [L{line_number:02d}] INFO: New 4-digit prefix found mid-line: {token}")
                    current_prefix = token[:2]
                    if token in parsed_numbers:
                        print(f"    [L{line_number:02d}] DUPLICATE: Ignoring duplicate 4-digit number: {token}")
                    else:
                        parsed_numbers.add(token)
                
                else:
                    print(f"  [L{line_number:02d}] WARNING: Ignoring strangely formatted number (not 2, 3, or 4 digits): '{token}'")

    print(f"\n--- Parsing Complete ---")
    print(f"Found {len(parsed_numbers)} unique photo numbers to copy.")
    return sorted(list(parsed_numbers)) # Return a SET

def main():
    """
    Main function to run the copy process.
    """
    # 1. Set up destination folder
    dest_dir_path = os.path.join(os.getcwd(), DEST_DIR_NAME)
    
    if DRY_RUN:
        print("="*40)
        print("--- RUNNING IN DRY RUN MODE ---")
        print("--- NO FOLDERS WILL BE CREATED ---")
        print("--- NO FILES WILL BE COPIED ---")
        print("="*40)
    
    if not DRY_RUN:
        os.makedirs(dest_dir_path, exist_ok=True)

    # 2. Select logic based on MODE
    source_dir = ""
    numbers_to_copy = []
    
    if MODE.upper() == "CAMERA":
        print(f"Selected Mode: CAMERA")
        source_dir = SOURCE_DIR_CAMERA
        numbers_to_copy = parse_numbers_camera(RAW_CAMERA_STRING)
        
    elif MODE.upper() == "PHONE":
        print(f"Selected Mode: PHONE")
        source_dir = SOURCE_DIR_PHONE
        numbers_to_copy = parse_numbers_phone(RAW_PHONE_STRING)
        
    else:
        print(f"--- ERROR ---")
        print(f"Invalid MODE: '{MODE}'. Please set MODE to 'CAMERA' or 'PHONE' at the top of the script.")
        return

    print(f"\nSource folder: {source_dir}")
    print(f"Destination folder: {dest_dir_path}\n")

    # 3. Check if source directory exists
    if not os.path.isdir(source_dir):
        print(f"--- ERROR ---")
        print(f"Source directory not found:")
        print(f"{source_dir}")
        print("Please check the 'SOURCE_DIR' variable for your selected mode.")
        return

    # 4. Get the list of file numbers to copy
    if not numbers_to_copy:
        print("No numbers were found in the list. Exiting.")
        return

    print(f"\n--- Starting File Processing ---")
    copied_count = 0
    
    # 5. Execute correct logic based on mode
    
    if MODE.upper() == "CAMERA":
        # --- CAMERA LOGIC: Build filename and copy ---
        missing_files = []
        file_builder = lambda num: f"SAN{num.zfill(5)}.JPG" # Filename builder
        
        for num_str in numbers_to_copy: # numbers_to_copy is a SET of unique numbers
            file_name = file_builder(num_str)
            source_path = os.path.join(source_dir, file_name)
            dest_path = os.path.join(dest_dir_path, file_name)

            print(f"-> Processing: {file_name}...")

            if os.path.exists(source_path):
                if DRY_RUN:
                    print(f"    [FOUND] Would copy to {dest_path}")
                    copied_count += 1
                else:
                    try:
                        shutil.copy2(source_path, dest_path)
                        print(f"    [COPIED] {file_name}")
                        copied_count += 1
                    except Exception as e:
                        print(f"    [ERROR] Failed to copy {file_name}: {e}")
            else:
                print(f"    [NOT FOUND]")
                missing_files.append(file_name)
        
        # --- Final CAMERA Report ---
        print(f"\n--- Process Complete ---")
        if DRY_RUN:
            print(f"Report for [DRY RUN]:")
            print(f"Would have copied: {copied_count} files.")
        else:
            print(f"Successfully copied: {copied_count} files.")
        
        if missing_files:
            print(f"Could not find:    {len(missing_files)} files.")
            print("--- Missing Files ---")
            for f in missing_files:
                print(f"  {f}")

    elif MODE.upper() == "PHONE":
        # --- PHONE LOGIC: Search for files ending with the number ---
        print("Scanning source directory for all files...")
        all_files = [f for f in os.listdir(source_dir) if os.path.isfile(os.path.join(source_dir, f))]
        print(f"Found {len(all_files)} files in {source_dir}.")
        
        missing_numbers = []
        
        for num_str in numbers_to_copy: # numbers_to_copy is a LIST, including duplicates
            print(f"-> Processing number: {num_str}...")
            
            # We need to find files that END WITH ...XXXX.jpg
            suffix_lower = f"{num_str}.jpg"
            suffix_upper = f"{num_str}.JPG"
            
            found_matches = []
            for filename in all_files:
                if filename.endswith(suffix_lower) or filename.endswith(suffix_upper):
                    found_matches.append(filename)
                    
            if not found_matches:
                print(f"    [NOT FOUND] No file ending in '...{num_str}.jpg' or '...{num_str}.JPG'")
                missing_numbers.append(num_str)
            else:
                if len(found_matches) > 1:
                    print(f"    [INFO] Found {len(found_matches)} matching files for this number: {', '.join(found_matches)}")
                
                for file_to_copy in found_matches:
                    source_path = os.path.join(source_dir, file_to_copy)
                    dest_path = os.path.join(dest_dir_path, file_to_copy)
                    
                    if DRY_RUN:
                        print(f"    [FOUND] Would copy {file_to_copy}")
                        copied_count += 1
                    else:
                        try:
                            shutil.copy2(source_path, dest_path)
                            print(f"    [COPIED] {file_to_copy}")
                            copied_count += 1
                        except Exception as e:
                            print(f"    [ERROR] Failed to copy {file_to_copy}: {e}")

        # --- Final PHONE Report ---
        print(f"\n--- Process Complete ---")
        if DRY_RUN:
            print(f"Report for [DRY RUN]:")
            print(f"Would have copied: {copied_count} files.")
        else:
            print(f"Successfully copied: {copied_count} files.")
        
        # Report missing *numbers*
        unique_missing_numbers = sorted(list(set(missing_numbers)))
        if unique_missing_numbers:
            print(f"Could not find files for: {len(unique_missing_numbers)} unique numbers.")
            print("--- Missing Numbers ---")
            print(", ".join(unique_missing_numbers))

    # --- Final Message ---
    if not DRY_RUN:
        print(f"\nAll selected photos are now in: {dest_dir_path}")
    else:
        print(f"\nTo copy these files, change 'DRY_RUN' to 'False' at the top of the script and run again.")

# Run the main function when the script is executed
if __name__ == "__main__":
    main()