
# Copyright 2025 Apoorv Parle
# SPDX-License-Identifier: Apache-2.0

BUILD_DIR := build
TARGET := $(BUILD_DIR)/multiquadlet_gen

PYTHON_CFLAGS := $(shell python3-config --cflags --embed)
PYTHON_LDFLAGS := $(shell python3-config --ldflags --embed)

SYSTEMD_UNIT_PARSER_PATH := $(shell pip show SystemdUnitParser | grep Location | cut -d' ' -f2)/SystemdUnitParser/__init__.py

C_SOURCE_MAIN := $(BUILD_DIR)/multiquadlet_gen.c
C_SOURCE_MODULES := $(BUILD_DIR)/SystemdUnitParser.c

.PHONY: all clean

all: $(TARGET)

$(BUILD_DIR):
	@mkdir -p $(BUILD_DIR)

$(C_SOURCE_MAIN): multiquadlet_gen.py | $(BUILD_DIR)
	cython --embed -3 -o $@ $<

$(C_SOURCE_MODULES): $(SYSTEMD_UNIT_PARSER_PATH) | $(BUILD_DIR)
	cython -3 -o $@ $<

$(TARGET): $(C_SOURCE_MAIN) $(C_SOURCE_MODULES)
	# Modify the main C file to include the SystemdUnitParser module init function.
	perl -i.orig -pe 'if(/PyImport_AppendInittab.*PyInit_multiquadlet_gen/) { print("extern PyObject \*PyInit_SystemdUnitParser\(void\);\n"); $$l=$$_; $$l =~ s/multiquadlet_gen/SystemdUnitParser/g; print($$l); }' $<
	gcc $(PYTHON_CFLAGS) $(C_SOURCE_MAIN) $(C_SOURCE_MODULES) $(PYTHON_LDFLAGS) -o $@

clean:
	rm -rf $(BUILD_DIR)

