import click

from lib.amazon import has_notify_file, delete_notify_file, set_current_notify
from lib.cli import cli


@cli.group()
def notify():
    """Now-live notification manipulation commands."""


@notify.command(name="set_base")
@click.argument("sha", type=str)
def notify_current(sha: str):
    """Sets the first commit from which to start notifying to a specific one.
    The commit hash is not validated, so make sure to add a valid one"""
    if has_notify_file():
        delete_notify_file()
    set_current_notify(sha)
