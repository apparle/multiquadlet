#!/usr/bin/env bash

# Copyright 2025 Apoorv Parle
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

log_with_level() {
    local level="${1:-6}" # Default to kernel info level (6)
    local message="${2:-}"
    echo "multiquadlet_gen[${level}]: ${message}"
}

check_dependencies() {
    if ! command -v jc &> /dev/null; then
        log_with_level 3 "Error: 'jc' command not found. Please install jc (e.g., 'sudo apt install jc' or 'sudo dnf install jc')."
        return 1
    fi
    if ! command -v jq &> /dev/null; then
        log_with_level 3 "Error: 'jq' command not found. Please install jq (e.g., 'sudo apt install jq' or 'sudo dnf install jq')."
        return 1
    fi
    return 0
}

check_dependencies || exit 1

process_target_install_section() {
    # Exit immediately if a command exits with a non-zero status within this function.
    set -euo pipefail

    local NORMAL_DIR="$1"
    local TARGET_UNIT_NAME=$2

    local INTERMEDIATE_TARGET_FILE="${NORMAL_DIR}"/"${TARGET_UNIT_NAME}"

    #log_with_level 6 "Processing install section for unit: ${TARGET_UNIT_NAME} from source: ${INTERMEDIATE_TARGET_FILE}"
    #log_with_level 6 "Generator output directory: ${NORMAL_DIR}"

    if [ ! -f "${INTERMEDIATE_TARGET_FILE}" ]; then
        log_with_level 3 "Error: Intermediate target file not found: ${INTERMEDIATE_TARGET_FILE}. Skipping."
        return 1 # Return success even if file not found, as it might be optional.
    fi

    # Parse the .target file using jc and jq
    local UNIT_CONFIG_JSON
    UNIT_CONFIG_JSON=$(cat "$INTERMEDIATE_TARGET_FILE" | jc --ini-dup 2>/dev/null )

    if [ -z "${UNIT_CONFIG_JSON}" ]; then
        log_with_level 3 "Error: Failed to parse ${INTERMEDIATE_TARGET_FILE} with jc. Check file syntax."
        return 1 # Indicate failure to parse.
    fi

    # Extract 'WantedBy', 'RequiredBy', and 'UpheldBy' values from the '[Install]' section.
    # `// empty` handles cases where the key might be missing, returning an empty string.
    local WANTED_BY
    WANTED_BY=$(echo "${UNIT_CONFIG_JSON}" | jq -r '(.Install.WantedBy // empty) | join(" ")' )
    local REQUIRED_BY
    REQUIRED_BY=$(echo "${UNIT_CONFIG_JSON}" | jq -r '(.Install.RequiredBy // empty) | join(" ")' )
    local UPHELD_BY
    UPHELD_BY=$(echo "${UNIT_CONFIG_JSON}" | jq -r '(.Install.UpheldBy // empty) | join (" ")' )

    # --- Helper function to create symlinks ---
    create_symlink_for_dependency () {
        local dep_type="$1" # e.g., "wants", "requires", "upholds"
        local dep_list="$2"

        if [ -n "${dep_list}" ]; then
            for target in ${dep_list}; do
                local SYMLINK_DIR="${NORMAL_DIR}/${target}.${dep_type}"
                local SYMLINK_PATH="${SYMLINK_DIR}/${TARGET_UNIT_NAME}"

                mkdir -p "${SYMLINK_DIR}" || { log_with_level 3 "Error: Failed to create directory ${SYMLINK_DIR}"; return 1; }

                if [ -L "${SYMLINK_PATH}" ] && [ "$(readlink "${SYMLINK_PATH}")" = "${INTERMEDIATE_TARGET_FILE}" ]; then
                    log_with_level 7 "Symlink already exists and is correct: ${SYMLINK_PATH}"
                else
                    if [ -e "${SYMLINK_PATH}" ]; then
                        log_with_level 3 "Error: Any entry already exists ${SYMLINK_PATH} . Skipping."
                        return 1
                        #log_with_level 6 "Removing old entry at ${SYMLINK_PATH}"
                        #rm -rf "${SYMLINK_PATH}" || { log_with_level 3 "Error: Failed to remove old entry ${SYMLINK_PATH}"; return 1; }
                    fi
                    ln -r -s "${INTERMEDIATE_TARGET_FILE}" "${SYMLINK_PATH}" || { log_with_level 3 "Error: Failed to create symlink ${SYMLINK_PATH}"; return 1; }
                    log_with_level 6 "Created symlink: ${SYMLINK_PATH} -> ${INTERMEDIATE_TARGET_FILE} (type: ${dep_type})"
                fi
            done
        fi
        return 0
    }

    # --- Process Dependencies ---
    create_symlink_for_dependency "wants"    "${WANTED_BY}"   || return 1
    create_symlink_for_dependency "requires" "${REQUIRED_BY}" || return 1
    create_symlink_for_dependency "upholds"  "${UPHELD_BY}"   || return 1

    log_with_level 6 "Finished processing install section for ${TARGET_UNIT_NAME}"
    return 0
}

if [[ "$SYSTEMD_SCOPE" == "user" ]]; then
    if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
        log_with_level 3 "Error: SYSTEMD_SCOPE is 'user' but XDG_RUNTIME_DIR is not set."
        exit 1
    fi
    INPUT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/multiquadlet"
    OUTDIR="$XDG_RUNTIME_DIR/multiquadlet-generated"
else
    INPUT_DIR="/etc/containers/multiquadlet"
    OUTDIR="/run/multiquadlet-generated"
fi

mkdir -p "$OUTDIR"
rm -rf "$OUTDIR"/*

if [[ ! -d "$INPUT_DIR" ]]; then
    log_with_level 4 "Warning: Input directory '$INPUT_DIR' does not exist. Skipping multiquadlet processing."
    exit 0
fi

find "$INPUT_DIR"/ -maxdepth 1 \( -name "*.container" -o -name "*.network" -o -name "*.volume" -o -name "*.pod" \) -exec cp -t "$OUTDIR"/ {} +

shopt -s nullglob
for infile in "$INPUT_DIR"/*.multiquadlet; do
    log_with_level 6 "Processing input file: $infile"
    if ! exec 3< "$infile"; then
        log_with_level 3 "Error: Cannot read input file '$infile', skipping."
        continue
    fi
    current=""
    started_section=0
    outfd=""
    skip_file=0
    files_generated=()

    while IFS='' read -r line <&3 || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^---\ (.+)\ ---$ ]]; then
            fname="${BASH_REMATCH[1]}"
            # Check for output file existence at this point
            if [[ -e "$OUTDIR/$fname" ]]; then
                log_with_level 3 "Error: Output file '$OUTDIR/$fname' already exists before processing '$infile'. Skipping '$infile'."
                skip_file=1
                break
            fi
            # Close previous output file if open
            if [[ -n "${outfd:-}" ]]; then
                exec {outfd}>&-
            fi
            # Open new output file and write the header
            exec {outfd}>"$OUTDIR/$fname" || {
                log_with_level 3 "Error: Failed to open output file '$OUTDIR/$fname' for writing."
                skip_file=1
                break
            }
            echo "# Automatically generated by $0 from $(basename "$infile")" >&$outfd
            current="$fname"
            started_section=1
            files_generated+=("$OUTDIR/$fname")
        elif [[ $started_section -eq 1 ]]; then
            # Only write section content after the first marker
            echo "$line" >&$outfd
        fi
    done
    exec 3<&-
    # Close last output file if open
    if [[ -n "${outfd:-}" ]]; then
        exec {outfd}>&-
    fi

    if (( skip_file )); then
        # Clean up any output files started for this input file if any
        for f in "${files_generated[@]}"; do
            rm -f -- "$f"
        done
        continue
    fi


    if ((${#files_generated[@]})); then
        log_with_level 6 "Generated files from $infile:"
        for f in "${files_generated[@]}"; do
            log_with_level 6 "  $f"
        done
    else
        log_with_level 6 "No files generated from $infile."
    fi
done


log_with_level 6 "Running: QUADLET_UNIT_DIRS=$OUTDIR /usr/lib/systemd/${SYSTEMD_SCOPE}-generators/podman-${SYSTEMD_SCOPE}-generator $@"
QUADLET_UNIT_DIRS="$OUTDIR" "/usr/lib/systemd/${SYSTEMD_SCOPE}-generators/podman-${SYSTEMD_SCOPE}-generator" "$@"

GENDIR=$1

#for file in "$OUTDIR"/*.target "$OUTDIR"/*.service; do
for file in "$OUTDIR"/*.target; do
    filename=$(basename "$file")
    dest_file="$GENDIR/$filename"

    if [ -e "$dest_file" ]; then
        log_with_level 4 "Warning: File '$filename' already exists in '$GENDIR'. Skipping."
    else
        log_with_level 6 "Copying '$filename' to '$GENDIR'..."
        cp "$file" "$dest_file"
        process_target_install_section "${GENDIR}" "${filename}"
    fi
done

log_with_level 6 "Finished."

