"""Generic dynamic CLI host for plug-in command modules."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import sys

from cli_contract import CommandError, ModuleSpec, OptionSpec, ResultEnvelope, dataclass_to_plain


MODULE_PACKAGE = os.environ.get("CLI_MODULE_PACKAGE", "modules")
HOST_DESCRIPTION = os.environ.get("CLI_HOST_DESCRIPTION", "Modular command host")


def discover_modules() -> dict[str, object]:
    package = importlib.import_module(MODULE_PACKAGE)
    discovered: dict[str, object] = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{MODULE_PACKAGE}.{module_info.name}")
        spec = getattr(module, "SPEC", None)
        run = getattr(module, "run", None)
        render_text = getattr(module, "render_text", None)
        if not isinstance(spec, ModuleSpec) or not callable(run) or not callable(render_text):
            raise CommandError(f"Invalid command module: {module_info.name}")
        discovered[spec.name] = module
    if not discovered:
        raise CommandError("No command modules were found in modules/")
    return discovered


def build_parser(modules: dict[str, object]) -> tuple[argparse.ArgumentParser, dict[str, list[OptionSpec]]]:
    parser = argparse.ArgumentParser(
        description=HOST_DESCRIPTION,
        epilog=build_global_help(modules),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-j", "--json", dest="json_output", action="store_true", help="Return JSON output")
    parser.add_argument("-v", "--verbose", dest="verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-d", "--debug", dest="debug", action="store_true", help="Enable debug output (implies --verbose)")
    action_group = parser.add_mutually_exclusive_group(required=True)

    option_registry: dict[tuple[str, ...], dict[str, object]] = {}
    option_owners: dict[str, list[OptionSpec]] = {}

    for module in modules.values():
        spec: ModuleSpec = module.SPEC
        action_group.add_argument(
            *spec.action_flags,
            dest="selected_module",
            action="store_const",
            const=spec.name,
            help=spec.help,
        )
        option_owners[spec.name] = list(spec.options)
        for option in spec.options:
            existing = option_registry.get(option.flags)
            current = {
                "dest": option.dest,
                "action": option.action,
                "choices": option.choices,
                "default": option.default,
                "metavar": option.metavar,
                "help": option.help,
            }
            if existing and existing != current:
                raise CommandError(f"Conflicting option specification for {'/'.join(option.flags)}")
            if not existing:
                parser.add_argument(*option.flags, **option.argparse_kwargs())
                option_registry[option.flags] = current

    return parser, option_owners


def validate_required_options(args: argparse.Namespace, spec: ModuleSpec) -> None:
    missing = []
    for option in spec.options:
        if not option.required:
            continue
        dest = option.dest or option.flags[-1].lstrip("-").replace("-", "_")
        if getattr(args, dest, None) in (None, False):
            missing.append("/".join(option.flags))
    if missing:
        raise CommandError(f"Missing required options for {spec.name}: {', '.join(missing)}", show_usage=True, exit_code=2)


def build_global_help(modules: dict[str, object]) -> str:
    lines = ["Commands:"]
    for module in sorted(modules.values(), key=lambda item: item.SPEC.name):
        spec: ModuleSpec = module.SPEC
        lines.append(f"  {spec.action_flags[0]}, {spec.action_flags[1]}  {spec.help}")
        for example in spec.usage_examples[:2]:
            lines.append(f"    example: {example}")
    return "\n".join(lines)


def build_module_help(spec: ModuleSpec) -> str:
    lines = [
        f"Command: {spec.name} ({spec.action_flags[0]}, {spec.action_flags[1]})",
        spec.help,
        "",
        "Required options:",
    ]
    required = [option for option in spec.options if option.required]
    optional = [option for option in spec.options if not option.required]
    for option in required:
        lines.append(f"  {'/'.join(option.flags)}  {option.help}")
    if optional:
        lines.append("")
        lines.append("Optional options:")
        for option in optional:
            lines.append(f"  {'/'.join(option.flags)}  {option.help}")
    if spec.usage_examples:
        lines.append("")
        lines.append("Examples:")
        for example in spec.usage_examples:
            lines.append(f"  {example}")
    return "\n".join(lines)


def print_result(result: ResultEnvelope, json_output: bool, render_text) -> int:
    if json_output:
        print(json.dumps(dataclass_to_plain(result), indent=2, sort_keys=True))
        return 0
    if result.data is not None:
        text = render_text(result)
        if text:
            print(text)
    elif result.status != "ok":
        print("\n".join(result.errors), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = None
    module = None
    render_text = lambda r: ""  # noqa: E731  — fallback before module is known
    try:
        modules = discover_modules()
        parser, _ = build_parser(modules)
        args = parser.parse_args(argv)
        module = modules[args.selected_module]
        render_text = module.render_text
        spec: ModuleSpec = module.SPEC
        validate_required_options(args, spec)
        result: ResultEnvelope = module.run(args)
    except CommandError as exc:
        if "spec" in locals() and exc.show_usage:
            print(f"error: {exc}", file=sys.stderr)
            print("", file=sys.stderr)
            print(build_module_help(spec), file=sys.stderr)
            return exc.exit_code
        result = ResultEnvelope(
            command=spec.name if "spec" in locals() else "host",
            source=None,
            status="error",
            data=None,
            warnings=[],
            errors=[str(exc)],
        )
        if args is None:
            print("\n".join(result.errors), file=sys.stderr)
            return 1
    return print_result(result, bool(args.json_output), render_text)


if __name__ == "__main__":
    raise SystemExit(main())
