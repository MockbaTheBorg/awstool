"""Command module contract for the generic CLI host.

Each module in this package must expose:

- `SPEC`: a `cli_contract.ModuleSpec` instance describing the action flag and options
- `run(args)`: the module entrypoint returning `cli_contract.ResultEnvelope`
- `render_text(result)`: human-readable renderer for non-JSON output

`awstool.py` loads these modules dynamically and validates their command metadata
before building the final CLI parser. The host itself is project-agnostic; domain
logic lives in the modules and their supporting libraries.
"""
