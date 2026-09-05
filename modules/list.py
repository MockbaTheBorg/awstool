"""Dataset listing command module."""

from __future__ import annotations

from cli_contract import ModuleSpec, OptionSpec, command_result

from awslib import load_tape, render_dataset_table, render_file_table


SPEC = ModuleSpec(
    name="list",
    action_flags=("-l", "--list"),
    help="List datasets discovered from standard tape labels",
    options=(
        OptionSpec(flags=("--tape",), dest="tape", metavar="PATH", required=True, help="AWS tape image"),
    ),
    usage_examples=(
        "python awstool.py --list --tape tapes/0560_jcltap.aws",
        "python awstool.py --list --tape tapes/CBT509.563",
    ),
)


def run(args):
    tape = load_tape(args.tape)
    if tape.label_type == "unlabeled":
        return command_result("list", args.tape, {"kind": "files", "items": tape.unlabeled_files})
    return command_result("list", args.tape, {"kind": "datasets", "items": tape.datasets})


def render_text(result) -> str:
    if result.data["kind"] == "files":
        return render_file_table(result.data["items"])
    return render_dataset_table(result.data["items"])
