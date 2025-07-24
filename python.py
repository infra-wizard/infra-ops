#!/usr/bin/env python3

import argparse
import os
import sys
import logging
import re
import json
import subprocess
import tempfile
import shutil
from glob import glob

# Configure logging
logging.basicConfig(
    filename='buildlist_parser.log',
    filemode='w',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def validate_path(path, description):
    if not os.path.exists(path):
        logging.error(f"The {description} '{path}' does not exist.")
        sys.exit(1)
    if not os.path.isdir(path) and description == "Yocto source layers path":
        logging.error(f"The {description} '{path}' is not a directory.")
        sys.exit(1)
    if not os.path.isfile(path) and description == "pn-buildlist path":
        logging.error(f"The {description} '{path}' is not a file.")
        sys.exit(1)
    logging.info(f"Validated {description}: {path}")

def read_buildlist(buildlist_path):
    entries = []
    package_names = []
    with open(buildlist_path, 'r') as file:
        for line in file:
            line = line.strip()
            if line.startswith("mc:"):
                parts = line.split(":")
                if len(parts) == 3:
                    machine, package = parts[1], parts[2]
                    entries.append((machine, package))
                    package_names.append(package)
                    logging.info(f"Parsed entry - Machine: {machine}, Package: {package}")
            else:
                logging.warning(f"Malformed line skipped: {line}")
    
    return entries, package_names

def find_recipes(yocto_layers_path, package_names):
    recipes_found = []
    recipes_not_found = package_names.copy()
    recipe_paths = {}
    
    for root, dirs, files in os.walk(yocto_layers_path):
        for file in files:
            if file.endswith((".bb", ".inc")):
                recipe_name = os.path.splitext(file)[0]
                
                # Handle different recipe naming patterns
                original_recipe_name = recipe_name
                
                # Remove common suffixes that might be added to recipe files
                suffixes_to_remove = ['_git', '_svn', '_hg', '-git', '-svn', '-hg']
                for suffix in suffixes_to_remove:
                    if recipe_name.endswith(suffix):
                        recipe_name = recipe_name[:-len(suffix)]
                        break
                
                # If the recipe is named "common", try to match it to a package name in the path
                if recipe_name == "common":
                    for pkg in package_names:
                        if pkg in root:
                            recipe_name = pkg
                            break
                
                # Check if this recipe matches any package name
                if recipe_name in package_names:
                    recipes_found.append(recipe_name)
                    recipe_paths[recipe_name] = os.path.join(root, file)
                    if recipe_name in recipes_not_found:
                        recipes_not_found.remove(recipe_name)
                    logging.info(f"Recipe found: {recipe_name} -> {original_recipe_name} at {os.path.join(root, file)}")
                
                # Also check if the package name is a substring of the recipe name
                # This handles cases like polaris-vdm-client-test.bb matching polaris-vdm-client
                for pkg in package_names:
                    if pkg not in recipe_paths and (
                        recipe_name.startswith(pkg + '-') or 
                        recipe_name.startswith(pkg + '_') or
                        original_recipe_name.startswith(pkg + '-') or
                        original_recipe_name.startswith(pkg + '_')
                    ):
                        recipes_found.append(pkg)
                        recipe_paths[pkg] = os.path.join(root, file)
                        if pkg in recipes_not_found:
                            recipes_not_found.remove(pkg)
                        logging.info(f"Recipe found (pattern match): {pkg} -> {original_recipe_name} at {os.path.join(root, file)}")
                        break
                
                # GENERIC: Handle directory-based matching (like geodata case)  
                # Check if package name contains the directory name as a prefix
                directory_name = os.path.basename(root)
                for pkg in package_names:
                    if pkg not in recipe_paths and len(directory_name) > 3:
                        # Check if package starts with directory name (geodatatypes starts with geodata)
                        if pkg.startswith(directory_name) and (pkg in original_recipe_name or pkg in file):
                            recipes_found.append(pkg)
                            recipe_paths[pkg] = os.path.join(root, file)
                            if pkg in recipes_not_found:
                                recipes_not_found.remove(pkg)
                            logging.info(f"Recipe found (directory-based match): {pkg} -> {original_recipe_name} at {os.path.join(root, file)}")
                            break
    
    return recipes_found, recipes_not_found, recipe_paths

def extract_src_info(recipe_path, machine_type):
    src_uri = None
    src_rev = None
    src_branch = None
    
    recipe_name = os.path.basename(recipe_path)
    logging.info(f"=== EXTRACTING INFO FROM: {recipe_name} ===")
    
    # Read the main recipe file and handle multi-line entries
    with open(recipe_path, 'r') as file:
        content = file.read()
    
    logging.info(f"File content length: {len(content)} characters")
    
    # Handle multi-line entries with backslashes
    content = re.sub(r'\\\s*\n\s*', ' ', content)
    lines = content.split('\n')
    
    logging.info(f"Processing {len(lines)} lines in {recipe_name}")
    
    for i, line in enumerate(lines):
        line = line.strip()
        if "SRC_URI" in line:
            logging.info(f"Line {i+1}: Found SRC_URI line: {line}")
            
        if "SRC_URI" in line and ("git" in line or "github" in line):
            logging.info(f"Line {i+1}: Processing SRC_URI with git: {line}")
            
            # Try multiple git URL patterns - made more permissive
            patterns = [
                (r'gitsm://git@([^;]+)', 'gitsm://git@'),
                (r'gitsm://([^;]+)', 'gitsm://'),
                (r'https://git@([^;]+)', 'https://git@'),
                (r'https://([^;]+)\.git', 'https://*.git'),
                (r'git://([^;]+)', 'git://'),
                (r'https://([^;]+)', 'https://'),
                (r'git@([^;]+)', 'git@'),
                (r'"([^"]*github\.com[^"]*)"', 'quoted github'),
                # More permissive fallback patterns
                (r'([^"\s]+github\.com[^"\s;]+)', 'github flexible'),
                (r'([^"\s]+\.git)', 'any .git'),
            ]
            
            for pattern, pattern_name in patterns:
                logging.info(f"  Trying pattern: {pattern_name}")
                match = re.search(pattern, line)
                if match:
                    src_uri = match.group(1)
                    
                    # Clean up and standardize the URI format
                    if src_uri.endswith('.git'):
                        src_uri = src_uri[:-4]  # Remove .git suffix
                    
                    # Convert to git@ format for consistency
                    if 'github.com' in src_uri:
                        if src_uri.startswith('github.com/'):
                            src_uri = f"git@{src_uri.replace('/', ':', 1)}.git"
                        elif 'github.com/' in src_uri:
                            # Handle cases like "something/github.com/user/repo"
                            github_part = src_uri[src_uri.find('github.com/'):]
                            src_uri = f"git@{github_part.replace('/', ':', 1)}.git"
                        else:
                            src_uri = f"git@github.com:{src_uri}.git"
                    
                    logging.info(f"  ✓ MATCHED! SRC_URI: {src_uri} (from pattern: {pattern_name})")
                    break
                else:
                    logging.info(f"  ✗ No match for pattern: {pattern_name}")
            
            if not src_uri:
                logging.warning(f"  No SRC_URI pattern matched for line: {line}")
                
        elif "SRCREV" in line:
            logging.info(f"Line {i+1}: Found SRCREV line: {line}")
            patterns = [
                r'SRCREV\s*=\s*"([^"]+)"',
                r"SRCREV\s*=\s*'([^']+)'",
                r'SRCREV\s*=\s*([a-f0-9]{6,})',  # Made more flexible - 6+ hex chars
                r'SRCREV\s*=\s*([^\s;]+)',       # Any non-whitespace value
            ]
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    src_rev = match.group(1)
                    logging.info(f"  ✓ Found SRCREV: {src_rev}")
                    break
        elif "SRCBRANCH" in line:
            logging.info(f"Line {i+1}: Found SRCBRANCH line: {line}")
            patterns = [
                r'SRCBRANCH\s*=\s*"([^"]+)"',
                r"SRCBRANCH\s*=\s*'([^']+)'",
                r'SRCBRANCH\s*=\s*([^\s;]+)',    # Any non-whitespace value
            ]
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    src_branch = match.group(1)
                    logging.info(f"  ✓ Found SRCBRANCH: {src_branch}")
                    break
    
    logging.info(f"After parsing {recipe_name}: URI={src_uri}, REV={src_rev}, BRANCH={src_branch}")
    
    # If SRCREV or SRCBRANCH are missing, search for them in related files
    if machine_type and (src_rev is None or src_branch is None):
        logging.info(f"Missing info, searching related files with machine_type: {machine_type}")
        recipe_dir = os.path.dirname(recipe_path)
        recipe_filename = os.path.basename(recipe_path)
        
        # Extract base name (remove .inc or .bb extension)
        if recipe_filename.endswith('.inc'):
            base_name = recipe_filename[:-4]
        elif recipe_filename.endswith('.bb'):
            base_name = recipe_filename[:-3]
        else:
            base_name = recipe_filename
        
        logging.info(f"Searching for missing info - base_name: {base_name}, machine_type: {machine_type}")
        
        # Look for machine-specific files with enhanced patterns for geodata
        possible_patterns = [
            f"{base_name}_{machine_type}.bb",      # janus_2.0.bb
            f"{base_name}-{machine_type}.bb",      # janus-2.0.bb  
            f"{base_name}.{machine_type}.bb",      # janus.2.0.bb
            f"common_{machine_type}.inc",          # geodata: common_2.0.inc
            f"common_{machine_type}.bb",           # geodata: common_2.0.bb
            f"{base_name}_{machine_type}.inc",     # base_2.0.inc
        ]
        
        for pattern in possible_patterns:
            target_file = os.path.join(recipe_dir, pattern)
            logging.info(f"  Checking for file: {target_file}")
            if os.path.exists(target_file):
                logging.info(f"  ✓ Found machine-specific file: {target_file}")
                
                with open(target_file, 'r') as file:
                    content = file.read()
                    content = re.sub(r'\\\s*\n\s*', ' ', content)
                    file_lines = content.split('\n')
                    
                    for line_num, line in enumerate(file_lines):
                        line = line.strip()
                        if src_rev is None and "SRCREV" in line:
                            logging.info(f"    Line {line_num+1}: Found SRCREV line: {line}")
                            patterns = [
                                r'SRCREV\s*=\s*"([^"]+)"',
                                r"SRCREV\s*=\s*'([^']+)'",
                                r'SRCREV\s*=\s*([a-f0-9]{6,})',
                                r'SRCREV\s*=\s*([^\s;]+)',
                            ]
                            for pattern_regex in patterns:
                                match = re.search(pattern_regex, line)
                                if match:
                                    src_rev = match.group(1)
                                    logging.info(f"    ✓ Found SRCREV in {pattern}: {src_rev}")
                                    break
                        
                        if src_branch is None and "SRCBRANCH" in line:
                            logging.info(f"    Line {line_num+1}: Found SRCBRANCH line: {line}")
                            patterns = [
                                r'SRCBRANCH\s*=\s*"([^"]+)"',
                                r"SRCBRANCH\s*=\s*'([^']+)'",
                                r'SRCBRANCH\s*=\s*([^\s;]+)',
                            ]
                            for pattern_regex in patterns:
                                match = re.search(pattern_regex, line)
                                if match:
                                    src_branch = match.group(1)
                                    logging.info(f"    ✓ Found SRCBRANCH in {pattern}: {src_branch}")
                                    break
                        
                        if src_rev and src_branch:
                            break
                
                if src_rev and src_branch:
                    break
            else:
                logging.info(f"  ✗ File does not exist: {target_file}")
        
        # If still missing, try searching ALL .bb files in the directory
        if src_rev is None or src_branch is None:
            logging.info(f"Still missing info, searching all .bb files in directory")
            
            try:
                for filename in os.listdir(recipe_dir):
                    if filename.endswith('.bb') and filename.startswith(base_name):
                        target_file = os.path.join(recipe_dir, filename)
                        logging.info(f"  Checking file: {filename}")
                        
                        with open(target_file, 'r') as file:
                            content = file.read()
                            content = re.sub(r'\\\s*\n\s*', ' ', content)
                            file_lines = content.split('\n')
                            
                            for line in file_lines:
                                line = line.strip()
                                if src_rev is None and "SRCREV" in line:
                                    patterns = [
                                        r'SRCREV\s*=\s*"([^"]+)"',
                                        r"SRCREV\s*=\s*'([^']+)'",
                                        r'SRCREV\s*=\s*([a-f0-9]{6,})',
                                        r'SRCREV\s*=\s*([^\s;]+)',
                                    ]
                                    for pattern in patterns:
                                        match = re.search(pattern, line)
                                        if match:
                                            src_rev = match.group(1)
                                            logging.info(f"    ✓ Found SRCREV in {filename}: {src_rev}")
                                            break
                                
                                if src_branch is None and "SRCBRANCH" in line:
                                    patterns = [
                                        r'SRCBRANCH\s*=\s*"([^"]+)"',
                                        r"SRCBRANCH\s*=\s*'([^']+)'",
                                        r'SRCBRANCH\s*=\s*([^\s;]+)',
                                    ]
                                    for pattern in patterns:
                                        match = re.search(pattern, line)
                                        if match:
                                            src_branch = match.group(1)
                                            logging.info(f"    ✓ Found SRCBRANCH in {filename}: {src_branch}")
                                            break
                        
                        if src_rev and src_branch:
                            break
            except Exception as e:
                logging.error(f"Error searching directory {recipe_dir}: {e}")
    
    logging.info(f"=== FINAL RESULT for {recipe_name}: URI={src_uri}, REV={src_rev}, BRANCH={src_branch} ===")
    return src_uri, src_rev, src_branch

def clone_and_describe_repo(src_uri, src_branch, src_rev):
    with tempfile.TemporaryDirectory() as repo_dir:
        try:
            # Use the URI as-is since it's already in git@ format from extract_src_info
            git_url = src_uri
            
            logging.info(f"Attempting to clone via SSH: {git_url}")
            
            # Try cloning with the branch
            result = subprocess.run(
                ["git", "clone", "-b", src_branch, git_url, repo_dir], 
                check=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Checkout specific revision
            subprocess.run(
                ["git", "checkout", src_rev], 
                cwd=repo_dir, 
                check=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            
            # Get tag description
            result = subprocess.run(
                ["git", "describe", "--tags"], 
                cwd=repo_dir, 
                capture_output=True, 
                text=True, 
                check=True
            )
            tag = result.stdout.strip()
            logging.info(f"Successfully got tag: {tag}")
            return tag
            
        except subprocess.CalledProcessError as e:
            logging.error(f"Git operation failed for {git_url}: {e}")
            logging.error(f"stderr: {e.stderr if hasattr(e, 'stderr') else 'No stderr'}")
            # Return a fallback tag based on the revision
            return f"rev-{src_rev[:8]}" if src_rev else None

def main():
    print("Starting package version finder script...")
    parser = argparse.ArgumentParser(description="Read and parse pn-buildlist file.")
    parser.add_argument("--buildlist-path", required=True, help="Path to the pn-buildlist file")
    parser.add_argument("--yocto-layers-path", required=True, help="Path to the Yocto source layers (BitBake recipes)")
    
    args = parser.parse_args()
    print(f"Arguments parsed - buildlist: {args.buildlist_path}, yocto: {args.yocto_layers_path}")
    
    validate_path(args.buildlist_path, "pn-buildlist path")
    validate_path(args.yocto_layers_path, "Yocto source layers path")
    
    print("Reading buildlist...")
    buildlist_entries, package_names = read_buildlist(args.buildlist_path)
    print(f"Found {len(package_names)} packages in buildlist")
    
    print("Finding recipes...")
    recipes_found, recipes_not_found, recipe_paths = find_recipes(args.yocto_layers_path, package_names)
    print(f"Found {len(recipes_found)} recipes, missing {len(recipes_not_found)} recipes")
    
    package_info = {}
    print(f"Processing {len(recipe_paths)} recipes...")
    
    for recipe, path in recipe_paths.items():
        print(f"Processing: {recipe}")
        machine = next((entry[0] for entry in buildlist_entries if entry[1] == recipe), None)
        
        # Extract machine type from machine string (e.g., "2.0" from "rcrip2-0")
        machine_type = None
        if machine:
            # Try multiple patterns to extract version from machine string
            patterns = [
                r'(\d+\.\d+)',           # matches "2.0" in "2.0" 
                r'rcrip(\d+)-(\d+)',     # matches "rcrip2-0" -> "2.0"
                r'.*?(\d+)-(\d+)',       # matches any "X-Y" -> "X.Y"
                r'.*?(\d+)\.(\d+)',      # matches any "X.Y"
            ]
            
            for pattern in patterns:
                match = re.search(pattern, machine)
                if match:
                    if len(match.groups()) == 2:
                        machine_type = f"{match.group(1)}.{match.group(2)}"
                    else:
                        machine_type = match.group(1)
                    break
            
            logging.info(f"Machine: {machine} -> Machine Type: {machine_type}")
        
        logging.info(f"Processing recipe: {recipe}, machine: {machine}, machine_type: {machine_type}")
        
        src_uri, src_rev, src_branch = extract_src_info(path, machine_type)
        
        if src_uri and src_rev and src_branch:
            tag = clone_and_describe_repo(src_uri, src_branch, src_rev)
            package_info[recipe] = {
                "machine": machine,
                "recipe_path": path,
                "src_uri": src_uri,
                "src_rev": src_rev,
                "src_branch": src_branch,
                "tag": tag
            }
            logging.info(f"Successfully processed {recipe}")
        else:
            print(f"  WARNING: Incomplete info for {recipe}")
            logging.warning(f"Incomplete source info for {recipe}: URI={src_uri}, REV={src_rev}, BRANCH={src_branch}")
    
    print(f"Writing output for {len(package_info)} packages...")
    with open("final_output.json", "w") as f:
        json.dump(package_info, f, indent=4)
    
    print("Script completed successfully!")
    print(f"- Log file: buildlist_parser.log")
    print(f"- Output file: final_output.json")

if __name__ == "__main__":
    main()
