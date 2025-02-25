import argparse
import sys
from argparse import ArgumentDefaultsHelpFormatter


class MyParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("error: %s\n" % message)
        self.print_help()
        sys.exit(2)


class arguments:

    def __init__(self) -> None:
        return

    def get_args(self, sysargs):
        """
        Get the arguments from the command line

        Parameters
        ----------
        sysargs : list
            List of arguments from the command line

        Returns
        -------
        args : argparse.Namespace
            The arguments from the command line
        """

        parent_parser = MyParser(
            description="Create Zarr dataset from data-catalog (dmidc)",
            formatter_class=ArgumentDefaultsHelpFormatter,
        )

        parent_parser.add_argument(
            "--verbose", 
            action="store_true", 
            help="Verbose output", 
            default=False
        )

        parent_parser.add_argument(
            "--source",
            type=str,
            help="Which data-catalog source to use",
            default="dinisf",
            required=False,
        )

        parent_parser.add_argument(
            "--output",
            type=str,
            help="Output directory",
            default="output",
            required=False,
        )

        parent_parser.add_argument(
            "--log-level", default="INFO", help="The log level to use"
        )

        parent_parser.add_argument(
            "--log-file", default=None, help="The file to log to"
        )

        if len(sysargs) == 0:
            parent_parser.print_help()
            sys.exit(2)

        args = parent_parser.parse_args()

        return args
