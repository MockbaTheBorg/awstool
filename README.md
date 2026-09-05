# awstool

Pure-Python CLI to inspect, extract from, and create Hercules **AWS tape image** files
(`.aws`) — the virtual tape format used by the Hercules IBM mainframe emulator. Reads
standard IBM tape labels (VOL1/HDR1/HDR2), IEBCOPY PDS unloads (member directories +
data), and unlabeled tape files; can also build new AWS tapes from host files.

No dependencies beyond the Python 3.10+ standard library.

## Layout

```
awstool.py       # CLI entry point — discovers command modules, builds argparse, dispatches
awstool          # shell wrapper: python awstool.py "$@"
cli_contract.py  # shared contract: ModuleSpec/OptionSpec/ResultEnvelope, CommandError
awslib.py        # core library: AWS record/tape parsing, IEBCOPY parsing, extraction, tape creation
modules/         # one file per CLI command (info, list, members, extract, new)
tapes/           # sample .aws tape images for manual testing
data/            # sample JCL members extracted from tapes/herccmd.aws
```

`awstool.py` is a generic plug-in host: it discovers any module in `modules/` exposing
`SPEC` (a `ModuleSpec`), `run(args)`, and `render_text(result)`, and wires it into a
single argparse-based CLI with mutually-exclusive top-level actions (`-i`, `-l`, `-m`,
`-e`, `-n`). Adding a new command means dropping a new module in `modules/` — the host
and `awslib.py` need no changes.

## Usage

```bash
./awstool <action> [options]
# or
python3 awstool.py <action> [options]
```

Global flags: `-j/--json` (structured output), `-v/--verbose`, `-d/--debug`.

### Info — tape summary

```bash
python3 awstool.py --info --tape tapes/herccmd.aws
```

### List — datasets or unlabeled files on a tape

```bash
python3 awstool.py --list --tape tapes/0560_jcltap.aws
```

### Members — list members of an IEBCOPY (PDS unload) dataset

```bash
python3 awstool.py --members --tape tapes/herccmd.aws --dataset GRZES.HERCCMD
```

### Extract — pull a dataset, unlabeled file, or PDS member off a tape

```bash
# single member, converted to text (EBCDIC -> ASCII, RECFM-aware line splitting)
python3 awstool.py --extract --tape tapes/0560_jcltap.aws --dataset BIS --member HELLO --mode text

# every member of a PDS, dumped to a directory
python3 awstool.py --extract --tape tapes/0560_jcltap.aws --dataset BIS --all-members --output-dir out --mode text

# unlabeled tape file by index, forcing conversion with explicit RECFM/LRECL
python3 awstool.py --extract --tape tapes/CBT509.563 --file 1 --mode text --encoding cp037 --recfm FB --lrecl 80 --unnum
```

Extraction modes:
- `raw` — bytes as stored on tape, no conversion
- `ascii` — EBCDIC-to-ASCII byte conversion only
- `text` — EBCDIC-to-ASCII plus RECFM-aware record/line reconstruction (`--unnum` strips
  sequence numbers in columns 72-80)

`ascii`/`text` are refused for RECFM `U`/keyed/unknown datasets unless `-f/--force` is
given. `--encoding` selects the EBCDIC code page (default `cp037`); `--recfm`/`--lrecl`
override label values for unlabeled tape files, where no label supplies them.

### New — build an AWS tape image

```bash
# empty tape
python3 awstool.py --new --output-tape out.aws --new-mode empty

# one sequential dataset from a host file
python3 awstool.py --new --output-tape out.aws --new-mode sequential \
  --input-file input.txt --new-dataset USER.TEST --write-recfm FB --write-lrecl 80 --write-blksize 3200

# a PDS (IEBCOPY unload) from a directory of host files, one member per file
python3 awstool.py --new --output-tape out.aws --new-mode library \
  --input-dir pdsdir --new-dataset USER.PROCLIB --write-recfm FB --write-lrecl 80 --write-blksize 3200
```

Creation modes: `empty`, `sequential` (single dataset from `--input-file`, or one
dataset per file under `--input-dir` when `--new-dataset-prefix` is used instead of
`--new-dataset`), `library` (IEBCOPY-format PDS built from `--input-dir`, one member
per file). `--write-binary` skips ASCII-to-EBCDIC text conversion of host input.

## Output

Every command returns a `ResultEnvelope` (`command`, `source`, `status`, `data`,
`warnings`, `errors`). Default rendering is a human-readable text summary per module;
pass `-j/--json` for the full structured envelope.

## Scope / limitations

- Reads labeled tapes with standard VOL1/HDR1/HDR2/EOF1 IBM labels, and unlabeled tape
  files (raw block dumps).
- IEBCOPY member parsing supports unblocked/blocked RECFM F/FB/V/VB; RECFM `U`, keyed
  datasets, and spanned records are extraction-only (`raw`, or `ascii`/`text` with
  `--force`, with no guaranteed correctness).
- No dataset compression/interpretation beyond IEBCOPY unload format — this is not a
  general MVS dataset access method.
