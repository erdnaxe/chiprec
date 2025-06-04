# Chiprec

Chiprec is a tool to guess which chips could potentially run a given firmware.
It uses peripheral registers and an SVD database to find by elimination chips
that have all the necessary peripherals required to run a given firmware dump.

**Currently chiprec disassembler only supports ARM Cortex-M.**

Link to the presentation and paper (in French): https://www.sstic.org/2025/presentation/identification_images_de_micrologiciels/

See also https://github.com/cmsis-svd/cmsis-svd-data/issues/44 about integration more CMSIS SVD directly into the cmsis-svd project.

## How to use

Firstly, you need to collect some SVD to create a database:
```bash
# Collect cmsis-svd SVD files
git clone https://github.com/cmsis-svd/cmsis-svd-data.git

# Collect Keil SVD files and patch
# The patch might need to be updated
./download_keil_svd.py
(cd keil-svd && patch -i ../keil-svd.patch)

# Generate SQLite database
shopt -s globstar
./chiprec_svd_import.py cmsis-svd-data/data/**/*.svd keil-svd/**/*.*
```

Then, you may identify a firmware dump using `./chiprec.py dump.bin`.

## Future improvements

`chiprec.py` is currently kept very simple, only using Python standard library
for the initial proof-of-concept.
This script could be greatly improved by:

  - Using Capstone-Engine for disassembly to add support for other
    architectures such as AVR.
  - Using initial stack pointer address (usually offset 0 of the firmware)
    to remove chips with smaller SRAM.
  - Detect the ARM Cortex-M variant from the disassembly and filter using
    `device->cpu->name,mpuPresent,fpuPresent` field in SVD.
