#!/usr/bin/env python
# Copyright (C) 2025  A. Iooss
# SPDX-License-Identifier: MIT

"""
Chip identification script for ARM Cortex-M

Example usage:
    ./chiprec.py my-firmware-dump.bin
"""

import argparse
import sqlite3


def find_used_registers(firmware: bytes) -> set:
    """
    Find peripheral access patterns

    Returns read and written peripherals addresses.
    Addresses are ordered by position in file. This is important as addresses at
    the end of the file might be false positives.
    """
    # NOTE: maybe use a disassembler (e.g. capstone)
    regs = set()
    for fw_offset in range(2, len(firmware), 2):
        # Match LDR instruction using PC+imm
        instr = int.from_bytes(firmware[fw_offset : fw_offset + 2], "little")
        if (instr & 0xF800) != 0x4800:
            continue

        # Verify that previous instruction is not Thumb-2, else we might be
        # decoding half of a 32-bit instruction
        prev_instr = int.from_bytes(firmware[fw_offset - 2 : fw_offset], "little")
        if (prev_instr & 0xE000) == 0xE000:
            continue

        # Get data and destination register rd
        rb = (instr & 0xFF) << 2
        rd = (instr >> 8) & 0x07
        data_offset = (fw_offset + rb) // 4 * 4 + 4
        data = int.from_bytes(firmware[data_offset : data_offset + 4], "little")

        # Find following LDR/STR rd+imm which does the peripheral access
        # ARM Cortex-M peripherals are mapped at 0x40000000-0x60000000, see
        # https://developer.arm.com/documentation/dui0552/a/the-cortex-m3-processor/memory-model
        registers_containing_addr = [rd]
        for fw_offset2 in range(fw_offset + 2, len(firmware), 2):
            if not registers_containing_addr:
                break  # lost addr

            instr2 = int.from_bytes(firmware[fw_offset2 : fw_offset2 + 2], "little")
            if (prev_instr & 0xE000) == 0xE000:
                break  # don't handle Thumb-2

            if (instr2 & 0xF800) == 0x6800:
                # Found LDR reg+imm, verify that it uses rd
                rd2 = (instr2 >> 0) & 0x07
                rn2 = (instr2 >> 3) & 0x07
                if rn2 in registers_containing_addr:
                    addr = data + (((instr2 >> 6) & 0x1F) << 2)
                    if (
                        0x40000000 <= addr < 0x60000000
                        or 0xA0000000 <= addr < 0xE0000000
                    ):
                        regs.add((addr, "read"))
                elif rd2 in registers_containing_addr:
                    registers_containing_addr.remove(rd2)
            elif (instr2 & 0xF800) == 0x6000:
                # Found STR reg+imm, verify that it uses rd
                rn2 = (instr2 >> 3) & 0x07
                if rn2 in registers_containing_addr:
                    addr = data + (((instr2 >> 6) & 0x1F) << 2)
                    if (
                        0x40000000 <= addr < 0x60000000
                        or 0xA0000000 <= addr < 0xE0000000
                    ):
                        regs.add((addr, "write"))
            elif (instr2 & 0xF800) == 0x8000:
                # Found STRH reg+imm, verify that it uses rd
                rn2 = (instr2 >> 3) & 0x07
                if rn2 in registers_containing_addr:
                    addr = data + (((instr2 >> 6) & 0x1F) << 1)
                    if (
                        0x40000000 <= addr < 0x60000000
                        or 0xA0000000 <= addr < 0xE0000000
                    ):
                        regs.add((addr, "write"))
            elif (instr2 & 0xF800) == 0x7000:
                # Found STRB reg+imm, verify that it uses rd
                rn2 = (instr2 >> 3) & 0x07
                if rn2 in registers_containing_addr:
                    addr = data + ((instr2 >> 6) & 0x1F)
                    if (
                        0x40000000 <= addr < 0x60000000
                        or 0xA0000000 <= addr < 0xE0000000
                    ):
                        regs.add((addr, "write"))
            elif (instr2 & 0xF800) == 0x4800:
                # Other instruction: LDR PC+imm
                rd2 = (instr2 >> 8) & 0x07
                if rd2 in registers_containing_addr:
                    registers_containing_addr.remove(rd2)
            elif (instr2 & 0xFE00) == 0x1A00:
                # Other instruction: SUBS
                rd2 = (instr2 >> 0) & 0x7
                if rd2 in registers_containing_addr:
                    registers_containing_addr.remove(rd2)
            elif (instr2 & 0xF800) == 0x2000:
                # Other instruction: MOVS imm
                rd2 = (instr2 >> 8) & 0x07
                if rd2 in registers_containing_addr:
                    registers_containing_addr.remove(rd2)
            else:
                # Unknown instruction, stop
                break

    return regs


def dict_intersection_merge(d1: dict, d2: dict) -> dict:
    """
    Intersect two dicts on keys, and merge values (dict of lists)
    """
    if d1 is None:
        return d2  # 'None' means 'all' in our case
    d1 = d1.copy()
    for key in [k for k in d1.keys()]:
        if key not in d2:
            d1.pop(key)
        else:
            d1[key] += d2[key]
    return d1


def find_devices_by_register(cursor: sqlite3.Cursor, addr: int, access: str) -> dict:
    """
    Get devices matching a given register
    """
    res = cursor.execute(
        "SELECT device_id, peripheral_name, register_name "
        "FROM register "
        "JOIN peripheral ON peripheral_id == peripheral.id "
        "WHERE register_address <= ?1 "
        "AND register_address + 32 > ?1 "  # query performance optimisation
        "AND register_address + register_size / 8 > ?1",
        (addr,),
    )
    return {dev_id: [attr + [access]] for dev_id, *attr in res.fetchall()}


def find_devices(cursor: sqlite3.Cursor, regs: set) -> dict:
    """
    Get devices matching given registers
    """
    matchs = None
    for addr, access in list(regs):
        m = find_devices_by_register(cursor, addr, access)
        if not m:
            print(f"No devices match register 0x{addr:08x} (read), skipping")
            continue
        new_matchs = dict_intersection_merge(matchs, m)
        if not new_matchs:
            print(f"Intersection with register 0x{addr:08x} (read) is empty, skipping")
            continue
        matchs = new_matchs
    return matchs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "filename",
        nargs="+",
        type=argparse.FileType("rb"),
    )
    args = parser.parse_args()

    # Open database
    con = sqlite3.connect("database.db")

    for f in args.filename:
        cursor = con.cursor()
        firmware = f.read()

        # Disassemble to find registers reads/writes
        regs = find_used_registers(firmware)

        print(f"=== {f.name} ===")
        print(
            "Found addresses:",
            ", ".join([f"0x{addr:08x} ({access})" for addr, access in regs]),
        )
        matchs = find_devices(cursor, regs)
        print()
        for dev_id, registers in matchs.items():
            res = cursor.execute(
                "SELECT device_name, device_vendor, svd_filename FROM device "
                "WHERE id == ?",
                (dev_id,),
            )
            dev_name, dev_vendor, svd_filename = res.fetchone()
            if dev_vendor:
                dev_name = f"{dev_vendor} {dev_name}"
            print(f"{dev_name} ({svd_filename}):")
            for p_name, reg_name, access in registers:
                print(f"    {access} register {reg_name} of {p_name}")
            print()
