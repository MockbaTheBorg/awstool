"""Tape information command module."""

from __future__ import annotations

from cli_contract import ModuleSpec, OptionSpec, command_result

from awslib import describe_tape, load_tape


SPEC = ModuleSpec(
    name="info",
    action_flags=("-i", "--info"),
    help="Show tape-level information and dataset summary",
    options=(
        OptionSpec(flags=("--tape",), dest="tape", metavar="PATH", required=True, help="AWS tape image"),
    ),
    usage_examples=(
        "python awstool.py --info --tape tapes/herccmd.aws",
        "python awstool.py --info --tape tapes/CBT509.563",
    ),
)


def run(args):
    tape = load_tape(args.tape)
    return command_result("info", args.tape, describe_tape(tape))


def render_text(result) -> str:
    data = result.data
    lines = [
        f"path: {data['path']}",
        f"size_bytes: {data['size_bytes']}",
        f"label_type: {data['label_type']}",
        f"volume: {data['volume'] or '-'}",
        f"tape_file_count: {data['tape_file_count']}",
        f"dataset_count: {data['dataset_count']}",
    ]
    if data.get("unlabeled_file_count"):
        lines.append(f"unlabeled_file_count: {data['unlabeled_file_count']}")
    return "\n".join(lines)
