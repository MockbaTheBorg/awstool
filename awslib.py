"""Core library for labeled Hercules AWS tape inspection and extraction."""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cli_contract import CommandError, dataclass_to_plain


EBCDIC_CODEC = "cp037"


class AwsToolError(CommandError):
    """Base exception for tape parsing and extraction failures."""


class UnsupportedTapeError(AwsToolError):
    """Raised when the tape is outside the supported v1 scope."""


class ExtractionModeError(AwsToolError):
    """Raised when a requested extraction mode is not allowed."""


@dataclass
class AwsRecord:
    index: int
    current_length: int
    previous_length: int
    flags1: int
    flags2: int
    payload: bytes

    @property
    def is_tapemark(self) -> bool:
        return self.current_length == 0


@dataclass
class AwsTapeFile:
    index: int
    records: list[AwsRecord]


@dataclass
class TapeLabel:
    kind: str
    raw_text: str


@dataclass
class DatasetLabelSet:
    header_labels: list[TapeLabel]
    trailer_labels: list[TapeLabel]
    volume: str | None
    dataset_name: str
    record_format: str | None
    block_length: int | None
    logical_record_length: int | None
    block_attribute: str | None
    carriage_control: str | None
    label_technique: str | None
    dataset_sequence: str | None
    creation_program: str | None


@dataclass
class MemberInfo:
    name: str
    start_ttr: str
    start_track: int
    start_record: int
    alias: bool
    user_data_halfwords: int
    format: str | None = None
    size_bytes: int | None = None
    size_records: int | None = None
    text_classification: str | None = None
    extraction_modes: list[str] = field(default_factory=list)
    force_modes: list[str] = field(default_factory=list)


@dataclass
class DatasetInfo:
    tape_path: str
    dataset_index: int
    file_index: int
    dataset_name: str
    volume: str | None
    label_type: str
    record_format: str | None
    logical_record_length: int | None
    block_length: int | None
    block_attribute: str | None
    carriage_control: str | None
    creation_program: str | None
    dataset_sequence: str | None
    data_record_count: int
    data_size_bytes: int
    dataset_kind: str
    member_aware: bool
    member_count: int | None = None
    text_classification: str | None = None
    extraction_modes: list[str] = field(default_factory=list)
    force_modes: list[str] = field(default_factory=list)
    warning: str | None = None
    validation_status: str = "validated"
    validation_notes: list[str] = field(default_factory=list)


@dataclass
class UnlabeledFileInfo:
    file_index: int
    block_count: int
    size_bytes: int
    min_block_size: int
    max_block_size: int
    avg_block_size: int
    first_block_preview: str | None = None


@dataclass
class IebcopyHeader:
    copy1_length: int
    block_size: int
    logical_record_length: int
    record_format_byte: int
    key_length: int

    @property
    def record_format(self) -> str:
        return decode_recfm_byte(self.record_format_byte)


@dataclass
class IebcopyRecordSummary:
    kind: str
    record_index: int
    length: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class IebcopyExtent:
    volume_sequence: bytes
    device_modifier: int
    track_count: int
    start_cylinder: int
    start_head: int
    end_cylinder: int
    end_head: int
    relative_track_base: int

    def contains(self, cylinder: int, head: int) -> bool:
        if cylinder < self.start_cylinder or cylinder > self.end_cylinder:
            return False
        if cylinder == self.start_cylinder and head < self.start_head:
            return False
        if cylinder == self.end_cylinder and head > self.end_head:
            return False
        return True

    def to_relative_track(self, cylinder: int, head: int) -> int:
        return self.relative_track_base + ((cylinder - self.start_cylinder) * 15) + (
            head - self.start_head
        )


@dataclass
class IebcopyMemberBlock:
    cchh: bytes
    record_number: int
    key_length: int
    data: bytes
    ttr_value: int | None = None


@dataclass
class IebcopyDataset:
    header: IebcopyHeader
    extents: list[IebcopyExtent]
    members: list[MemberInfo]
    all_member_blocks: list[IebcopyMemberBlock]
    record_summaries: list[IebcopyRecordSummary]
    validation_notes: list[str] = field(default_factory=list)


@dataclass
class TapeImage:
    path: Path
    size_bytes: int
    records: list[AwsRecord]
    files: list[AwsTapeFile]
    volume: str | None
    label_type: str
    datasets: list[DatasetInfo]
    unlabeled_files: list[UnlabeledFileInfo]
    dataset_payloads: dict[str, list[bytes]]
    iebcopy_datasets: dict[str, IebcopyDataset]
    trailing_empty_file: bool


@dataclass
class CreationPlan:
    mode: str
    output_path: str
    volume: str
    dataset_name: str | None = None
    dataset_prefix: str | None = None
    input_file: str | None = None
    input_dir: str | None = None
    record_format: str = "FB"
    logical_record_length: int = 80
    block_length: int = 3200
    encoding: str = EBCDIC_CODEC
    binary: bool = False

def decode_ebcdic(raw: bytes) -> str:
    return codecs.decode(raw, EBCDIC_CODEC, errors="replace")


def decode_bytes(raw: bytes, encoding: str) -> str:
    return codecs.decode(raw, encoding, errors="replace")


def decode_recfm_byte(value: int) -> str:
    base = (value & 0xC0) >> 6
    record_format = {0: "U", 1: "V", 2: "F", 3: "U"}.get(base, "U")
    blocked = bool(value & 0x10)
    spanned = bool(value & 0x08)
    suffix = ""
    if blocked and record_format in {"F", "V"}:
        suffix += "B"
    if spanned and record_format == "V":
        suffix += "S"
    return record_format + suffix


def recfm_text_safe(record_format: str | None, key_length: int = 0) -> bool:
    return record_format in {"F", "FB", "V", "VB"} and key_length == 0


def classify_text_export(record_format: str | None, key_length: int = 0) -> tuple[str, list[str], list[str]]:
    if recfm_text_safe(record_format, key_length):
        return "supported", ["raw", "ascii", "text"], []
    if record_format in {"U", "VS", "VBS"} or key_length:
        return "unsupported", ["raw"], ["ascii"]
    return "unknown", ["raw"], ["ascii", "text"]


def encode_recfm_byte(record_format: str) -> int:
    mapping = {
        "U": 0x00,
        "F": 0x80,
        "FB": 0x90,
        "V": 0x40,
        "VB": 0x50,
        "VS": 0x48,
        "VBS": 0x58,
    }
    if record_format not in mapping:
        raise AwsToolError(f"Unsupported RECFM for writing: {record_format}")
    return mapping[record_format]


def parse_ttr(raw: bytes) -> tuple[int, int]:
    track = int.from_bytes(raw[:2], "big")
    record = raw[2]
    return track, record


def format_ttr(track: int, record: int) -> str:
    return f"{track:04X}{record:02X}"


def load_tape(path: str | Path) -> TapeImage:
    tape_path = Path(path)
    data = tape_path.read_bytes()
    records = parse_aws_records(data)
    files = split_tape_files(records)
    label_type = "standard"
    try:
        volume, datasets, payloads, iebcopy_datasets = parse_labeled_tape(tape_path, files)
        unlabeled_files: list[UnlabeledFileInfo] = []
    except UnsupportedTapeError:
        label_type = "unlabeled"
        volume = None
        datasets = []
        payloads = {}
        iebcopy_datasets = {}
        unlabeled_files = summarize_unlabeled_files(files)
    trailing_empty = bool(files and not files[-1].records)
    return TapeImage(
        path=tape_path,
        size_bytes=len(data),
        records=records,
        files=files,
        volume=volume,
        label_type=label_type,
        datasets=datasets,
        unlabeled_files=unlabeled_files,
        dataset_payloads=payloads,
        iebcopy_datasets=iebcopy_datasets,
        trailing_empty_file=trailing_empty,
    )


def parse_aws_records(data: bytes) -> list[AwsRecord]:
    records: list[AwsRecord] = []
    offset = 0
    record_index = 0
    while offset + 6 <= len(data):
        current_length = int.from_bytes(data[offset : offset + 2], "little")
        previous_length = int.from_bytes(data[offset + 2 : offset + 4], "little")
        flags1 = data[offset + 4]
        flags2 = data[offset + 5]
        payload_start = offset + 6
        payload_end = payload_start + current_length
        if payload_end > len(data):
            overrun = payload_end - len(data)
            raise AwsToolError(
                f"Invalid AWS tape image: record {record_index} at offset {offset} overruns file by {overrun} bytes"
            )
        payload = data[payload_start:payload_end]
        records.append(
            AwsRecord(
                index=record_index,
                current_length=current_length,
                previous_length=previous_length,
                flags1=flags1,
                flags2=flags2,
                payload=payload,
            )
        )
        record_index += 1
        offset = payload_end
    if offset != len(data):
        raise AwsToolError("Trailing bytes remain after parsing the AWS tape image")
    return records


def split_tape_files(records: list[AwsRecord]) -> list[AwsTapeFile]:
    files: list[AwsTapeFile] = []
    current: list[AwsRecord] = []
    file_index = 0
    for record in records:
        if record.is_tapemark:
            files.append(AwsTapeFile(index=file_index, records=current))
            current = []
            file_index += 1
            continue
        current.append(record)
    if current:
        files.append(AwsTapeFile(index=file_index, records=current))
    return files


def parse_labeled_tape(
    tape_path: Path, files: list[AwsTapeFile]
) -> tuple[str | None, list[DatasetInfo], dict[str, list[bytes]], dict[str, IebcopyDataset]]:
    volume: str | None = None
    datasets: list[DatasetInfo] = []
    dataset_payloads: dict[str, list[bytes]] = {}
    iebcopy_datasets: dict[str, IebcopyDataset] = {}

    for file_index, tape_file in enumerate(files):
        if not tape_file.records:
            continue
        decoded_labels = try_decode_labels(tape_file.records)
        if not decoded_labels:
            continue
        if any(label.kind == "VOL1" for label in decoded_labels):
            volume = next(label.raw_text[4:10].strip() for label in decoded_labels if label.kind == "VOL1")
        if not any(label.kind == "HDR1" for label in decoded_labels):
            continue

        if file_index + 1 >= len(files):
            raise UnsupportedTapeError("Header labels are not followed by a data file")

        header_set = build_label_set(decoded_labels, volume)
        data_file = files[file_index + 1]
        trailer_labels = try_decode_labels(files[file_index + 2].records) if file_index + 2 < len(files) else []
        header_set.trailer_labels = trailer_labels
        payloads = [record.payload for record in data_file.records]
        dataset_payloads[header_set.dataset_name] = payloads

        dataset_kind = "sequential"
        member_aware = False
        member_count: int | None = None
        warning: str | None = None

        iebcopy: IebcopyDataset | None = None
        if len(payloads) >= 2:
            try:
                iebcopy = parse_iebcopy_dataset(payloads, header_set)
            except AwsToolError:
                iebcopy = None
            else:
                iebcopy_datasets[header_set.dataset_name] = iebcopy
                dataset_kind = "iebcopy"
                member_aware = True
                member_count = len(iebcopy.members)
                warning = "; ".join(iebcopy.validation_notes) if iebcopy.validation_notes else None

        text_classification, extraction_modes, force_modes = classify_text_export(
            header_set.record_format
        )
        if iebcopy:
            text_classification, extraction_modes, force_modes = classify_text_export(
                iebcopy.header.record_format, iebcopy.header.key_length
            )

        datasets.append(
            DatasetInfo(
                tape_path=str(tape_path),
                dataset_index=len(datasets),
                file_index=data_file.index,
                dataset_name=header_set.dataset_name,
                volume=header_set.volume,
                label_type="standard",
                record_format=iebcopy.header.record_format if iebcopy else header_set.record_format,
                logical_record_length=iebcopy.header.logical_record_length
                if iebcopy
                else header_set.logical_record_length,
                block_length=iebcopy.header.block_size if iebcopy else header_set.block_length,
                block_attribute=header_set.block_attribute,
                carriage_control=header_set.carriage_control,
                creation_program=header_set.creation_program,
                dataset_sequence=header_set.dataset_sequence,
                data_record_count=len(payloads),
                data_size_bytes=sum(len(block) for block in payloads),
                dataset_kind=dataset_kind,
                member_aware=member_aware,
                member_count=member_count,
                text_classification=text_classification,
                extraction_modes=extraction_modes,
                force_modes=force_modes,
                warning=warning,
                validation_status="validated" if not iebcopy or not iebcopy.validation_notes else "validated-with-notes",
                validation_notes=list(iebcopy.validation_notes) if iebcopy else [],
            )
        )

    if not datasets:
        raise UnsupportedTapeError("No standard labeled datasets were found on this AWS tape image")

    return volume, datasets, dataset_payloads, iebcopy_datasets


def summarize_unlabeled_files(files: list[AwsTapeFile]) -> list[UnlabeledFileInfo]:
    summaries: list[UnlabeledFileInfo] = []
    for tape_file in files:
        if not tape_file.records:
            continue
        sizes = [record.current_length for record in tape_file.records]
        preview = decode_ebcdic(tape_file.records[0].payload[:80]).rstrip()
        summaries.append(
            UnlabeledFileInfo(
                file_index=tape_file.index + 1,
                block_count=len(tape_file.records),
                size_bytes=sum(sizes),
                min_block_size=min(sizes),
                max_block_size=max(sizes),
                avg_block_size=sum(sizes) // len(sizes),
                first_block_preview=preview,
            )
        )
    return summaries


def try_decode_labels(records: list[AwsRecord]) -> list[TapeLabel]:
    labels: list[TapeLabel] = []
    for record in records:
        if record.current_length != 80:
            return []
        text = decode_ebcdic(record.payload)
        kind = text[:4]
        if kind not in {"VOL1", "HDR1", "HDR2", "EOF1", "EOF2"}:
            return []
        labels.append(TapeLabel(kind=kind, raw_text=text))
    return labels


def build_label_set(labels: list[TapeLabel], volume: str | None) -> DatasetLabelSet:
    hdr1 = next(label.raw_text for label in labels if label.kind == "HDR1")
    hdr2 = next((label.raw_text for label in labels if label.kind == "HDR2"), "")
    dataset_name = hdr1[4:21].rstrip()
    record_format = hdr2[4].strip() or None
    block_length = parse_int_field(hdr2[5:10])
    logical_record_length = parse_int_field(hdr2[10:15])
    block_attribute = hdr2[15].strip() or None
    creation_program = hdr2[16:33].rstrip() or None
    carriage_control = hdr2[37].strip() or None if len(hdr2) > 37 else None
    label_technique = hdr2[39].strip() or None if len(hdr2) > 39 else None
    dataset_sequence = hdr1[31:35].strip() or None
    return DatasetLabelSet(
        header_labels=labels,
        trailer_labels=[],
        volume=volume,
        dataset_name=dataset_name,
        record_format=record_format,
        block_length=block_length,
        logical_record_length=logical_record_length,
        block_attribute=block_attribute,
        carriage_control=carriage_control,
        label_technique=label_technique,
        dataset_sequence=dataset_sequence,
        creation_program=creation_program,
    )


def parse_int_field(raw: str) -> int | None:
    raw = raw.strip()
    return int(raw) if raw.isdigit() else None


def parse_iebcopy_dataset(blocks: list[bytes], labels: DatasetLabelSet) -> IebcopyDataset:
    if len(blocks) < 3:
        raise AwsToolError("IEBCOPY candidate does not contain enough records")
    copy1 = blocks[0]
    copy2 = blocks[1]
    header, validation_notes = parse_iebcopy_headers(copy1, copy2)
    extents = parse_iebcopy_extents(copy2)
    record_summaries = [
        IebcopyRecordSummary("copyr1", 0, len(copy1)),
        IebcopyRecordSummary("copyr2", 1, len(copy2), {"extent_count": len(extents)}),
    ]

    member_starts: list[MemberInfo] = []
    first_data_index = 2
    for index in range(2, len(blocks)):
        members, has_eof, summary = parse_directory_record(blocks[index], index)
        record_summaries.append(summary)
        member_starts.extend(members)
        first_data_index = index + 1
        if has_eof:
            break

    if not member_starts:
        raise AwsToolError(f"IEBCOPY dataset {labels.dataset_name} has no directory entries")

    member_record_summaries, all_member_blocks = parse_member_blocks(blocks[first_data_index:], extents, first_data_index)
    record_summaries.extend(member_record_summaries)
    resolved_blocks = resolve_member_blocks_from_list(member_starts, all_member_blocks)

    text_classification, extraction_modes, force_modes = classify_text_export(
        header.record_format, header.key_length
    )
    members_by_name = {member.name: member for member in member_starts}
    for name, blocks_for_member in resolved_blocks.items():
        member = members_by_name[name]
        member.format = header.record_format
        member.size_bytes = sum(len(block.data) for block in blocks_for_member)
        member.size_records = count_records_from_blocks(
            blocks_for_member, header.record_format, header.logical_record_length
        )
        member.text_classification = text_classification
        member.extraction_modes = extraction_modes
        member.force_modes = force_modes

    return IebcopyDataset(
        header=header,
        extents=extents,
        members=sorted(member_starts, key=lambda item: (item.start_track, item.start_record, item.name)),
        all_member_blocks=all_member_blocks,
        record_summaries=record_summaries,
        validation_notes=validation_notes,
    )


def parse_iebcopy_headers(copy1: bytes, copy2: bytes) -> tuple[IebcopyHeader, list[str]]:
    notes: list[str] = []
    if len(copy1) not in {60, 64}:
        raise AwsToolError(f"IEBCOPY COPYR1 length {len(copy1)} is not supported")
    if len(copy2) != 284:
        raise AwsToolError(f"IEBCOPY COPYR2 length {len(copy2)} is not supported")
    if copy1[2:4] != b"\x00\x00":
        raise AwsToolError("IEBCOPY COPYR1 reserved bytes 2:4 are not zero")
    if copy1[8:12] != bytes.fromhex("00CA6D0F"):
        raise AwsToolError("IEBCOPY COPYR1 signature is missing")
    if copy2[2:4] != b"\x00\x00":
        raise AwsToolError("IEBCOPY COPYR2 reserved bytes 2:4 are not zero")
    if int.from_bytes(copy2[4:6], "big") != len(copy2) - 4:
        raise AwsToolError("IEBCOPY COPYR2 segment length does not match the record payload length")
    if copy2[6:8] != b"\x00\x00":
        raise AwsToolError("IEBCOPY COPYR2 segment flags are not zero")
    if copy2[280:284] != b"\x00\x00\x00\x00":
        raise AwsToolError("IEBCOPY COPYR2 trailing reserved bytes are not zero")

    header = IebcopyHeader(
        copy1_length=int.from_bytes(copy1[0:2], "big"),
        block_size=int.from_bytes(copy1[14:16], "big"),
        logical_record_length=int.from_bytes(copy1[16:18], "big"),
        record_format_byte=copy1[18],
        key_length=copy1[19],
    )
    if header.copy1_length != len(copy1):
        raise AwsToolError("IEBCOPY COPYR1 self-reported length does not match the record length")
    if header.block_size <= 0:
        raise AwsToolError("IEBCOPY COPYR1 block size is not valid")
    if header.record_format not in {"F", "FB", "V", "VB", "U"}:
        raise AwsToolError(f"IEBCOPY COPYR1 record format {header.record_format} is not supported")
    if header.record_format in {"F", "FB"} and header.logical_record_length == 0:
        notes.append("IEBCOPY COPYR1 reports fixed-block records with zero LRECL")
    if header.record_format == "U" and header.logical_record_length != 0:
        notes.append("IEBCOPY COPYR1 reports RECFM=U with a non-zero LRECL")
    return header, notes


def parse_directory_record(record: bytes, record_index: int) -> tuple[list[MemberInfo], bool, IebcopyRecordSummary]:
    members: list[MemberInfo] = []
    offset = 8
    saw_eof = False
    block_count = 0
    while offset + 12 <= len(record):
        if record[offset : offset + 12] == b"\x00" * 12:
            saw_eof = True
            break
        if offset + 276 > len(record):
            raise AwsToolError("IEBCOPY directory record does not end on a 276-byte directory block boundary")
        block = record[offset : offset + 276]
        if block[0:4] != b"\x00\x00\x00\x00":
            raise AwsToolError("IEBCOPY directory block prefix is not zero")
        if block[10:12] != bytes.fromhex("0100"):
            raise AwsToolError("IEBCOPY directory block record header does not match the supported PDS directory form")
        directory_data = block[20:276]
        used = int.from_bytes(directory_data[:2], "big")
        if used < 2 or used > 256:
            raise AwsToolError("IEBCOPY directory block used-length is outside the valid range")
        cursor = 2
        while cursor + 12 <= min(used, 256):
            name = directory_data[cursor : cursor + 8]
            if name == b"\xff" * 8:
                saw_eof = True
                break
            ttr_raw = directory_data[cursor + 8 : cursor + 11]
            c_field = directory_data[cursor + 11]
            halfwords = c_field & 0x1F
            track, record_number = parse_ttr(ttr_raw)
            members.append(
                MemberInfo(
                    name=decode_ebcdic(name).rstrip(),
                    start_ttr=format_ttr(track, record_number),
                    start_track=track,
                    start_record=record_number,
                    alias=bool(c_field & 0x80),
                    user_data_halfwords=halfwords,
                )
            )
            cursor += 12 + (halfwords * 2)
        offset += 276
        block_count += 1
    summary = IebcopyRecordSummary(
        "directory",
        record_index,
        len(record),
        {"directory_blocks": block_count, "members_found": len(members), "end_marker_seen": saw_eof},
    )
    return members, saw_eof, summary


def parse_iebcopy_extents(copy2: bytes) -> list[IebcopyExtent]:
    extents: list[IebcopyExtent] = []
    relative_track_base = 0
    for offset in range(24, 24 + 256, 16):
        raw = copy2[offset : offset + 16]
        if raw == b"\x00" * 16:
            break
        start_cylinder, start_head = decode_cchh_fields(raw[6:8], raw[8:10])
        end_cylinder, end_head = decode_cchh_fields(raw[10:12], raw[12:14])
        track_count = (raw[5] << 16) | int.from_bytes(raw[14:16], "big")
        if track_count == 0:
            continue
        extents.append(
            IebcopyExtent(
                volume_sequence=raw[0:4],
                device_modifier=raw[4],
                track_count=track_count,
                start_cylinder=start_cylinder,
                start_head=start_head,
                end_cylinder=end_cylinder,
                end_head=end_head,
                relative_track_base=relative_track_base,
            )
        )
        relative_track_base += track_count
    if not extents:
        raise AwsToolError("IEBCOPY COPYR2 record does not contain any extents")
    return extents


def decode_cchh_fields(cc_low: bytes, hh_mixed: bytes) -> tuple[int, int]:
    low_cylinder = int.from_bytes(cc_low, "big")
    high_and_head = int.from_bytes(hh_mixed, "big")
    cylinder = ((high_and_head >> 4) << 16) | low_cylinder
    head = high_and_head & 0x000F
    return cylinder, head


def parse_member_blocks(
    records: list[bytes], extents: list[IebcopyExtent], first_record_index: int
) -> tuple[list[IebcopyRecordSummary], list[IebcopyMemberBlock]]:
    blocks_in_order: list[IebcopyMemberBlock] = []
    summaries: list[IebcopyRecordSummary] = []

    for record_offset, record in enumerate(records):
        offset = 8
        block_count = 0
        while offset + 12 <= len(record):
            prefix = record[offset : offset + 4]
            cchh = record[offset + 4 : offset + 8]
            record_number = record[offset + 8]
            key_length = record[offset + 9]
            data_length = int.from_bytes(record[offset + 10 : offset + 12], "big")
            data_start = offset + 12 + key_length
            data_end = data_start + data_length
            if data_end > len(record):
                raise AwsToolError("IEBCOPY member block overruns its containing record")
            if prefix != b"\x00\x00\x00\x00":
                raise AwsToolError("Unsupported IEBCOPY member block prefix")
            if data_length == 0:
                break
            block = IebcopyMemberBlock(
                cchh=cchh,
                record_number=record_number,
                key_length=key_length,
                data=record[data_start:data_end],
            )
            block.ttr_value = resolve_member_block_ttr(cchh, record_number, extents)
            blocks_in_order.append(block)
            offset = data_end
            block_count += 1
        summaries.append(
            IebcopyRecordSummary(
                "member-data",
                first_record_index + record_offset,
                len(record),
                {"blocks": block_count},
            )
        )

    blocks_by_ttr = sorted(blocks_in_order, key=lambda item: item.ttr_value or 0)
    return summaries, blocks_by_ttr


def resolve_member_block_ttr(cchh: bytes, record_number: int, extents: list[IebcopyExtent]) -> int:
    cylinder = int.from_bytes(cchh[0:2], "big")
    head = int.from_bytes(cchh[2:4], "big")
    for extent in extents:
        if extent.contains(cylinder, head):
            relative_track = extent.to_relative_track(cylinder, head)
            return (relative_track << 8) | record_number
    raise AwsToolError(
        f"IEBCOPY member block address {cylinder:04X}{head:04X}{record_number:02X} is outside COPYR2 extents"
    )


def resolve_member_blocks_from_list(
    members: list[MemberInfo], all_blocks: list[IebcopyMemberBlock]
) -> dict[str, list[IebcopyMemberBlock]]:
    members = sorted(members, key=lambda item: (item.start_track, item.start_record))
    starts = [((member.start_track << 8) | member.start_record, member.name) for member in members]
    resolved: dict[str, list[IebcopyMemberBlock]] = {member.name: [] for member in members}

    for index, (start_ttr, name) in enumerate(starts):
        next_ttr = starts[index + 1][0] if index + 1 < len(starts) else None
        for block in all_blocks:
            if block.ttr_value is None:
                continue
            if block.ttr_value < start_ttr:
                continue
            if next_ttr is not None and block.ttr_value >= next_ttr:
                continue
            resolved[name].append(block)
    return resolved


def count_records_from_blocks(
    blocks: list[IebcopyMemberBlock], record_format: str | None, logical_record_length: int
) -> int | None:
    if record_format in {"F", "FB"} and logical_record_length:
        total_bytes = sum(len(block.data) for block in blocks)
        return total_bytes // logical_record_length
    if record_format in {"V", "VB"}:
        return len(read_variable_records_from_bytes(b"".join(block.data for block in blocks)))
    return None


def list_members(tape: TapeImage, dataset_name: str) -> list[MemberInfo]:
    dataset = tape.iebcopy_datasets.get(dataset_name)
    if not dataset:
        raise UnsupportedTapeError(f"{dataset_name} is not a supported IEBCOPY dataset")
    resolved_blocks = resolve_member_blocks_from_list(dataset.members, dataset.all_member_blocks)
    for member in dataset.members:
        member_blocks = resolved_blocks.get(member.name, [])
        member.size_bytes = sum(len(block.data) for block in member_blocks)
        member.size_records = count_records_from_blocks(
            member_blocks, dataset.header.record_format, dataset.header.logical_record_length
        )
    return dataset.members


def describe_tape(tape: TapeImage) -> dict[str, Any]:
    details = {
        "path": str(tape.path),
        "size_bytes": tape.size_bytes,
        "label_type": tape.label_type,
        "volume": tape.volume,
        "tape_file_count": len(tape.files),
        "dataset_count": len(tape.datasets),
        "unlabeled_file_count": len(tape.unlabeled_files),
        "trailing_empty_file": tape.trailing_empty_file,
        "datasets": dataclass_to_plain(tape.datasets),
    }
    if tape.unlabeled_files:
        details["files"] = dataclass_to_plain(tape.unlabeled_files)
    if tape.iebcopy_datasets:
        details["iebcopy_validation"] = {
            name: {
                "validation_notes": dataset.validation_notes,
                "record_summaries": dataclass_to_plain(dataset.record_summaries),
            }
            for name, dataset in tape.iebcopy_datasets.items()
        }
    return details


def extract_dataset(
    tape: TapeImage,
    dataset_name: str,
    *,
    file_index: int | None = None,
    member_name: str | None = None,
    mode: str = "raw",
    force: bool = False,
    encoding: str = EBCDIC_CODEC,
    user_lrecl: int | None = None,
    user_recfm: str | None = None,
    unnum: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    if file_index is not None:
        if file_index < 1 or file_index > len(tape.files):
            raise AwsToolError(f"File {file_index} was not found on the tape")
        tape_file = tape.files[file_index - 1]
        if not tape_file.records:
            raise AwsToolError(f"File {file_index} is empty")
        record_format = user_recfm
        raw_bytes = b"".join(record.payload for record in tape_file.records)
        label = f"file:{file_index}"
        if mode == "raw":
            return raw_bytes, extraction_metadata(label, mode, "unknown", force, len(raw_bytes))
        if mode == "ascii":
            text = "".join(decode_bytes(record.payload, encoding) for record in tape_file.records)
            if unnum:
                text = apply_unnum_to_lines(text.splitlines())
                text = "\n".join(text)
            output = text.encode("utf-8")
            return output, extraction_metadata(label, mode, "unknown", force, len(output))
        if mode == "text":
            if user_lrecl and user_recfm in {"F", "FB"}:
                chunks = [
                    raw_bytes[index : index + user_lrecl]
                    for index in range(0, len(raw_bytes), user_lrecl)
                    if raw_bytes[index : index + user_lrecl]
                ]
                lines = [decode_bytes(chunk, encoding).rstrip(" ") for chunk in chunks]
            else:
                lines = [decode_bytes(record.payload, encoding).rstrip(" ") for record in tape_file.records]
            if unnum:
                lines = apply_unnum_to_lines(lines)
            text = "\n".join(lines)
            output = text.encode("utf-8")
            return output, extraction_metadata(label, mode, "unknown", True if force else False, len(output))
        raise ExtractionModeError(f"Unsupported extraction mode: {mode}")

    dataset = next((item for item in tape.datasets if item.dataset_name == dataset_name), None)
    if not dataset:
        raise AwsToolError(f"Dataset {dataset_name} was not found on the tape")

    if member_name:
        if dataset_name not in tape.iebcopy_datasets:
            raise UnsupportedTapeError(f"{dataset_name} does not support member extraction")
        iebcopy = tape.iebcopy_datasets[dataset_name]
        member_blocks = resolve_member_blocks_from_list(iebcopy.members, iebcopy.all_member_blocks).get(
            member_name
        )
        if member_blocks is None:
            raise AwsToolError(f"Member {member_name} was not found in {dataset_name}")
        raw_bytes = b"".join(block.data for block in member_blocks)
        record_format = iebcopy.header.record_format
        logical_record_length = iebcopy.header.logical_record_length
        key_length = iebcopy.header.key_length
        label = f"{dataset_name}({member_name})"
    else:
        raw_bytes = b"".join(tape.dataset_payloads[dataset_name])
        record_format = dataset.record_format
        logical_record_length = dataset.logical_record_length or 0
        key_length = 0
        label = dataset_name

    classification, extraction_modes, force_modes = classify_text_export(record_format, key_length)
    if mode == "raw":
        return raw_bytes, extraction_metadata(label, mode, classification, force, len(raw_bytes))
    if mode not in {"ascii", "text"}:
        raise ExtractionModeError(f"Unsupported extraction mode: {mode}")
    if mode not in extraction_modes:
        if not force or mode not in force_modes:
            raise ExtractionModeError(
                f"{mode} extraction is not allowed for {label} without --force"
            )

    if mode == "ascii":
        ascii_bytes = decode_bytes(raw_bytes, encoding).encode("utf-8")
        return ascii_bytes, extraction_metadata(label, mode, classification, force, len(ascii_bytes))

    text_output = normalize_text_export(raw_bytes, record_format, logical_record_length, encoding=encoding, unnum=unnum)
    return text_output.encode("utf-8"), extraction_metadata(
        label, mode, classification, force, len(text_output.encode("utf-8"))
    )


def extraction_metadata(
    label: str, mode: str, classification: str, force: bool, output_size: int
) -> dict[str, Any]:
    return {
        "target": label,
        "mode": mode,
        "text_classification": classification,
        "forced": bool(force and mode != "raw"),
        "output_size_bytes": output_size,
    }


def normalize_text_export(
    raw_bytes: bytes, record_format: str | None, logical_record_length: int, *, encoding: str, unnum: bool
) -> str:
    if record_format in {"F", "FB"} and logical_record_length:
        records = [
            raw_bytes[index : index + logical_record_length]
            for index in range(0, len(raw_bytes), logical_record_length)
            if raw_bytes[index : index + logical_record_length]
        ]
    elif record_format in {"V", "VB"}:
        records = read_variable_records_from_bytes(raw_bytes)
    else:
        raise ExtractionModeError(f"Normalized text export is not supported for RECFM {record_format}")

    decoded = [decode_bytes(record, encoding).rstrip(" ") for record in records]
    if unnum:
        decoded = apply_unnum_to_lines(decoded)
    return "\n".join(decoded)


def read_variable_records_from_bytes(raw_bytes: bytes) -> list[bytes]:
    records: list[bytes] = []
    offset = 0
    while offset + 4 <= len(raw_bytes):
        length = int.from_bytes(raw_bytes[offset : offset + 2], "big")
        if length < 4 or offset + length > len(raw_bytes):
            break
        records.append(raw_bytes[offset + 4 : offset + length])
        offset += length
    return records


def apply_unnum_to_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        if len(line) >= 80 and line[71:80].strip().isdigit():
            cleaned.append(line[:71].rstrip(" "))
        else:
            cleaned.append(line)
    return cleaned


def render_dataset_table(datasets: Iterable[DatasetInfo]) -> str:
    lines = []
    for dataset in datasets:
        parts = [
            dataset.dataset_name,
            f"type={dataset.dataset_kind}",
            f"recfm={dataset.record_format or 'n/a'}",
            f"lrecl={dataset.logical_record_length or 'n/a'}",
            f"blksize={dataset.block_length or 'n/a'}",
        ]
        if dataset.member_count is not None:
            parts.append(f"members={dataset.member_count}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def render_file_table(files: Iterable[UnlabeledFileInfo]) -> str:
    lines = []
    for file_info in files:
        parts = [
            f"file={file_info.file_index}",
            f"blocks={file_info.block_count}",
            f"bytes={file_info.size_bytes}",
            f"min={file_info.min_block_size}",
            f"max={file_info.max_block_size}",
            f"avg={file_info.avg_block_size}",
        ]
        if file_info.first_block_preview:
            parts.append(f"preview={file_info.first_block_preview!r}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def render_member_table(members: Iterable[MemberInfo]) -> str:
    lines = []
    for member in members:
        parts = [
            member.name,
            f"ttr={member.start_ttr}",
            f"size={member.size_bytes or 0}",
        ]
        if member.size_records is not None:
            parts.append(f"records={member.size_records}")
        parts.append(f"recfm={member.format or 'n/a'}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def validate_creation_metadata(record_format: str, logical_record_length: int, block_length: int) -> None:
    if record_format in {"F", "FB"}:
        if logical_record_length <= 0:
            raise AwsToolError("LRECL must be positive for fixed record formats")
        if block_length < logical_record_length:
            raise AwsToolError("BLKSIZE must be at least LRECL for fixed record formats")
        if record_format == "FB" and block_length % logical_record_length != 0:
            raise AwsToolError("BLKSIZE must be a multiple of LRECL for RECFM=FB")
    elif record_format in {"V", "VB"}:
        if logical_record_length <= 0:
            raise AwsToolError("LRECL must be positive for variable record formats")
        if block_length < logical_record_length + 4:
            raise AwsToolError("BLKSIZE must allow for RDW plus data for variable record formats")
    elif record_format == "U":
        if block_length <= 0:
            raise AwsToolError("BLKSIZE must be positive for RECFM=U")
    else:
        raise AwsToolError(f"Unsupported RECFM for creation: {record_format}")


def create_tape(plan: CreationPlan) -> dict[str, Any]:
    output = Path(plan.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if plan.mode == "empty":
        output.write_bytes(b"")
        return creation_result(plan, 0, 0, [])

    if plan.mode == "sequential":
        if plan.input_file:
            dataset_name = require_dataset_name(plan.dataset_name)
            blocks = serialize_input_file(
                Path(plan.input_file),
                plan.record_format,
                plan.logical_record_length,
                plan.block_length,
                plan.encoding,
                binary=plan.binary,
            )
            payload = build_labeled_dataset(
                dataset_name,
                plan.volume,
                blocks,
                plan.record_format,
                plan.logical_record_length,
                plan.block_length,
            )
            output.write_bytes(payload)
            validate_created_tape(output, expected_dataset_count=1)
            return creation_result(plan, 1, len(payload), [dataset_name])
        if plan.input_dir:
            prefix = require_dataset_name(plan.dataset_prefix)
            entries = sorted(path for path in Path(plan.input_dir).iterdir() if path.is_file())
            datasets: list[tuple[str, list[bytes]]] = []
            for entry in entries:
                dsname = build_dataset_name(prefix, entry.stem)
                blocks = serialize_input_file(
                    entry,
                    plan.record_format,
                    plan.logical_record_length,
                    plan.block_length,
                    plan.encoding,
                    binary=plan.binary,
                )
                datasets.append((dsname, blocks))
            payload = b"".join(
                build_labeled_dataset(
                    name,
                    plan.volume,
                    blocks,
                    plan.record_format,
                    plan.logical_record_length,
                    plan.block_length,
                )
                for name, blocks in datasets
            )
            output.write_bytes(payload)
            validate_created_tape(output, expected_dataset_count=len(datasets))
            return creation_result(plan, len(datasets), len(payload), [name for name, _ in datasets])
        raise AwsToolError("Sequential creation requires either --input-file or --input-dir")

    if plan.mode == "library":
        input_dir = Path(plan.input_dir or "")
        if not input_dir.is_dir():
            raise AwsToolError("Library creation requires --input-dir")
        dataset_name = require_dataset_name(plan.dataset_name)
        members = build_library_members(
            input_dir,
            plan.record_format,
            plan.logical_record_length,
            plan.block_length,
            plan.encoding,
            binary=plan.binary,
        )
        payload_blocks = build_iebcopy_unload(
            members,
            plan.record_format,
            plan.logical_record_length,
            plan.block_length,
        )
        payload = build_labeled_dataset(
            dataset_name,
            plan.volume,
            payload_blocks,
            "V",
            max(plan.block_length - 4, 0),
            plan.block_length,
        )
        output.write_bytes(payload)
        validate_created_library_tape(output, dataset_name, len(members))
        return creation_result(plan, 1, len(payload), [dataset_name], member_count=len(members))

    raise AwsToolError(f"Unsupported creation mode: {plan.mode}")


def creation_result(
    plan: CreationPlan, dataset_count: int, output_size: int, datasets: list[str], *, member_count: int | None = None
) -> dict[str, Any]:
    result = {
        "output_path": plan.output_path,
        "mode": plan.mode,
        "volume": plan.volume,
        "dataset_count": dataset_count,
        "datasets": datasets,
        "output_size_bytes": output_size,
        "record_format": plan.record_format,
        "logical_record_length": plan.logical_record_length,
        "block_length": plan.block_length,
        "encoding": plan.encoding,
    }
    if member_count is not None:
        result["member_count"] = member_count
    return result


def require_dataset_name(name: str | None) -> str:
    if not name:
        raise AwsToolError("Dataset name or prefix is required for this creation mode")
    cleaned = name.strip().upper()
    if not cleaned or len(cleaned) > 44:
        raise AwsToolError(f"Invalid dataset name: {name}")
    return cleaned


def build_dataset_name(prefix: str, stem: str) -> str:
    token = "".join(ch for ch in stem.upper() if ch.isalnum())[:8] or "FILE"
    return require_dataset_name(f"{prefix}.{token}")


def serialize_input_file(
    path: Path,
    record_format: str,
    logical_record_length: int,
    block_length: int,
    encoding: str,
    *,
    binary: bool,
) -> list[bytes]:
    validate_creation_metadata(record_format, logical_record_length, block_length)
    raw = path.read_bytes()
    if record_format in {"F", "FB"}:
        records: list[bytes] = []
        if binary:
            for index in range(0, len(raw), logical_record_length):
                chunk = raw[index : index + logical_record_length]
                if len(chunk) < logical_record_length:
                    chunk = chunk + (b"\x00" * (logical_record_length - len(chunk)))
                records.append(chunk)
        else:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines() or [""]
            space = codecs.encode(" ", encoding)
            for line in lines:
                encoded = codecs.encode(line, encoding, errors="replace")
                if len(encoded) > logical_record_length:
                    raise AwsToolError(f"Line in {path} exceeds LRECL {logical_record_length}")
                records.append(encoded + (space * (logical_record_length - len(encoded))))
        per_block = max(1, block_length // logical_record_length)
        return [b"".join(records[index : index + per_block]) for index in range(0, len(records), per_block)]
    if record_format in {"V", "VB"}:
        blocks: list[bytes] = []
        current = bytearray()
        lines = [raw] if binary else [codecs.encode(line, encoding, errors="replace") for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
        for line in lines or [b""]:
            if len(line) > logical_record_length:
                raise AwsToolError(f"Record in {path} exceeds LRECL {logical_record_length}")
            record = (len(line) + 4).to_bytes(2, "big") + b"\x00\x00" + line
            if current and len(current) + len(record) > block_length:
                blocks.append(bytes(current))
                current = bytearray()
            current.extend(record)
        if current:
            blocks.append(bytes(current))
        return blocks
    if record_format == "U":
        return [raw[index : index + block_length] for index in range(0, len(raw), block_length)] or [b""]
    raise AwsToolError(f"Unsupported RECFM for serialization: {record_format}")


def build_labeled_dataset(
    dataset_name: str,
    volume: str,
    data_blocks: list[bytes],
    record_format: str,
    logical_record_length: int,
    block_length: int,
) -> bytes:
    tape = bytearray()
    previous_length = 0
    for record in (
        build_label_record(build_vol1_label(volume)),
        build_label_record(build_header1_label(dataset_name, volume, "HDR1")),
        build_label_record(build_header2_label(record_format, logical_record_length, block_length, "HDR2")),
    ):
        tape.extend(build_aws_record(record, previous_length))
        previous_length = len(record)
    tape.extend(build_aws_tapemark(previous_length))
    previous_length = 0
    for block in data_blocks:
        tape.extend(build_aws_record(block, previous_length))
        previous_length = len(block)
    tape.extend(build_aws_tapemark(previous_length))
    previous_length = 0
    for record in (
        build_label_record(build_header1_label(dataset_name, volume, "EOF1")),
        build_label_record(build_header2_label(record_format, logical_record_length, block_length, "EOF2")),
    ):
        tape.extend(build_aws_record(record, previous_length))
        previous_length = len(record)
    tape.extend(build_aws_tapemark(previous_length))
    tape.extend(build_aws_tapemark(0))
    return bytes(tape)


def build_aws_record(payload: bytes, previous_length: int) -> bytes:
    return (
        len(payload).to_bytes(2, "little")
        + previous_length.to_bytes(2, "little")
        + bytes([0xA0, 0x00])
        + payload
    )


def build_aws_tapemark(previous_length: int) -> bytes:
    return b"\x00\x00" + previous_length.to_bytes(2, "little") + bytes([0x40, 0x00])


def build_label_record(text: str) -> bytes:
    return codecs.encode(text.ljust(80)[:80], EBCDIC_CODEC)


def build_vol1_label(volume: str) -> str:
    return f"VOL1{volume[:6].ljust(6)}"


def build_header1_label(dataset_name: str, volume: str, kind: str) -> str:
    data = [" "] * 80
    data[0:4] = list(kind)
    data[4:21] = list(dataset_name.ljust(17)[:17])
    data[21:27] = list(volume[:6].ljust(6))
    data[27:31] = list("0001")
    data[31:35] = list("0001")
    data[54:67] = list("IBM OS/VS 370".ljust(13))
    return "".join(data)


def build_header2_label(record_format: str, logical_record_length: int, block_length: int, kind: str) -> str:
    data = [" "] * 80
    data[0:4] = list(kind)
    data[4] = record_format[0]
    data[5:10] = list(f"{block_length:05d}")
    data[10:15] = list(f"{logical_record_length:05d}")
    data[15] = "0"
    data[16:33] = list("AWSTOOL".ljust(17))
    return "".join(data)


def build_library_members(
    input_dir: Path,
    record_format: str,
    logical_record_length: int,
    block_length: int,
    encoding: str,
    *,
    binary: bool,
) -> list[tuple[str, list[bytes]]]:
    entries = sorted(path for path in input_dir.iterdir() if path.is_file())
    members: list[tuple[str, list[bytes]]] = []
    seen: set[str] = set()
    for entry in entries:
        member_name = derive_member_name(entry.stem, seen)
        blocks = serialize_input_file(entry, record_format, logical_record_length, block_length, encoding, binary=binary)
        members.append((member_name, blocks))
    if not members:
        raise AwsToolError("Library creation requires at least one input file")
    return members


def derive_member_name(stem: str, seen: set[str]) -> str:
    cleaned = "".join(ch for ch in stem.upper() if ch.isalnum())[:8] or "MEMBER"
    candidate = cleaned
    suffix = 0
    while candidate in seen:
        suffix += 1
        candidate = f"{cleaned[: 8 - len(str(suffix))]}{suffix}"
    seen.add(candidate)
    return candidate


def build_iebcopy_unload(
    members: list[tuple[str, list[bytes]]], record_format: str, logical_record_length: int, block_length: int
) -> list[bytes]:
    copy1 = build_copyr1(record_format, logical_record_length, block_length)
    member_entries: list[tuple[str, int, int, list[bytes]]] = []
    next_relative_track = 0
    for member_name, blocks in members:
        member_entries.append((member_name, next_relative_track, 1, blocks))
        next_relative_track += len(blocks)
    copy2 = build_copyr2(next_relative_track or 1)
    directory_record = build_directory_record(member_entries)
    member_data_records = build_member_data_records(member_entries)
    return [copy1, copy2, directory_record, *member_data_records]


def build_copyr1(record_format: str, logical_record_length: int, block_length: int) -> bytes:
    copy1 = bytearray(64)
    copy1[0:2] = (64).to_bytes(2, "big")
    copy1[8:12] = bytes.fromhex("00CA6D0F")
    copy1[12:14] = (2).to_bytes(2, "big")
    copy1[14:16] = block_length.to_bytes(2, "big")
    copy1[16:18] = logical_record_length.to_bytes(2, "big")
    copy1[18] = encode_recfm_byte(record_format)
    return bytes(copy1)


def build_copyr2(track_count: int) -> bytes:
    copy2 = bytearray(284)
    copy2[0:2] = (284).to_bytes(2, "big")
    copy2[4:6] = (280).to_bytes(2, "big")
    start_cylinder, start_head = relative_track_to_cchh(0)
    end_cylinder, end_head = relative_track_to_cchh(max(track_count - 1, 0))
    extent = bytearray(16)
    extent[14:16] = track_count.to_bytes(2, "big")
    encode_cchh_fields(extent, 6, 8, start_cylinder, start_head)
    encode_cchh_fields(extent, 10, 12, end_cylinder, end_head)
    copy2[24:40] = extent
    return bytes(copy2)


def relative_track_to_cchh(track: int) -> tuple[int, int]:
    return divmod(track, 15)


def encode_cchh_fields(target: bytearray, cc_offset: int, hh_offset: int, cylinder: int, head: int) -> None:
    target[cc_offset : cc_offset + 2] = (cylinder & 0xFFFF).to_bytes(2, "big")
    target[hh_offset : hh_offset + 2] = (((cylinder >> 16) << 4) | (head & 0x0F)).to_bytes(2, "big")


def build_directory_record(member_entries: list[tuple[str, int, int, list[bytes]]]) -> bytes:
    blocks: list[bytes] = []
    current_entries = bytearray(b"\x00\x00")
    for name, track, record, _blocks in member_entries:
        entry = codecs.encode(name.ljust(8)[:8], EBCDIC_CODEC) + track.to_bytes(2, "big") + bytes([record & 0xFF, 0x00])
        end_marker = b"\xff" * 8 + b"\x00\x00\x00\x00"
        if len(current_entries) + len(entry) + len(end_marker) > 256:
            blocks.append(build_directory_block(bytes(current_entries)))
            current_entries = bytearray(b"\x00\x00")
        current_entries.extend(entry)
    current_entries.extend(b"\xff" * 8 + b"\x00\x00\x00\x00")
    blocks.append(build_directory_block(bytes(current_entries)))
    return b"\x00" * 8 + b"".join(blocks) + (b"\x00" * 12)


def build_directory_block(directory_data: bytes) -> bytes:
    used = min(len(directory_data), 256)
    block = bytearray(276)
    block[10:12] = bytes.fromhex("0100")
    block[20:20 + used] = directory_data[:used]
    block[20:22] = used.to_bytes(2, "big")
    return bytes(block)


def build_member_data_records(member_entries: list[tuple[str, int, int, list[bytes]]]) -> list[bytes]:
    records: list[bytes] = []
    for _name, start_track, _start_record, blocks in member_entries:
        for index, block in enumerate(blocks):
            cylinder, head = relative_track_to_cchh(start_track + index)
            cchh = cylinder.to_bytes(2, "big") + head.to_bytes(2, "big")
            record = bytearray(b"\x00" * 8)
            record.extend(b"\x00" * 4)
            record.extend(cchh)
            record.append(1)
            record.append(0)
            record.extend(len(block).to_bytes(2, "big"))
            record.extend(block)
            record.extend(b"\x00" * 12)
            records.append(bytes(record))
    return records


def validate_created_tape(path: Path, *, expected_dataset_count: int) -> None:
    tape = load_tape(path)
    if tape.label_type != "standard":
        raise AwsToolError("Created tape did not parse back as a standard labeled tape")
    if len(tape.datasets) != expected_dataset_count:
        raise AwsToolError("Created tape did not round-trip with the expected dataset count")


def validate_created_library_tape(path: Path, dataset_name: str, expected_member_count: int) -> None:
    tape = load_tape(path)
    members = list_members(tape, dataset_name)
    if len(members) != expected_member_count:
        raise AwsToolError("Created IEBCOPY tape did not round-trip with the expected member count")
