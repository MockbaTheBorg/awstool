"""IEBCOPY member listing command module."""

from __future__ import annotations

from cli_contract import ModuleSpec, OptionSpec, command_result

from awslib import list_members, load_tape, render_member_table


SPEC = ModuleSpec(
    name="members",
    action_flags=("-m", "--members"),
    help="List members of a supported IEBCOPY dataset",
    options=(
        OptionSpec(flags=("--tape",), dest="tape", metavar="PATH", required=True, help="AWS tape image"),
        OptionSpec(flags=("--dataset",), dest="dataset", metavar="DSNAME", required=True, help="Dataset name"),
    ),
    usage_examples=(
        "python awstool.py --members --tape tapes/0560_jcltap.aws --dataset BIS",
        "python awstool.py --members --tape tapes/herccmd.aws --dataset GRZES.HERCCMD",
    ),
)


def run(args):
    tape = load_tape(args.tape)
    return command_result("members", args.tape, list_members(tape, args.dataset))


def render_text(result) -> str:
    return render_member_table(result.data)
