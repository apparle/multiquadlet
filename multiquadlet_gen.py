#!/usr/bin/env python3

# Copyright 2025 Apoorv Parle
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import shutil
import re
from SystemdUnitParser import SystemdUnitParser
import subprocess
import tempfile

def log_with_level(level, message):
    """Logs a message with a specified kernel log level."""
    print(f"multiquadlet_gen[{level}]: {message}")

def get_quadlet_service_filename(fname):
    just_name, extension = os.path.splitext(fname)
    if extension == '.container':
        return f"{just_name}.service"
    elif extension in ['.pod', '.kube', '.network', '.volume', '.image', '.build']:
        file_type = extension[1:]
        return f"{just_name}-{file_type}.service"
    elif extension in ['.target', '.socket', '.service', '.timer']:
        return fname
    else:
        log_with_level(3, f"Error: Unknown file type {fname} while trying to figure out service name. Skipping.")
        return None

def update_source_path(f, source_file):
    if not os.path.isfile(f):
        log_with_level(3, f"Error: Intermediate quadlet generated file not found: {f}. Skipping.")
        return True

    config = SystemdUnitParser()
    try:
        config.read(f)
    except Exception as e:
        print(f"Error: Failed to parse '{f}': {e}", file=sys.stderr)
        log_with_level(3, f"Error: failed to parse file {f}")
        return True

    source_path_present = config.get('Unit', 'SourcePath', fallback=None)

    infile = os.path.basename(f)
    content = []
    
    try:
        with open(f, 'r') as fr:
            lines = fr.read().splitlines()
        for line in lines:
            if source_path_present:
                match = re.match(r"^\s*SourcePath=.*", line)
                if match:
                    content.append(f"SourcePath={source_file}")
                    log_with_level(6, f"Updated 'SourcePath={source_file}' in {f}")
                else:
                    content.append(line)
            else:
                content.append(line)
                match = re.match(r"^\s*\[Unit\]\s*", line)
                if match:
                    content.append(f"SourcePath={source_file}")
                    log_with_level(6, f"Inserted 'SourcePath={source_file}' in '{f}'")
        with open(f, 'w') as fw:
            fw.write('\n'.join(content))
            fw.write('\n')
    except IOError as e:
        log_with_level(3, f"Error: Cannot process input file '{f}', skipping: {e}")
        return True 

def process_unit_install_section(normal_dir, unit_name):
    """Parses a systemd file and creates symlinks based on its [Install] section."""
    intermediate_unit_file = os.path.join(normal_dir, unit_name)

    if not os.path.isfile(intermediate_unit_file):
        log_with_level(3, f"Error: Intermediate unit file not found: {intermediate_unit_file}. Skipping.")
        return True

    config = SystemdUnitParser()
    try:
        config.read(intermediate_unit_file)
    except Exception as e:
        print(f"Error: Failed to parse '{intermediate_unit_file}': {e}", file=sys.stderr)
        log_with_level(3, f"Error: failed to parse file {intermediate_unit_file}")
        return False

    if 'Install' not in config:
        log_with_level(6, f"No [Install] section found in {unit_name}. Skipping.")
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

        log_with_level(6, f"Installing {unit_name} as type={dep_type} for target units = {targets}")
        for target in targets:
            symlink_dir = os.path.join(normal_dir, f"{target}.{dep_type}")
            symlink_path = os.path.join(symlink_dir, unit_name)

            try:
                os.makedirs(symlink_dir, exist_ok=True)
                relative_target_path = os.path.relpath(os.path.join(normal_dir, unit_name), symlink_dir)
                os.symlink(relative_target_path, symlink_path)
                log_with_level(6, f"Created symlink: {symlink_path} -> {relative_target_path} (type: {dep_type})")
            except FileExistsError:
                log_with_level(7, f"Symlink already exists at {symlink_path}.")
            except OSError as e:
                log_with_level(3, f"Error creating symlink {symlink_path}: {e}")
                return False
    
    log_with_level(6, f"Finished processing Install section for {unit_name}")
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
    else:
        input_dir = os.path.abspath("/etc/containers/multiquadlet")

    if not len(sys.argv) > 1:
        log_with_level(3, f"Error: Must be run as: {os.path.basename(__file__)} gendir [gendir-early] [gendir-late]")
    gendir = sys.argv[1]

    if not os.path.isdir(input_dir):
        log_with_level(4, f"Warning: Input directory '{input_dir}' does not exist. Skipping multiquadlet processing.")
        sys.exit(0)

    interim_top_dir = tempfile.TemporaryDirectory(prefix='multiquadlet_gen_')
    interimdir = os.path.join(interim_top_dir.name, "multiquadlet_interim")
    os.makedirs(interimdir, exist_ok=True)
    log_with_level(6, f"Using temporary directory for intermediate quadet files: {interimdir}")

    source_file_map = {}

    # Copy files
    try:
        files_to_copy = [f for f in os.listdir(input_dir) if re.match(r".*\.(container|network|volume|pod)$", f)]
        for f in files_to_copy:
            infile = os.path.join(input_dir, f)
            shutil.copy(infile, interimdir)
            source_file_map[get_quadlet_service_filename(f)] = infile
    except FileNotFoundError:
        pass


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
                    with open(output_path, 'w') as fw:
                        fw.write(f"# Automatically generated by {os.path.basename(__file__)} from {os.path.basename(infile)}\n")
                        fw.write('\n'.join(files_generated_content[fname]))
                        fw.write('\n')
                    log_with_level(6, f"  {output_path}")
                    source_file_map[get_quadlet_service_filename(fname)] = input_path
            else:
                log_with_level(6, f"No files generated from {infile}.")
        except IOError as e:
            log_with_level(3, f"Error: Cannot read input file '{input_path}', skipping: {e}")
            continue

    
    # Run podman generator
    interimdir_gen = os.path.join(interim_top_dir.name, "generator")
    interimdir_gen_early = os.path.join(interim_top_dir.name, "generator.early")
    interimdir_gen_late = os.path.join(interim_top_dir.name, "generator.late")
    try:
        podman_generator_path = f"/usr/lib/systemd/{systemd_scope}-generators/podman-{systemd_scope}-generator"
        result = subprocess.run([podman_generator_path, interimdir_gen, interimdir_gen_early, interimdir_gen_late], env={**os.environ, 'QUADLET_UNIT_DIRS': interimdir}, check=True, capture_output=True, text=True)
        log_with_level(6, f"Output of podman quadlet generator: \nSTDOUT: \n{result.stdout} \nSTDERR: \n{result.stderr}")
        if result.returncode != 0:
            log_with_level(3, f"Error: podman generator failed with exit code {result.returncode}.")
            sys.exit(result.returncode)
        else:
            log_with_level(6, f"Podman generator completed successfuly.")
    except FileNotFoundError:
        log_with_level(3, f"Error: podman generator not found at '{podman_generator_path}'.")
        sys.exit(1)
    except Exception as e:
        log_with_level(3, f"Error: unexpected error while running podman generator: {e}.")
        sys.exit(1)

    # Fix up the SourcePath in the generated files
    for fname in [f for f in os.listdir(interimdir_gen) if os.path.isfile(os.path.join(interimdir_gen, f))]:
        update_source_path(os.path.join(interimdir_gen, fname), source_file_map[fname])
    shutil.copytree(interimdir_gen, gendir, symlinks=True, dirs_exist_ok=True)

    # Process and copy generated target files
    for fname in os.listdir(interimdir):
        if fname.endswith(('.target', '.socket', '.service', '.timer')):
            dest_file = os.path.join(gendir, fname)
            if not os.path.exists(dest_file):
                update_source_path(os.path.join(interimdir, fname), source_file_map[fname])
                log_with_level(6, f"Copying '{fname}' to '{gendir}'...")
                shutil.copy(os.path.join(interimdir, fname), dest_file)
                process_unit_install_section(gendir, fname)
            else:
                log_with_level(3, f"Error: {dest_file} already exists, skipping.")

    log_with_level(6, "Finished.")

if __name__ == "__main__":
    main()

