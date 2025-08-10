#!/usr/bin/env python3

# Copyright 2025 Apoorv Parle
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import shutil
import re
from SystemdUnitParser import SystemdUnitParser
import subprocess

def log_with_level(level, message):
    """Logs a message with a specified kernel log level."""
    print(f"multiquadlet_gen[{level}]: {message}")

def process_target_install_section(normal_dir, target_unit_name):
    """Parses a .target file and creates symlinks based on its [Install] section."""
    intermediate_target_file = os.path.join(normal_dir, target_unit_name)

    if not os.path.isfile(intermediate_target_file):
        log_with_level(3, f"Error: Intermediate target file not found: {intermediate_target_file}. Skipping.")
        return True

    config = SystemdUnitParser()
    try:
        config.read(intermediate_target_file)
    except Exception as e:
        print(f"Error: Failed to parse '{intermediate_target_file}': {e}", file=sys.stderr)
        log_with_level(3, f"Error: failed to parse file {intermediate_target_file}")
        return False

    if 'Install' not in config:
        log_with_level(6, f"No [Install] section found in {target_unit_name}. Skipping.")
        return True

    dependencies = {
        "wants":    config.get('Install', 'WantedBy', fallback=None),
        "requires": config.get('Install', 'RequiredBy', fallback=None),
        "upholds":  config.get('Install', 'UpheldBy', fallback=None)
    }

    for dep_type, dep_list in dependencies.items():
        targets = []
        if not dep_list:
            continue
        elif isinstance(dep_list, str):
            targets = dep_list.split()
        else:
            targets = sum([v.split() for v in dep_list],targets)

        log_with_level(6, f"Installing {target_unit_name} as type={dep_type} for target units = {targets}")
        for target in targets:
            symlink_dir = os.path.join(normal_dir, f"{target}.{dep_type}")
            symlink_path = os.path.join(symlink_dir, target_unit_name)

            try:
                os.makedirs(symlink_dir, exist_ok=True)
                os.symlink(os.path.join(normal_dir, target_unit_name), symlink_path)
                log_with_level(6, f"Created symlink: {symlink_path} -> {os.path.join(normal_dir, target_unit_name)} (type: {dep_type})")
            except FileExistsError:
                log_with_level(7, f"Symlink already exists at {symlink_path}.")
            except OSError as e:
                log_with_level(3, f"Error creating symlink {symlink_path}: {e}")
                return False
    
    log_with_level(6, f"Finished processing Install section for {target_unit_name}")
    return True

def main():
    """Main function to orchestrate the script's execution."""
    systemd_scope = os.environ.get("SYSTEMD_SCOPE")
    if systemd_scope == "user":
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not xdg_runtime_dir:
            log_with_level(3, "Error: SYSTEMD_SCOPE is 'user' but XDG_RUNTIME_DIR is not set.")
            sys.exit(1)
        input_dir = os.path.expanduser('~/.config/containers/multiquadlet/')
        interimdir = os.path.join(xdg_runtime_dir, "multiquadlet-generated")
    else:
        input_dir = os.path.abspath("/etc/containers/multiquadlet")
        interimdir = os.path.abspath("/run/multiquadlet-generated")

    if not len(sys.argv) > 1:
        log_with_level(3, f"Error: Must be run as: {os.path.basename(__file__)} gendir [gendir-early] [gendir-late]")
    gendir = sys.argv[1]

    if not os.path.isdir(input_dir):
        log_with_level(4, f"Warning: Input directory '{input_dir}' does not exist. Skipping multiquadlet processing.")
        sys.exit(0)

    shutil.rmtree(interimdir, ignore_errors=True)
    os.makedirs(interimdir, exist_ok=True)

    # Copy files
    try:
        files_to_copy = [f for f in os.listdir(input_dir) if re.match(r".*\.(container|network|volume|pod)$", f)]
        for f in files_to_copy:
            shutil.copy(os.path.join(input_dir, f), interimdir)
    except FileNotFoundError:
        pass # Directory might not exist, handled above

    # Process multiquadlet files
    for infile in [f for f in os.listdir(input_dir) if f.endswith('.multiquadlet')]:
        log_with_level(6, f"Processing input file: {infile}")
        input_path = os.path.join(input_dir, infile)
        files_generated_content = {}
        
        try:
            with open(input_path, 'r') as f:
                lines = f.read().splitlines()
                
            current_content = []
            current_fname = None
            skip_file = False

            for line in lines:
                match = re.match(r"^---\ (.+)\ ---$", line)
                if match:
                    current_fname=match.group(1)
                    output_path = os.path.join(interimdir, current_fname)
                    if current_fname in files_generated_content.keys() or os.path.exists(output_path):
                        log_with_level(3, f"Error: Output file '{output_path}' already exists before processing '{infile}'. Skipping '{infile}' altogether.")
                        skip_file = True
                        break
                    else:
                        files_generated_content[current_fname] = []
                else:
                    files_generated_content[current_fname].append(line)

            if skip_file:
                continue

            if len(files_generated_content) > 0:
                log_with_level(6, f"Generated files from {infile}:")
                for fname in files_generated_content.keys():
                    output_path = os.path.join(interimdir, fname)
                    with open(output_path, 'w') as out_f:
                        out_f.write(f"# Automatically generated by {os.path.basename(__file__)} from {os.path.basename(infile)}\n")
                        out_f.write('\n'.join(files_generated_content[fname]))
                        out_f.write('\n')
                    log_with_level(6, f"  {output_path}")
            else:
                log_with_level(6, f"No files generated from {infile}.")
        except IOError as e:
            log_with_level(3, f"Error: Cannot read input file '{input_path}', skipping: {e}")
            continue

    # Run podman generator
    try:
        podman_generator_path = f"/usr/lib/systemd/{systemd_scope}-generators/podman-{systemd_scope}-generator"
        subprocess.run([podman_generator_path, *sys.argv[1:]], env={**os.environ, 'QUADLET_UNIT_DIRS': interimdir}, check=True)
    except FileNotFoundError:
        log_with_level(3, f"Error: podman generator not found at '{podman_generator_path}'.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        log_with_level(3, f"Error: podman generator failed with exit code {e.returncode}.")
        sys.exit(e.returncode)

    # Process and copy generated target files
    for fname in os.listdir(interimdir):
        if fname.endswith(".target"):
            dest_file = os.path.join(gendir, fname)
            if not os.path.exists(dest_file):
                log_with_level(6, f"Copying '{fname}' to '{gendir}'...")
                shutil.copy(os.path.join(interimdir, fname), dest_file)
                process_target_install_section(gendir, fname)
            else:
                log_with_level(3, f"Error: {dest_file} already exists, skipping.")

    log_with_level(6, "Finished.")

if __name__ == "__main__":
    main()

