"""Tape creation command module."""

from __future__ import annotations

from cli_contract import ModuleSpec, OptionSpec, command_result

from awslib import AwsToolError, CreationPlan, create_tape


SPEC = ModuleSpec(
    name="new",
    action_flags=("-n", "--new"),
    help="Create a new AWS tape image",
    options=(
        OptionSpec(flags=("--output-tape",), dest="new_output", metavar="PATH", required=True, help="Output AWS tape path"),
        OptionSpec(
            flags=("--new-mode",),
            dest="new_mode",
            choices=("empty", "sequential", "library"),
            default="empty",
            help="Creation mode",
        ),
        OptionSpec(flags=("--input-file",), dest="new_input_file", metavar="PATH", help="Host file input"),
        OptionSpec(flags=("--input-dir",), dest="new_input_dir", metavar="PATH", help="Host directory input"),
        OptionSpec(flags=("--new-dataset",), dest="new_dataset", metavar="DSNAME", help="Dataset name"),
        OptionSpec(flags=("--new-dataset-prefix",), dest="new_dataset_prefix", metavar="PREFIX", help="Dataset prefix for folder-to-sequential mode"),
        OptionSpec(flags=("--new-volume",), dest="new_volume", default="VOL001", help="Volume serial"),
        OptionSpec(
            flags=("--write-recfm",),
            dest="new_recfm",
            choices=("F", "FB", "V", "VB", "U"),
            default="FB",
            help="Record format",
        ),
        OptionSpec(flags=("--write-lrecl",), dest="new_lrecl", default="80", metavar="N", help="Logical record length"),
        OptionSpec(flags=("--write-blksize",), dest="new_blksize", default="3200", metavar="N", help="Block size"),
        OptionSpec(flags=("--write-encoding",), dest="new_encoding", default="cp037", help="EBCDIC code page"),
        OptionSpec(flags=("--write-binary",), dest="new_binary", action="store_true", help="Treat host input as binary"),
    ),
    usage_examples=(
        "python awstool.py --new --output-tape out.aws --new-mode empty",
        "python awstool.py --new --output-tape out.aws --new-mode sequential --input-file input.txt --new-dataset USER.TEST --write-recfm FB --write-lrecl 80 --write-blksize 3200",
        "python awstool.py --new --output-tape out.aws --new-mode sequential --input-dir folder --new-dataset-prefix USER.TEST",
        "python awstool.py --new --output-tape out.aws --new-mode library --input-dir pdsdir --new-dataset USER.PROCLIB --write-recfm FB --write-lrecl 80 --write-blksize 3200",
    ),
)


def run(args):
    plan = CreationPlan(
        mode=args.new_mode,
        output_path=args.new_output,
        volume=args.new_volume.upper()[:6],
        dataset_name=args.new_dataset,
        dataset_prefix=args.new_dataset_prefix,
        input_file=args.new_input_file,
        input_dir=args.new_input_dir,
        record_format=args.new_recfm,
        logical_record_length=int(args.new_lrecl),
        block_length=int(args.new_blksize),
        encoding=args.new_encoding,
        binary=bool(args.new_binary),
    )

    if args.new_mode == "empty" and (args.new_input_file or args.new_input_dir or args.new_dataset or args.new_dataset_prefix):
        raise AwsToolError(
            "Empty mode does not accept input files, folders, or dataset naming options",
            show_usage=True,
            exit_code=2,
        )
    if args.new_mode == "sequential" and bool(args.new_input_file) == bool(args.new_input_dir):
        raise AwsToolError("Sequential mode requires exactly one of --input-file or --input-dir", show_usage=True, exit_code=2)
    if args.new_mode == "library" and (not args.new_input_dir or args.new_input_file):
        raise AwsToolError("Library mode requires --input-dir and does not accept --input-file", show_usage=True, exit_code=2)

    data = create_tape(plan)
    return command_result("new", args.new_output, data)


def render_text(result) -> str:
    data = result.data
    lines = [
        f"output_path: {data['output_path']}",
        f"mode: {data['mode']}",
        f"volume: {data['volume']}",
        f"dataset_count: {data['dataset_count']}",
        f"output_size_bytes: {data['output_size_bytes']}",
    ]
    if data.get("datasets"):
        lines.append(f"datasets: {', '.join(data['datasets'])}")
    if data.get("member_count") is not None:
        lines.append(f"member_count: {data['member_count']}")
    return "\n".join(lines)
