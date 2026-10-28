"""-c/--config and --log-level count before OR after the verb.

A subparser that inherits an option with a default overwrites the value the
top-level parser already read: `station-web -c X serve` served the defaults,
bound to 0.0.0.0, while the help promised either order.
"""
import pytest

from station_web.cli import build_parser


@pytest.mark.parametrize("argv", [
    ["-c", "/x.toml", "serve"],
    ["serve", "-c", "/x.toml"],
    ["--config", "/x.toml", "validate", "--json"],
])
def test_config_counts_on_either_side_of_the_verb(argv):
    assert build_parser().parse_args(argv).config == "/x.toml"


@pytest.mark.parametrize("argv", [
    ["--log-level", "DEBUG", "serve"],
    ["serve", "--log-level", "DEBUG"],
])
def test_log_level_counts_on_either_side_of_the_verb(argv):
    assert build_parser().parse_args(argv).log_level == "DEBUG"


def test_absent_options_read_as_none():
    args = build_parser().parse_args(["serve"])
    assert args.config is None and args.log_level is None
