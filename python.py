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
                
                # If the recipe is named "common", try to match it to a package name in the path
                if recipe_name == "common":
                    for pkg in package_names:
                        if pkg in root:
                            recipe_name = pkg
                            break
                
                if recipe_name in package_names:
                    recipes_found.append(recipe_name)
                    recipe_paths[recipe_name] = os.path.join(root, file)
                    if recipe_name in recipes_not_found:
                        recipes_not_found.remove(recipe_name)
                    logging.info(f"Recipe found: {recipe_name} at {os.path.join(root, file)}")
    
    return recipes_found, recipes_not_found, recipe_paths

def extract_src_info(recipe_path, machine_type):
    src_uri = None
    src_rev = None
    src_branch = None
    
    # Read the main recipe file
    with open(recipe_path, 'r') as file:
        lines = file.readlines()
    
    for line in lines:
        line = line.strip()
        if "SRC_URI" in line and "git" in line:
            match = re.search(r'git://([^;]+)', line)
            if match:
                src_uri = match.group(1)
                src_uri = re.sub(r'\.com/', '.com/', src_uri)
        elif "SRCREV" in line:
            match = re.search(r'SRCREV\s*=\s*"([^"]+)"', line)
            if match:
                src_rev = match.group(1)
        elif "SRCBRANCH" in line:
            match = re.search(r'SRCBRANCH\s*=\s*"([^"]+)"', line)
            if match:
                src_branch = match.group(1)
    
    # If SRCREV or SRCBRANCH are missing, search for them in related files
    if machine_type and (src_rev is None or src_branch is None):
        recipe_dir = os.path.dirname(recipe_path)
        recipe_filename = os.path.basename(recipe_path)
        
        # Extract base name (remove .inc or .bb extension)
        if recipe_filename.endswith('.inc'):
            base_name = recipe_filename[:-4]  # Remove .inc
        elif recipe_filename.endswith('.bb'):
            base_name = recipe_filename[:-3]   # Remove .bb
        else:
            base_name = recipe_filename
        
        logging.info(f"Searching for missing info for {recipe_filename}, base_name: {base_name}, machine_type: {machine_type}")
        
        # Look for machine-specific files in the same directory
        # Handle both patterns: base_name_version.bb and base_name-version.bb
        possible_patterns = [
            f"{base_name}_{machine_type}.bb",
            f"{base_name}-{machine_type}.bb",
            f"{base_name}.{machine_type}.bb"
        ]
        
        for pattern in possible_patterns:
            target_file = os.path.join(recipe_dir, pattern)
            if os.path.exists(target_file):
                logging.info(f"Found machine-specific file: {target_file}")
                
                # Extract missing info from the machine-specific file
                with open(target_file, 'r') as file:
                    for line in file:
                        line = line.strip()
                        if src_rev is None and "SRCREV" in line:
                            match = re.search(r'SRCREV\s*=\s*"([^"]+)"', line)
                            if match:
                                src_rev = match.group(1)
                                logging.info(f"Found SRCREV in {pattern}: {src_rev}")
                        
                        if src_branch is None and "SRCBRANCH" in line:
                            match = re.search(r'SRCBRANCH\s*=\s*"([^"]+)"', line)
                            if match:
                                src_branch = match.group(1)
                                logging.info(f"Found SRCBRANCH in {pattern}: {src_branch}")
                        
                        # Break early if we found both
                        if src_rev and src_branch:
                            break
                
                # If we found what we needed, break from pattern loop
                if src_rev and src_branch:
                    break
        
        # If still missing, try searching ALL .bb files in the directory
        if src_rev is None or src_branch is None:
            logging.info(f"Still missing info for {recipe_path}, searching all .bb files in directory")
            
            try:
                for filename in os.listdir(recipe_dir):
                    if filename.endswith('.bb') and filename.startswith(base_name):
                        target_file = os.path.join(recipe_dir, filename)
                        logging.info(f"Checking file: {filename}")
                        
                        with open(target_file, 'r') as file:
                            for line in file:
                                line = line.strip()
                                if src_rev is None and "SRCREV" in line:
                                    match = re.search(r'SRCREV\s*=\s*"([^"]+)"', line)
                                    if match:
                                        src_rev = match.group(1)
                                        logging.info(f"Found SRCREV in {filename}: {src_rev}")
                                
                                if src_branch is None and "SRCBRANCH" in line:
                                    match = re.search(r'SRCBRANCH\s*=\s*"([^"]+)"', line)
                                    if match:
                                        src_branch = match.group(1)
                                        logging.info(f"Found SRCBRANCH in {filename}: {src_branch}")
                        
                        # Break if we found both
                        if src_rev and src_branch:
                            break
            except Exception as e:
                logging.error(f"Error searching directory {recipe_dir}: {e}")
    
    return src_uri, src_rev, src_branch

def clone_and_describe_repo(src_uri, src_branch, src_rev):
    with tempfile.TemporaryDirectory() as repo_dir:
        try:
            subprocess.run(["git", "clone", "-b", src_branch, src_uri, repo_dir], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "checkout", src_rev], cwd=repo_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = subprocess.run(["git", "describe", "--tags"], cwd=repo_dir, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logging.error(f"Git operation failed for {src_uri}: {e}")
            return None

def main():
    parser = argparse.ArgumentParser(description="Read and parse pn-buildlist file.")
    parser.add_argument("--buildlist-path", required=True, help="Path to the pn-buildlist file")
    parser.add_argument("--yocto-layers-path", required=True, help="Path to the Yocto source layers (BitBake recipes)")
    
    args = parser.parse_args()
    
    validate_path(args.buildlist_path, "pn-buildlist path")
    validate_path(args.yocto_layers_path, "Yocto source layers path")
    
    buildlist_entries, package_names = read_buildlist(args.buildlist_path)
    recipes_found, recipes_not_found, recipe_paths = find_recipes(args.yocto_layers_path, package_names)
    
    package_info = {}
    for recipe, path in recipe_paths.items():
        machine = next((entry[0] for entry in buildlist_entries if entry[1] == recipe), None)
        
        # Extract machine type from machine string (e.g., "2.0" from machine info)
        machine_type = None
        if machine:
            # Extract version from machine string - adjust this regex based on your machine format
            match = re.search(r'(\d+\.\d+)', machine)
            if match:
                machine_type = match.group(1)
        
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
            logging.warning(f"Incomplete source info for {recipe}: URI={src_uri}, REV={src_rev}, BRANCH={src_branch}")
    
    with open("final_output.json", "w") as f:
        json.dump(package_info, f, indent=4)
    
    if __name__ == "__main__":
        main()
