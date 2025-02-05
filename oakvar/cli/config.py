from . import cli_entry
from . import cli_func


@cli_entry
def cli_config_user(args):
    return user(args)


@cli_func
def user(args, __name__="config user"):
    import oyaml as yaml
    from ..api.config import user

    conf = user()
    conf = yaml.dump(conf, default_flow_style=False)
    outer = args.get("outer", None)
    if outer:
        outer.write(conf)


@cli_entry
def cli_config_system(args):
    return system(args)


@cli_func
def system(args, __name__="config system"):
    from rich.console import Console
    from rich.table import Table
    from rich.box import SQUARE
    from ..api.config import system

    key = args.get("key")
    value = args.get("value")
    ty = args.get("type")
    ret = system(key=key, value=value, type=ty)
    if isinstance(ret, str):
        print(ret)
    elif isinstance(ret, dict):
        console = Console()
        table = Table(title=ret.get("sys_conf_path"), box=SQUARE)
        table.add_column("Key")
        table.add_column("Value")
        for k, v in ret.items():
            table.add_row(k, str(v))
        console.print(table)


def get_parser_fn_config():
    from argparse import ArgumentParser, RawDescriptionHelpFormatter

    parser_fn_config = ArgumentParser(formatter_class=RawDescriptionHelpFormatter)
    subparsers = parser_fn_config.add_subparsers(title="Commands")
    add_parser_ov_config_user(subparsers)
    add_parser_ov_config_system(subparsers)
    return parser_fn_config


def add_parser_ov_config_user(subparsers):
    # shows oakvar conf content.
    parser_cli_config_oakvar = subparsers.add_parser(
        "user",
        epilog="A dictionary. content of OakVar user configuration file",
        help="shows oakvar user configuration",
    )
    parser_cli_config_oakvar.set_defaults(func=cli_config_user)
    parser_cli_config_oakvar.r_return = "A named list. OakVar user config information"  # type: ignore
    parser_cli_config_oakvar.r_examples = [  # type: ignore
        "# Get the named list of the OakVar user configuration",
        "#roakvar::config.user()",
    ]


def add_parser_ov_config_system(subparsers):
    # shows oakvar conf content.
    parser_cli_config_oakvar = subparsers.add_parser(
        "system",
        epilog="A dictionary. content of OakVar system configuration file",
        help="shows oakvar system configuration",
    )
    parser_cli_config_oakvar.add_argument("key", nargs="?", help="Configuration key")
    parser_cli_config_oakvar.add_argument(
        "value", nargs="?", help="Configuration value"
    )
    parser_cli_config_oakvar.add_argument(
        "type", nargs="?", help="Type of configuration value"
    )
    parser_cli_config_oakvar.add_argument(
        "--fmt", default="json", help="Format of output: table / json"
    )
    parser_cli_config_oakvar.set_defaults(func=cli_config_system)
    parser_cli_config_oakvar.r_return = "A named list. OakVar system config information"  # type: ignore
    parser_cli_config_oakvar.r_examples = [  # type: ignore
        "# Get the named list of the OakVar system configuration",
        "#roakvar::config.system()",
    ]
