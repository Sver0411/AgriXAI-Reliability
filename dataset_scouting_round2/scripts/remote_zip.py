"""Read the official Zenodo ZIP index and selected members with verified byte ranges."""
import argparse
import csv
import io
import json
import struct
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = {"horti": "horti", "electro": "electro_v2", "lettuce": "lettuce"}


class RangeReader(io.RawIOBase):
    def __init__(self, url, size):
        self.url, self.size, self.pos = url, size, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        start = (0, self.pos, self.size)[whence]
        self.pos = start + offset
        if self.pos < 0:
            raise ValueError("Negative offset")
        return self.pos

    def read(self, length=-1):
        length = min(length if length >= 0 else self.size, self.size - self.pos)
        if length <= 0:
            return b""
        start, end = self.pos, self.pos + length - 1
        expected = f"bytes {start}-{end}/{self.size}"
        # A query key keeps range responses distinct in intervening HTTP caches.
        url = f"{self.url}?audit_range={start}-{end}"
        request = Request(url, headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=120) as reply:
                    if reply.status != 206 or reply.headers.get("Content-Range") != expected:
                        raise IOError(f"Unexpected range response: {reply.status} {reply.headers.get('Content-Range')}; expected {expected}")
                    payload = reply.read(length)
                break
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
        if len(payload) != length:
            raise IOError(f"Expected {length} bytes, received {len(payload)}")
        self.pos += length
        return payload


def fetch_member(archive, entry, destination):
    path = PurePosixPath(entry.filename)
    if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
        raise ValueError(entry.filename)
    dest = destination.joinpath(*(part.replace("%", "%25").replace(":", "%3A") for part in path.parts))
    if dest.exists():
        data = dest.read_bytes()
    else:
        remote = RangeReader(archive["links"]["self"], archive["size"])
        remote.seek(entry.header_offset)
        block = remote.read(30 + len(entry.filename.encode("utf-8")) + 4096 + entry.compress_size)
        if block[:4] != b"PK\x03\x04":
            raise ValueError(f"Invalid local header: {entry.filename}")
        names, extras = struct.unpack_from("<HH", block, 26)
        start = 30 + names + extras
        compressed = block[start:start+entry.compress_size]
        if len(compressed) != entry.compress_size:
            raise ValueError(f"Incomplete member: {entry.filename}")
        if entry.compress_type == ZIP_DEFLATED:
            data = zlib.decompress(compressed, -15)
        elif entry.compress_type == ZIP_STORED:
            data = compressed
        else:
            raise ValueError(f"Unsupported compression: {entry.compress_type}")
    if len(data) != entry.file_size or zlib.crc32(data) != entry.CRC:
        raise ValueError(f"ZIP size/CRC mismatch: {entry.filename}")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return entry.filename, len(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=ARCHIVES)
    parser.add_argument("--pick", type=Path, help="UTF-8 file of exact member paths to acquire")
    args = parser.parse_args()
    catalog = ROOT / "raw/catalog"
    meta = json.loads((catalog / (ARCHIVES[args.group] + ".json")).read_text(encoding="utf-8-sig"))
    archive = next(f for f in meta["files"] if f["key"].lower().endswith(".zip"))
    picks = args.pick.read_text(encoding="utf-8-sig").splitlines() if args.pick else []
    with ZipFile(RangeReader(archive["links"]["self"], archive["size"])) as z:
        files = [f for f in z.infolist() if not f.is_dir()]
        with (catalog / (args.group + "_zip_members.csv")).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["member", "bytes", "compressed_bytes", "crc32", "offset"])
            writer.writerows((f.filename, f.file_size, f.compress_size, f"{f.CRC:08x}", f.header_offset) for f in files)
        print(f"{args.group}: {len(files)} files; ZIP bytes={archive['size']}", flush=True)
        selected = [z.getinfo(name) for name in picks if name]
    if selected:
        destination = ROOT / "raw" / args.group / "zip_members"
        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, size in pool.map(lambda entry: fetch_member(archive, entry, destination), selected):
                print(f"{name}\t{size}", flush=True)


if __name__ == "__main__":
    main()
