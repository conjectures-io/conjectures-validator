"""Bound the central directory before ZipFile allocates its entry objects."""
import os
import struct
import zipfile

MAX_DIRECTORY_BYTES = 32 * 1024 * 1024


def check_directory(source, max_files):
    source.seek(0, os.SEEK_END)
    length = source.tell()
    source.seek(max(0, length - 65557))
    tail = source.read(65557)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or len(tail) - position < 22:
        raise zipfile.BadZipFile()
    end = struct.unpack_from("<4s4H2LH", tail, position)
    if position + 22 + end[7] != len(tail):
        raise zipfile.BadZipFile()
    end_offset = length - len(tail) + position
    if end[1] != 0 or end[2] != 0:
        raise zipfile.BadZipFile()
    entries, size = end[4], end[5]
    directory_end = end_offset
    locator = b""
    if end_offset >= 20:
        source.seek(end_offset - 20)
        locator = source.read(20)
    if locator[:4] == b"PK\x06\x07":
        _, disk, offset, disks = struct.unpack("<4sLQL", locator)
        if disk != 0 or disks != 1 or offset != end_offset - 76:
            raise zipfile.BadZipFile()
        source.seek(offset)
        record = source.read(56)
        if len(record) != 56 or record[:4] != b"PK\x06\x06":
            raise zipfile.BadZipFile()
        zip64 = struct.unpack("<4sQ2H2L4Q", record)
        if zip64[1] != 44 or zip64[4] != 0 or zip64[5] != 0:
            raise zipfile.BadZipFile()
        entries, size = zip64[7], zip64[8]
        directory_end = offset
    if entries > max_files or size > MAX_DIRECTORY_BYTES:
        from submission_api.trajectory_schema import PackageError
        raise PackageError("zip directory exceeds validation limits")
    if size > directory_end:
        raise zipfile.BadZipFile()
    source.seek(directory_end - size)
    remaining, count = size, 0
    while remaining:
        if remaining < 46:
            raise zipfile.BadZipFile()
        header = source.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise zipfile.BadZipFile()
        variable = sum(struct.unpack_from("<3H", header, 28))
        remaining -= 46 + variable
        if remaining < 0:
            raise zipfile.BadZipFile()
        count += 1
        if count > max_files:
            from submission_api.trajectory_schema import PackageError
            raise PackageError("zip directory exceeds validation limits")
        source.seek(variable, os.SEEK_CUR)
    source.seek(0)
