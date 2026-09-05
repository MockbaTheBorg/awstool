"""Dataset and member extraction command module."""

from __future__ import annotations

import sys
from pathlib import Path

from cli_contract import ModuleSpec, OptionSpec, command_result

from awslib import (
    AwsToolError,
    extract_dataset,
    list_members,
    load_tape,
)


SPEC = ModuleSpec(
    name="extract",
    action_flags=("-e", "--extract"),
    help="Extract a dataset or IEBCOPY member in raw, ASCII, or text form",
    options=(
        OptionSpec(flags=("--tape",), dest="tape", metavar="PATH", required=True, help="AWS tape image"),
        OptionSpec(flags=("--dataset",), dest="dataset", metavar="DSNAME", help="Dataset name"),
        OptionSpec(flags=("--file",), dest="file_index", metavar="N", help="Unlabeled tape file number"),
        OptionSpec(flags=("--member",), dest="member", metavar="NAME", help="Member name"),
        OptionSpec(
            flags=("--mode",),
            dest="mode",
            choices=("raw", "ascii", "text"),
            default="raw",
            help="Extraction mode",
        ),
        OptionSpec(flags=("-o", "--output"), dest="output", metavar="PATH", help="Write extracted output to PATH"),
        OptionSpec(
            flags=("--output-dir",),
            dest="output_dir",
            metavar="PATH",
            help="Write bulk member extraction outputs to PATH",
        ),
        OptionSpec(flags=("-f", "--force"), dest="force", action="store_true", help="Force exploratory conversion"),
        OptionSpec(
            flags=("--all-members",),
            dest="all_members",
            action="store_true",
            help="Extract all members of an IEBCOPY dataset",
        ),
        OptionSpec(flags=("--encoding",), dest="encoding", default="cp037", help="EBCDIC code page for conversion"),
        OptionSpec(flags=("--lrecl",), dest="lrecl", metavar="N", help="Override LRECL for unlabeled file text extraction"),
        OptionSpec(
            flags=("--recfm",),
            dest="recfm",
            choices=("F", "FB", "V", "VB", "U"),
            help="Override RECFM for unlabeled file text extraction",
        ),
        OptionSpec(flags=("--unnum",), dest="unnum", action="store_true", help="Strip sequence numbers in columns 72-80"),
    ),
    usage_examples=(
        "python awstool.py --extract --tape tapes/0560_jcltap.aws --dataset BIS --member HELLO --mode text",
        "python awstool.py --extract --tape tapes/0560_jcltap.aws --dataset BIS --all-members --output-dir out --mode text",
        "python awstool.py --extract --tape tapes/herccmd.aws --dataset GRZES.HERCCMD --member HERCCMD --mode ascii --force",
        "python awstool.py --extract --tape tapes/CBT509.563 --file 1 --mode raw",
        "python awstool.py --extract --tape tapes/CBT509.563 --file 1 --mode text --encoding cp037 --recfm FB --lrecl 80",
        "python awstool.py --extract --tape tapes/CBT509.563 --file 1 --mode text --encoding cp037 --recfm FB --lrecl 80 --unnum",
    ),
)


def member_output_path(output_dir: Path, member_name: str) -> Path:
    safe_name = member_name.strip().replace("/", "_")
    if not safe_name:
        raise AwsToolError("Encountered a member with an empty name")
    return output_dir / safe_name


def run(args):
    tape = load_tape(args.tape)
    if bool(args.dataset) == bool(args.file_index):
        raise AwsToolError("Specify exactly one of --dataset or --file", show_usage=True, exit_code=2)
    if args.all_members:
        if not args.dataset:
            raise AwsToolError("--all-members requires --dataset", show_usage=True, exit_code=2)
        if args.file_index:
            raise AwsToolError("--all-members cannot be used with --file", show_usage=True, exit_code=2)
        if args.member:
            raise AwsToolError("--all-members cannot be used with --member", show_usage=True, exit_code=2)
        if args.output:
            raise AwsToolError("--all-members cannot be used with --output; use --output-dir", show_usage=True, exit_code=2)
        if not args.output_dir:
            raise AwsToolError("--all-members requires --output-dir", show_usage=True, exit_code=2)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for member in list_members(tape, args.dataset):
            payload, metadata = extract_dataset(
                tape,
                args.dataset,
                member_name=member.name,
                mode=args.mode,
                force=bool(args.force),
                encoding=args.encoding,
                user_lrecl=int(args.lrecl) if args.lrecl else None,
                user_recfm=args.recfm,
                unnum=bool(args.unnum),
            )
            output_path = member_output_path(output_dir, member.name)
            output_path.write_bytes(payload)
            written.append(
                {
                    "member": member.name,
                    "output_path": str(output_path),
                    "output_size_bytes": len(payload),
                    "text_classification": metadata["text_classification"],
                    "forced": metadata["forced"],
                }
            )
        return command_result(
            "extract",
            args.tape,
            {
                "target": args.dataset,
                "bulk_members": True,
                "member_count": len(written),
                "mode": args.mode,
                "output_dir": str(output_dir),
                "written_members": written,
            },
        )

    if args.output and args.output_dir:
        raise AwsToolError("Specify only one of --output or --output-dir", show_usage=True, exit_code=2)
    payload, metadata = extract_dataset(
        tape,
        args.dataset or "",
        file_index=int(args.file_index) if args.file_index else None,
        member_name=args.member,
        mode=args.mode,
        force=bool(args.force),
        encoding=args.encoding,
        user_lrecl=int(args.lrecl) if args.lrecl else None,
        user_recfm=args.recfm,
        unnum=bool(args.unnum),
    )
    if args.output:
        Path(args.output).write_bytes(payload)
        metadata["output_path"] = args.output
    elif getattr(args, "json_output", False):
        metadata["stdout_suppressed"] = True
    else:
        stream = sys.stdout.buffer
        stream.write(payload)
        if args.mode != "raw" and not payload.endswith(b"\n"):
            stream.write(b"\n")
    return command_result("extract", args.tape, metadata)


def render_text(result) -> str:
    data = result.data
    if data.get("bulk_members"):
        lines = [
            f"target: {data['target']}",
            f"mode: {data['mode']}",
            f"member_count: {data['member_count']}",
            f"output_dir: {data['output_dir']}",
        ]
        for item in data["written_members"]:
            lines.append(f"{item['member']} -> {item['output_path']} ({item['output_size_bytes']} bytes)")
        return "\n".join(lines)
    if "output_path" not in data:
        return ""
    lines = [
        f"target: {data['target']}",
        f"mode: {data['mode']}",
        f"text_classification: {data['text_classification']}",
        f"forced: {data['forced']}",
        f"output_size_bytes: {data['output_size_bytes']}",
    ]
    if "output_path" in data:
        lines.append(f"output_path: {data['output_path']}")
    return "\n".join(lines)
