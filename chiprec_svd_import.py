#!/usr/bin/env python
# Copyright (C) 2025  A. Iooss
# SPDX-License-Identifier: MIT

"""
Chiprec SVD to SQLite database script

Example usage:
    shopt -s globstar
    ./chiprec_svd_import.py cmsis-svd-data/data/**/*.svd keil-svd/**/*.*
"""

import argparse
import os
import sqlite3
import xml.etree.ElementTree as ET

SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS "device" (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL,
    device_vendor TEXT,
    svd_filename TEXT NOT NULL,
    UNIQUE(device_name)
);
CREATE TABLE IF NOT EXISTS "peripheral" (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER,
    peripheral_name TEXT NOT NULL,
    peripheral_address INTEGER NOT NULL,
    FOREIGN KEY(device_id) REFERENCES device(id),
    UNIQUE(peripheral_name, device_id)
);
CREATE TABLE IF NOT EXISTS "register" (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    peripheral_id INTEGER,
    register_name TEXT NOT NULL,
    register_access INTEGER NOT NULL,
    register_address INTEGER NOT NULL,
    register_size INTEGER NOT NULL,
    FOREIGN KEY(peripheral_id) REFERENCES peripheral(id),
    UNIQUE(register_name, peripheral_id)
);

CREATE INDEX IF NOT EXISTS "register_register_address_idx" ON register(register_address);
"""


def fix_reg_access_typo(access: str) -> str:
    """Fix some manufacturers-introduced typo"""
    access = access.replace("read-onlye", "read-only")
    access = access.replace("read-wirte", "read-write")
    access = access.replace("read_write", "read-write")
    access = access.replace("read-writeonce", "read-write")
    access = access.replace("writeonce", "write-only")
    if access == "read":
        access = "read-only"
    if access == "write":
        access = "write-only"
    assert access in [
        "read-write",
        "write-only",
        "read-only",
    ], f"Got bad access value '{access}'"
    return access


def xml_get_text_or(root, key: str, default="") -> str:
    """Helper to quickly get a XML node value"""
    res = root.find(key)
    if res is not None and res.text is not None:
        return res.text.strip()
    return default


def add_svd_to_database(cursor: sqlite3.Cursor, file) -> None:
    """
    Parse System View Description (SVD) and add peripherals to SQLite database
    """
    svd_filename = os.path.basename(file.name)
    root = ET.parse(file).getroot()

    # Collect peripherals offset
    # This is needed to resolve derivedFrom later
    peripherals_offset = {}
    for peripheral in root.findall("./peripherals/peripheral"):
        p_name = peripheral.find("name").text
        p_offset = peripheral.find("./addressBlock/offset")
        if p_offset is not None and p_offset.text is not None:
            peripherals_offset[p_name] = int(p_offset.text, 0)

    # Save device
    device_name = root.find("./name").text.strip()
    device_vendor = xml_get_text_or(root, "./vendor")
    cursor.execute(
        "INSERT OR IGNORE INTO device (device_name, device_vendor, svd_filename) VALUES (?, ?, ?)",
        (device_name, device_vendor, svd_filename),
    )
    res = cursor.execute("SELECT id FROM device WHERE device_name = ?", (device_name,))
    (device_id,) = res.fetchone()

    # Collect peripherals
    for peripheral in root.findall("./peripherals/peripheral"):
        # Get peripheral name and address
        p_name = peripheral.find("name").text.strip().upper()
        base_addr = peripheral.find("baseAddress")
        if base_addr is None or base_addr.text is None:
            print(f"{svd_filename}/{p_name}: missing base address, skipping")
            continue
        p_address = int(base_addr.text, 0)
        derived_from = peripheral.get("derivedFrom", p_name)
        p_address += peripherals_offset.get(derived_from, 0)

        # Save peripheral
        cursor.execute(
            "INSERT OR IGNORE INTO peripheral (device_id, peripheral_name, peripheral_address) "
            "VALUES (?, ?, ?)",
            (device_id, p_name, p_address),
        )
        res = cursor.execute(
            "SELECT id FROM peripheral WHERE peripheral_name = ? AND device_id = ?",
            (
                p_name,
                device_id,
            ),
        )
        (peripheral_id,) = res.fetchone()

        # Collect registers
        for register in peripheral.findall("./registers/register"):
            r_name = register.find("name").text
            r_address = p_address + int(register.find("addressOffset").text, 0)

            # Get register size and access
            r_size = int(xml_get_text_or(register, "size", "32"), 0)
            r_access = xml_get_text_or(register, "access", "read-write").lower()
            r_access = fix_reg_access_typo(r_access)

            # Save register
            cursor.execute(
                "INSERT OR IGNORE INTO register (peripheral_id, register_name, "
                "register_access, register_address, register_size) "
                "VALUES (?, ?, ?, ?, ?)",
                (peripheral_id, r_name, r_access, r_address, r_size),
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "filename",
        nargs="+",
    )
    args = parser.parse_args()

    # Init database
    con = sqlite3.connect("database.db")
    con.executescript(SQL_SCHEMA)

    for path in args.filename:
        cursor = con.cursor()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                add_svd_to_database(cursor, f)
        except Exception as e:
            raise RuntimeError(f"Failed to load {path}") from e
        con.commit()
