import argparse
from . import cmd_admin, cmd_util
from .cmd_run import parser_fn_run
from .cmd_test import parser_fn_util_test
from .cmd_gui import parser_fn_gui
from .cmd_report import parser_fn_report
import sys


#class ArgumentParser(argparse.ArgumentParser):
#    def error(self, message):
#        self.print_help(sys.stderr)

# subparsers
p_entry = argparse.ArgumentParser(description="OakVar. Genomic variant analysis platform. https://github.com/rkimoakbioinformatics/oakvar")
sp_entry = p_entry.add_subparsers(title="Commands")

# run
p_run = sp_entry.add_parser("run", parents=[parser_fn_run], add_help=False, description="Run a job", help="Run a job", epilog="inputs should be the first argument")

# report
p_report = sp_entry.add_parser("report", parents=[parser_fn_report], add_help=False, help="Generate a report from a job", epilog="dbpath must be the first argument")

# gui
p_gui = sp_entry.add_parser("gui", parents=[parser_fn_gui], add_help=False, help="Start the GUI")

# module
p_module = sp_entry.add_parser("module", description="Change installed modules", help="Change installed modules")
sp_module = p_module.add_subparsers(title="Commands")
p_module_ls = sp_module.add_parser("ls", parents=[cmd_admin.parser_fn_module_ls], add_help=False, help="List modules")
p_module_install = sp_module.add_parser("install", description="Install modules", parents=[cmd_admin.parser_module_install], add_help=False, help="Install modules")
p_module_uninstall = sp_module.add_parser("uninstall", parents=[cmd_admin.parser_uninstall], add_help=False, help="Uninstall modules")
p_module_update = sp_module.add_parser("update", parents=[cmd_admin.parser_update], add_help=False, help="Update modules")
p_module_info = sp_module.add_parser("info", parents=[cmd_admin.parser_info], add_help=False, help="Module details")
p_module_installbase = sp_module.add_parser("installbase", parents=[cmd_admin.parser_module_installbase], add_help=False, help="Install base modules")

# config
p_config = sp_entry.add_parser("config", description="View and change configuration settings", help="View and change configuration settings")
sp_config = p_config.add_subparsers(title="Commands")
p_config_md = sp_config.add_parser("md", parents=[cmd_admin.parser_md], add_help=False, help="Change modules directory")
p_config_system = sp_config.add_parser("system", parents=[cmd_admin.parser_show_system_conf], add_help=False, help="Show system config")
p_config_cravat = sp_config.add_parser("oakvar", parents=[cmd_admin.parser_fn_config_cravat], add_help=False, help="Show oakvar config")

# new
p_new = sp_entry.add_parser("new", description="Create new modules", help="Create new modules")
sp_new = p_new.add_subparsers(title="Commands")
p_new_exampleinput = sp_new.add_parser("exampleinput", parents=[cmd_admin.parser_new_exampleinput], add_help=False, help="Make example input file")
p_new_annotator = sp_new.add_parser("annotator", parents=[cmd_admin.parser_new_annotator], add_help=False, help="Create new annotator")

# store
p_store = sp_entry.add_parser("store", description="Publish modules to the store", help="Publish modules to the store")
sp_store = p_store.add_subparsers(title="Commands")
p_store_publish = sp_store.add_parser("publish", parents=[cmd_admin.parser_store_publish], add_help=False, help="Publish a module")
p_store_newaccount = sp_store.add_parser("newaccount", parents=[cmd_admin.parser_store_newaccount], add_help=False, help="Create a publish account")
p_store_changepassword = sp_store.add_parser("changepassword", parents=[cmd_admin.parser_store_changepassword], add_help=False, help="Change the password of a publish account")
p_store_resetpassword = sp_store.add_parser("resetpassword", parents=[cmd_admin.parser_store_resetpassword], add_help=False, help="Request reset the password of a publish account")
p_store_verifyemail = sp_store.add_parser("verifyemail", parents=[cmd_admin.parser_store_verifyemail], add_help=False, help="Request email verification for a publish account")
p_store_checklogin = sp_store.add_parser("checklogin", parents=[cmd_admin.parser_store_checklogin], add_help=False, help="Check the login credentials of a publish account")

# util
p_util = sp_entry.add_parser("util", description="Utilities", help="Utilities")
sp_util = p_util.add_subparsers(title="Commands")
p_util_test = sp_util.add_parser("test", parents=[parser_fn_util_test], add_help=False, help="Test installed modules")
p_util_updateresult = sp_util.add_parser("updateresult", parents=[cmd_util.parser_util_updateresult], add_help=False, help="Update old result database to newer format")
p_util_sendgui = sp_util.add_parser("addjob", parents=[cmd_util.parser_util_addjob], add_help=False, help="Copy a command line job into the GUI submission list")
p_util_mergesqlite = sp_util.add_parser("mergesqlite", parents=[cmd_util.parser_util_mergesqlite], add_help=False, help="Merge SQLite result files")
p_util_filtersqlite = sp_util.add_parser("filtersqlite", parents=[cmd_util.parser_util_filtersqlite], add_help=False, help="Filter SQLite result files")
p_util_showsqliteinfo = sp_util.add_parser("showsqliteinfo", parents=[cmd_util.parser_util_showsqliteinfo], add_help=False, help="Show SQLite result file information")

# version
p_version = sp_entry.add_parser("version", parents=[cmd_admin.parser_version], add_help=False, help="Show version")

# feedback
p_feedback = sp_entry.add_parser(name="feedback", parents=[cmd_admin.parser_feedback], add_help=False, help="Send feedback to the developers")


def main():
    try:
        args = p_entry.parse_args()
        if hasattr(args, "func"):
            args.func(args)
        else:
            p_entry.parse_args(sys.argv[1:] + ["--help"])
    except SystemExit as e:
        raise e


if __name__ == "__main__":
    main()
