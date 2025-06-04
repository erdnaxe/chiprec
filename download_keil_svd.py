#!/usr/bin/env python
# Copyright (C) 2025  A. Iooss
# SPDX-License-Identifier: MIT

"""
Collect SVD from ARM Keil packs

Also see https://github.com/cmsis-svd/cmsis-svd-data/issues/44
"""

import os
import re
import zipfile
from io import BytesIO

import requests

# Some websites are blocking requests based on User-Agent header
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"
)
PACK_RE = re.compile(
    rb".*<pdsc\s+url=\"([^\"]+)\"\s+vendor=\"([^\"]+)\"\s+name=\"([^\"]+)\"\s+version=\"([^\"]+)\".*"
)


def get_vendor_packs_url():
    """Fetch database index and return non-deprecated packs URL"""
    r = requests.get(
        "https://www.keil.com/pack/index.pidx", headers={"User-Agent": USER_AGENT}
    )
    r.raise_for_status()
    for line in r.content.split(b"\n"):
        # As XML is generated, use a regex
        m = PACK_RE.match(line)
        if not m or b"deprecated" in line:
            continue
        url, vendor, name, version = [s.decode() for s in m.groups()]
        if not url.endswith("/"):
            url += "/"  # leading slash may be missing
        yield vendor, f"{url}{vendor}.{name}.{version}.pack"


def fetch_extract_pack(url: str, out_dir: str):
    """Fetch pack and extract SVD descriptions"""
    os.makedirs(out_dir, exist_ok=True)
    r = requests.get(url, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    pack_zip = zipfile.ZipFile(BytesIO(r.content))
    svd_paths = [p for p in pack_zip.namelist() if p[-4:].lower() in [".svd", ".xml"]]
    for path in svd_paths:
        svd_basename = os.path.basename(path)
        svd_content = pack_zip.open(path).read()
        if b"<device" not in svd_content or b"<peripherals" not in svd_content:
            continue  # not a SVD
        print(f"Writing {out_dir}{svd_basename}")
        open(f"{out_dir}{svd_basename}", "wb").write(svd_content.strip())


if __name__ == "__main__":
    # Download and extract svd
    already_downloaded = []
    if os.path.exists("downloaded_urls.txt"):
        with open("downloaded_urls.txt", "r") as f:
            for line in f:
                already_downloaded.append(line.strip())

    for vendor, url in get_vendor_packs_url():
        if url in already_downloaded:
            continue  # skip

        print(f"Downloading {vendor} '{url}'")
        try:
            fetch_extract_pack(url, f"keil-svd/{vendor}/")
        except Exception as e:
            print(e)
            continue

        # Remember already downloaded packages
        with open("downloaded_urls.txt", "a") as f:
            f.write(url + "\n")
