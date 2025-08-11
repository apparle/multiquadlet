# Multiquadlet - One file to rule them all quadlets

This project introduces multiquadlet, a systemd generator that extends the functionality of Podman's quadlet system. Multiquadlet allows users to define a single file containing multiple container, network, and volume configurations in a single file. The primary motivation for this tool is to simplify the management of a group of related services by consolidating their definitions into a single source file, similar to a typical docker compose file. 

## Motivation: Why Multiquadlet?

The quadlet system, integrated with Podman, is a powerful tool for generating systemd service files from declarative container definitions with a one-to-one mapping: a single .container or .network file is processed to generate a single corresponding systemd service file. While this simplicity is a strength for individual container services, it gets unwieldy when managing a complex multi-container application (eg: a db container, a broker container, a webservice container for a single app). Under the standard quadlet model, defining such a collection of services requires creating a separate .container, .network, or .volume file for each component. This means file proliferation, scattered configuration, and potential lack of consistency in the application's updates. While not a dealbreaker, it is definitely more cumbersome, unlike a typical docker compose file that can be a single, version-controllable source in one place.

##### Why not `podman compose`?
While this is an easy path when migrating from docker, it does have limitations:
- It is difficult to orchestrate cross-application (cross-compose file) dependencies. Eg: if a podman network is shared by 2 different application's compose files, then just using templated systemd compose service is not reliable.
	- This is not a podman limitation but a compose spec limitation and lack of well-thought systemd integration in that spec.
	- The `external` keyword does allow a workaround but it's not very clean IMO, and has no notion of systemd startup ordering.
- Getting podman auto-updates to work with templated services was too complicated.
- It's a community effort, not well-maintained and somewhat buggy for me

##### Why not `kube yaml`?
Podman does support Kubernetes style yaml files but it lacks the ability to express dependencies and ordering/restart between containers - https://github.com/containers/podman/issues/22496. Kubernetes doesn't support inter-dependencies, so this is unlikely to change.

The need for a consolidated, single-file approach was discussed on Podman GitHub issue containers/podman#26447 , but developers weren't convinced of the value, hence this standalone generator.

## Multiquadlet Goals
The multiquadlet generator is an attempt to solve these problems with these goals:
- Capture everything needed for an application in a single file.
	- But this should be optional. If someone prefers to split into multiple files, that should be permitted.
- Avoid extra user steps like _install_ or _convert_ that create other files. User should maintain and update the same file that they're using to set up the applicatoin, and not have to deal with other files.
- Avoid high-complexity in the generator which may need high-maintainance and run into the same maintainence issues as podman compose. 
- As https://xkcd.com/927/ highlights, _yet another standard_ is not the goal.
	- I want to avoid inventing a new format as much as possible. I for one, don't have the expertise or knowledge needed to invent new formats.
	- While I'm not opposed to inventing a brand new file-format and can discuss the ideas, there needs to be strong motivating reason, not just minor convenince.
- Provide first-class systemd integration similar to quadlets.

## Multiquadlet - Overview

It serves as an external solution that leverages the existing quadlet infrastructure without requiring any modifications to the Podman codebase itself.

#### File-format
A new file-format extension `.multiquadlet` is defined to capture "**multi**ple **quadlet**" definitions. This is a simple concatenation of the usual podman systemd unit files with a delimiter.
- The systemd-unit-file-name is defined at the top `--- someAppContainer.container ---`.
	- The files can be any format that podman quadlets support -- `.container`, `.network`, `.volume`, `.pod` etc. Additionally, it also supports the usual `.target` files part of native systemd.
	- TODO: Support for `.service` files could be added similar to `.target`, but there hasn't been a need for it yet
- This is followed by the usual spec for that quadlet file. It follows exact same syntax as [podman-systemd.unit](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- Many such files can be specified sequentially by specifying more `--- someOtherContainer.container ---` etc.

#### Location
Files must be placed under:
- Userspace containers: `~/.config/containers/multiquadlet/`
- System containers: `/etc/containers/multiquadlet`

In addition to `.multiquadlet` files, even standard podman unit files (`.container`, `.network`, `.volume`, `.pod`) can also be placed in this directory (instead of the usual `...containers/systemd` directory. This is necessary if that file is referenced from within a multiquadlet (eg: a `.network` file). Other than being able to cross-reference, no special processing is done for standard quadlet files. 

#### Systemd Generator
The multiquadlet tool operates as an intermediary systemd generator, translating a multiquadlet files into multiple quadlet files. Then it calls the native quadlet generator to process these quadlet files as usual. Since the native quadlet generator is used, everything that's supported by it is supported in these formats.

One thing to note -- this quadlet generator invocation is independent & parallel to it's processing of standard quadlet files under `.../containers/systemd`. Due to this, files can't be cross-referenced as volumes, networks etc.. Although you may still cross-reference Systemd service level dependencies as that's orchestrated by systemd after the generators.

#### Overall Workflow
1. A user creates a single multiquadlet file with a .multiquadlet extension. This file contains the concatenated content of multiple standard quadlet files (e.g., .container, .network) separated by a specific delimiter.
1. systemd detects the .multiquadlet file and, as part of its daemon-reload process, invokes the multiquadlet generator script.
1. The generator script reads the single input file and splits its contents based on the delimiter `--- <filename> ---` in separate files into a temporary directory.
1. Podman's native quadlet generator is then triggered on temporary directory, which processes each of these temporary files, creating the final .service unit files.
1. These resulting services can then be managed and controlled using standard systemctl commands.
1. Any updates to multiquadlet files are automatically propagated with the usual `sudo systemctl daemon-reload` or `systemctl --user daemon-reload`

## Installation

### Prerequisites

The following software is required to use multiquadlet:
- Podman (version 5.x or newer is recommended)
- systemd (with user-level generator support)
- Python 3.x
- Alternatively `jq` and `jc` if using bash script.

### Clone the Repository
``` bash
git clone https://github.com/apparle/multiquadlet.git
cd multiquadlet
```

### Generator Option 1: Python Script
The Python version of the generator, `multiquadlet_gen.py`, relies on a third-party Python package `SystemdUnitParser`, which is not available in in most Linux distribution repositories. 
While a `pip install SystemdUnitParser` command could install the package, a systemd generator runs in a minimal, isolated environment without access to a user's Python virtual environment (venv) or their local pip installations. If you've control over system-wide python installation to set up `SystemdUnitParser` python module, then you can choose to link the python script directly.

### Generator Option 2: Python script compiled to binary
To overcome the dependency problem above, Cython is used to compile the Python script and its dependencies into a single, standalone executable binary. The binary's dependencies are limited to common system libraries, such as `libpython`, which are readily available on most Linux distribution repositories. You can choose to even package python itself into the binary using `pyinstaller` project but libpython is readily available widely, so Cython is good enough. 

##### Build inside a fresh Container
- The included `Containerfile.build` to provide a clean, reproducible, and portable build environment. It is using `ubuntu:24.04` as base image, which will compile for Python 3.12.
- Modify it to use a base image that matches python version or even whole OS, as your deployment systemd machine.
- Build container image
	```
	podman build -t multiquadlet_gen_builder -f Containerfile.build
	```
- Build binary
  	```
	podman run --rm -v "$PWD":/build multiquadlet_gen_builder make
	```

#### Build locally from a venv
- Install `cython` and it's dependencies based on your linux distribution.
- Set up a python virtual environment (venv).
- Install SystemdUnitParser python module
	```
	pip install SystemdUnitParser
	```
- Build binary
	```
	make
	```

The binary `./build/multiquadlet_gen` binary is ready for use. 

### Generator Option 3: Bash Script
There's a bash script `multiquadlet_gen.sh` which uses `jq` and `jc` to process the files. While this is not the recommended as I may not maintain the bash script, it does work well today. It requires `jq` and `jc` must be installed. 

### Install the Generator
The generator should be installed at system-level or user-level or both in:
- User Level: `/usr/lib/systemd/user-generators/`
- System Level: `/usr/lib/systemd/system-generators/`

Create a symlink to the generator 
``` link.sh
# If using compiled binary
ln -s ""$(pwd)/build/multiquadlet_gen" /usr/lib/systemd/user-generators/multiquadlet_gen

# If using python script
ln -s ""$(pwd)/multiquadlet_gen.py" /usr/lib/systemd/user-generators/multiquadlet_gen.py

# If using bash script
ln -s ""$(pwd)/multiquadlet_gen.py" /usr/lib/systemd/user-generators/multiquadlet_gen.py
```


Now just doing a `sudo systemctl daemon-reload` or `systemctl --user daemon-reload` should invoke the systemd generator.

Create your multiquadlet files and start using them.

## Releases?
- This is too small a codebase to be versioned -- just use the latest on main branch.
- Compiled binaries have local dependencies with python versions, so it's best to compile locally.

## Exampes
TODO

## Debugging
- Use this command to inspect the output of generator, right after running `daemon-reload`
	``` file.sh
	# System containers
	journald -S '1 minute ago' | grep multiquadlet_gen

	# User containers
	journald --user -S '1 minute ago'` | grep multiquadlet_gen
	```

- Generator Not Found: If `systemctl --user daemon-reload` does not create the services, it is likely that the symlink to the multiquadlet script is not correctly placed.
- Syntax Errors:
	- If the delimiter format `--- <filename> ---` is incorrect, the multiquadlet generator itself may fail.
	- The generated files should not conflict with other similarly named files either generated or already present in the directory.
	- The generator will fail to create files if the syntax within one of the delimited sections is not a valid quadlet file format. systemd will report an error from the native quadlet generator in such cases. 
- The Shell vs. Python Behavior: The multiquadlet.sh shell script is a simpler implementation that may not handle all parsing and error conditions as gracefully as the multiquadlet.py Python version. The Python version, especially when compiled with Cython, offers more robust parsing and error handling, making it the preferred choice for reliable, long-term use.

# Contributing & Future Development
- Bug reports, Pull-requests, suggestions are welcome and encouraged.
- The project is an active effort to improve the Podman quadlet user experience. Long-term, if there's sufficient users, I'll attempt to integrate this into podman's native generators.
	- Star the repo if you see value and would like to see this integrated.
