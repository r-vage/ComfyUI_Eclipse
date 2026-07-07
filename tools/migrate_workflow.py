#!/usr/bin/env python3
"""
Workflow Migration Tool for ComfyUI Eclipse v4.0.0
Usage:
    python migrate_workflow.py <path_to_workflow.json or directory>
"""

import os
import sys
import shutil

MAPPINGS = {}

def migrate_file(filepath: str) -> bool:
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return False

    replacements_made = {}
    new_content = content

    # Sort MAPPINGS by key length in descending order to avoid substring replacement issues
    sorted_mappings = sorted(MAPPINGS.items(), key=lambda x: len(x[0]), reverse=True)
    for old, new in sorted_mappings:
        count = new_content.count(old)
        if count > 0:
            new_content = new_content.replace(old, new)
            replacements_made[old] = count

    if not replacements_made:
        print(f"No Eclipse v2/legacy node occurrences found in: {filepath}")
        return False

    # Create backup
    backup_path = filepath + ".bak"
    try:
        shutil.copy2(filepath, backup_path)
    except Exception as e:
        print(f"Failed to create backup for {filepath}: {e}")
        return False

    # Write migrated content
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"✓ Migrated: {filepath}")
        print(f"  Backup saved to: {backup_path}")
        for node, count in replacements_made.items():
            print(f"    - Replaced '{node}' -> {count} time(s)")
        return True
    except Exception as e:
        print(f"Error writing migrated workflow to {filepath}: {e}")
        return False

def main():
    global MAPPINGS
    if len(sys.argv) < 2:
        print("Usage: python migrate_workflow.py <path_to_workflow.json or directory> [path_to_mapping_file]")
        sys.exit(1)

    target = sys.argv[1]
    
    mapping_file = "migration_eclipse.txt, migration_rvtoolsv1.txt, migration_rvtoolsv2.txt"
    if len(sys.argv) >= 3:
        mapping_file = sys.argv[2]

    # Resolve mapping file path(s)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths_to_load = [p.strip() for p in mapping_file.split(',') if p.strip()]
    file_mappings = {}
    loaded_sources = []

    for mapping_path in paths_to_load:
        resolved_mapping_path = mapping_path
        if not os.path.isabs(resolved_mapping_path):
            alt_path = os.path.join(script_dir, resolved_mapping_path)
            if os.path.exists(alt_path):
                resolved_mapping_path = alt_path
            else:
                cwd_path = os.path.abspath(resolved_mapping_path)
                if os.path.exists(cwd_path):
                    resolved_mapping_path = cwd_path

        if os.path.exists(resolved_mapping_path) and os.path.isfile(resolved_mapping_path):
            try:
                with open(resolved_mapping_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '|' in line:
                            parts = line.split('|', 1)
                            old_name = parts[0].strip()
                            new_name = parts[1].strip()
                            if old_name and new_name:
                                file_mappings[old_name] = new_name
                loaded_sources.append(resolved_mapping_path)
            except Exception as e:
                print(f"Warning: Failed to read mapping file '{resolved_mapping_path}': {e}")

    if file_mappings:
        MAPPINGS = file_mappings
        print(f"Loaded mappings from: {', '.join(loaded_sources)}")
    else:
        print(f"Warning: No valid mappings loaded from '{mapping_file}'. Using fallback hardcoded mappings.")


    if not os.path.exists(target):
        print(f"Error: Target path '{target}' does not exist.")
        sys.exit(1)

    if os.path.isfile(target):
        migrate_file(target)
    elif os.path.isdir(target):
        print(f"Scanning directory for .json workflows: {target}")
        json_files = []
        for root, _, files in os.walk(target):
            for file in files:
                if file.lower().endswith('.json'):
                    json_files.append(os.path.join(root, file))

        if not json_files:
            print("No .json files found in the directory.")
            return

        migrated_count = 0
        for filepath in json_files:
            if migrate_file(filepath):
                migrated_count += 1

        print(f"\nMigration completed. Total files successfully migrated: {migrated_count}")

if __name__ == "__main__":
    main()
