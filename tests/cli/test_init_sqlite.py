from __future__ import unicode_literals

import os
import re
import shutil

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from great_expectations import DataContext
from great_expectations.cli import cli
from great_expectations.data_context.util import file_relative_path
from great_expectations.util import gen_directory_tree_str
from tests.cli.test_cli import yaml
from tests.cli.test_datasource_sqlite import (
    _add_datasource_and_credentials_to_context,
)
from tests.cli.test_init_pandas import _delete_and_recreate_dir
from tests.cli.utils import assert_no_logging_messages_or_tracebacks


@pytest.fixture
def titanic_sqlite_db_file(tmp_path_factory):
    from sqlalchemy import create_engine

    temp_dir = str(tmp_path_factory.mktemp("foo_path"))
    fixture_db_path = file_relative_path(__file__, "../test_sets/titanic.db")

    db_path = os.path.join(temp_dir, "titanic.db")
    shutil.copy(fixture_db_path, db_path)

    engine = create_engine("sqlite:///{}".format(db_path))
    assert engine.execute("select count(*) from titanic").fetchall()[0] == (1313,)
    return db_path


def test_cli_init_on_new_project(caplog, tmp_path_factory, titanic_sqlite_db_file):
    basedir = str(tmp_path_factory.mktemp("test_cli_init_diff"))
    ge_dir = os.path.join(basedir, "great_expectations")

    database_path = os.path.join(basedir, "titanic.db")
    shutil.copy(titanic_sqlite_db_file, database_path)
    engine = create_engine("sqlite:///{}".format(database_path))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--no-view", "-d", basedir],
        input="Y\n2\n5\ntitanic\n{}\n1\nwarning\n\n".format(engine.url),
    )
    stdout = result.output
    print(stdout)
    assert len(stdout) < 3000, "CLI output is unreasonably long."

    assert "Always know what to expect from your data" in stdout
    assert "What data would you like Great Expectations to connect to" in stdout
    assert "Which database backend are you using" in stdout
    assert "Give your new data source a short name" in stdout
    assert "What is the url/connection string for the sqlalchemy connection" in stdout
    assert "Attempting to connect to your database." in stdout
    assert "Great Expectations connected to your database" in stdout
    assert "Which table would you like to use?" in stdout
    assert "Name the new expectation suite [warning]" in stdout
    assert (
        "Great Expectations will choose a couple of columns and generate expectations about them"
        in stdout
    )
    assert "Profiling..." in stdout
    assert "Building" in stdout
    assert "Data Docs" in stdout
    assert "A new Expectation suite 'warning' was added to your project" in stdout
    assert "Great Expectations is now set up" in stdout

    context = DataContext(ge_dir)
    assert len(context.list_datasources()) == 1
    assert context.list_datasources() == [
        {"class_name": "SqlAlchemyDatasource", "name": "titanic"}
    ]

    first_suite = context.list_expectation_suite_keys()[0]
    suite = context.get_expectation_suite(first_suite.expectation_suite_name)
    assert len(suite.expectations) == 13

    assert os.path.isdir(ge_dir)
    config_path = os.path.join(basedir, "great_expectations/great_expectations.yml")
    assert os.path.isfile(config_path)

    config = yaml.load(open(config_path, "r"))
    data_source_class = config["datasources"]["titanic"]["data_asset_type"][
        "class_name"
    ]
    assert data_source_class == "SqlAlchemyDataset"

    obs_tree = gen_directory_tree_str(ge_dir)

    # Instead of monkey patching datetime, just regex out the time directories
    date_safe_obs_tree = re.sub(r"\d*T\d*\.\d*Z", "9999.9999", obs_tree)
    # Instead of monkey patching guids, just regex out the guids
    guid_safe_obs_tree = re.sub(
        r"[a-z0-9]{32}(?=\.(json|html))", "foobarbazguid", date_safe_obs_tree
    )
    print(guid_safe_obs_tree)
    assert (
        guid_safe_obs_tree
        == """\
great_expectations/
    .gitignore
    great_expectations.yml
    expectations/
        warning.json
    notebooks/
        pandas/
            validation_playground.ipynb
        spark/
            validation_playground.ipynb
        sql/
            validation_playground.ipynb
    plugins/
        custom_data_docs/
            renderers/
            styles/
                data_docs_custom_styles.css
            views/
    uncommitted/
        config_variables.yml
        data_docs/
            local_site/
                index.html
                expectations/
                    warning.html
                static/
                    fonts/
                        HKGrotesk/
                            HKGrotesk-Bold.otf
                            HKGrotesk-BoldItalic.otf
                            HKGrotesk-Italic.otf
                            HKGrotesk-Light.otf
                            HKGrotesk-LightItalic.otf
                            HKGrotesk-Medium.otf
                            HKGrotesk-MediumItalic.otf
                            HKGrotesk-Regular.otf
                            HKGrotesk-SemiBold.otf
                            HKGrotesk-SemiBoldItalic.otf
                    images/
                        0_values_not_null_html_en.jpg
                        10_suite_toc.jpeg
                        11_home_validation_results_failed.jpeg
                        12_validation_overview.png
                        13_validation_passed.jpeg
                        14_validation_failed.jpeg
                        15_validation_failed_unexpected_values.jpeg
                        16_validation_failed_unexpected_values (1).gif
                        1_values_not_null_html_de.jpg
                        2_values_not_null_json.jpg
                        3_values_not_null_validation_result_json.jpg
                        4_values_not_null_validation_result_html_en.jpg
                        5_home.png
                        6_home_tables.jpeg
                        7_home_suites.jpeg
                        8_home_validation_results_succeeded.jpeg
                        9_suite_overview.png
                        favicon.ico
                        glossary_scroller.gif
                        iterative-dev-loop.png
                        logo-long-vector.svg
                        logo-long.png
                        short-logo-vector.svg
                        short-logo.png
                        validation_failed_unexpected_values.gif
                        values_not_null_html_en.jpg
                        values_not_null_json.jpg
                        values_not_null_validation_result_html_en.jpg
                        values_not_null_validation_result_json.jpg
                    styles/
                        data_docs_custom_styles_template.css
                        data_docs_default_styles.css
                validations/
                    warning/
                        9999.9999/
                            foobarbazguid.html
        samples/
        validations/
            warning/
                9999.9999/
                    foobarbazguid.json
"""
    )

    assert_no_logging_messages_or_tracebacks(caplog, result)

    assert result.exit_code == 0


def test_init_on_existing_project_with_no_datasources_should_continue_init_flow_and_add_one(
    caplog, initialized_sqlite_project, titanic_sqlite_db_file,
):
    project_dir = initialized_sqlite_project
    ge_dir = os.path.join(project_dir, DataContext.GE_DIR)

    _remove_all_datasources(ge_dir)
    os.remove(os.path.join(ge_dir, "expectations", "warning.json"))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--no-view", "-d", project_dir],
        input="2\n5\nsqlite\nsqlite:///{}\n1\nmy_suite\n\n".format(
            titanic_sqlite_db_file
        ),
    )
    stdout = result.stdout

    assert result.exit_code == 0

    assert "Error: invalid input" not in stdout
    assert "Always know what to expect from your data" in stdout
    assert "What data would you like Great Expectations to connect to" in stdout
    assert (
        "Next, we will configure database credentials and store them in the `sqlite` section"
        in stdout
    )
    assert "What is the url/connection string for the sqlalchemy connection?" in stdout
    assert "Which table would you like to use?" in stdout
    assert "Great Expectations connected to your database" in stdout
    assert "A new Expectation suite 'my_suite' was added to your project" in stdout
    assert "This looks like an existing project that" not in stdout

    config = _load_config_file(os.path.join(ge_dir, DataContext.GE_YML))
    assert "sqlite" in config["datasources"].keys()

    context = DataContext(ge_dir)
    assert context.list_datasources() == [
        {"class_name": "SqlAlchemyDatasource", "name": "sqlite"}
    ]
    assert context.list_expectation_suite_keys()[0].expectation_suite_name == "my_suite"
    assert len(context.list_expectation_suite_keys()) == 1

    assert_no_logging_messages_or_tracebacks(caplog, result)


def _remove_all_datasources(ge_dir):
    config_path = os.path.join(ge_dir, DataContext.GE_YML)

    config = _load_config_file(config_path)
    config["datasources"] = {}

    with open(config_path, "w") as f:
        yaml.dump(config, f)

    context = DataContext(ge_dir)
    assert context.list_datasources() == []


def _load_config_file(config_path):
    assert os.path.isfile(config_path), "Config file is missing. Check path"

    with open(config_path, "r") as f:
        read = f.read()
        config = yaml.load(read)

    assert isinstance(config, dict)
    return config


@pytest.fixture
def initialized_sqlite_project(caplog, tmp_path_factory, titanic_sqlite_db_file):
    """This is an initialized project through the CLI."""
    basedir = str(tmp_path_factory.mktemp("my_rad_project"))

    engine = create_engine("sqlite:///{}".format(titanic_sqlite_db_file))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--no-view", "-d", basedir],
        input="Y\n2\n5\ntitanic\n{}\n1\nwarning\n\n".format(engine.url),
    )
    assert result.exit_code == 0
    assert_no_logging_messages_or_tracebacks(caplog, result)

    context = DataContext(os.path.join(basedir, DataContext.GE_DIR))
    assert isinstance(context, DataContext)
    assert len(context.list_datasources()) == 1
    assert context.list_datasources() == [
        {"class_name": "SqlAlchemyDatasource", "name": "titanic"}
    ]
    return basedir


def test_init_on_existing_project_with_multiple_datasources_exist_do_nothing(
    caplog, initialized_sqlite_project, titanic_sqlite_db, empty_sqlite_db
):
    project_dir = initialized_sqlite_project
    ge_dir = os.path.join(project_dir, DataContext.GE_DIR)

    context = DataContext(ge_dir)
    datasource_name = "wow_a_datasource"
    context = _add_datasource_and_credentials_to_context(
        context, datasource_name, empty_sqlite_db
    )
    assert len(context.list_datasources()) == 2

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--no-view", "-d", project_dir], input="n\n")
    stdout = result.stdout
    print(stdout)

    assert result.exit_code == 0

    assert "Error: invalid input" not in stdout

    assert "Always know what to expect from your data" in stdout
    assert "This looks like an existing project that" in stdout
    assert "appears complete" in stdout
    assert "Would you like to build & view this project's Data Docs" in stdout

    assert_no_logging_messages_or_tracebacks(caplog, result)


def test_init_on_existing_project_with_datasource_with_existing_suite_offer_to_build_docs(
    caplog, initialized_sqlite_project,
):
    project_dir = initialized_sqlite_project

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--no-view", "-d", project_dir], input="n\n")
    stdout = result.stdout

    assert result.exit_code == 0

    assert "Error: invalid input" not in stdout

    assert "Always know what to expect from your data" in stdout
    assert "This looks like an existing project that" in stdout
    assert "appears complete" in stdout
    assert "Would you like to build & view this project's Data Docs" in stdout

    assert_no_logging_messages_or_tracebacks(caplog, result)


def test_init_on_existing_project_with_datasource_with_no_suite_create_one(
    caplog, initialized_sqlite_project,
):
    project_dir = initialized_sqlite_project
    ge_dir = os.path.join(project_dir, DataContext.GE_DIR)
    uncommitted_dir = os.path.join(ge_dir, "uncommitted")

    # mangle the setup to remove all traces of any suite
    expectations_dir = os.path.join(ge_dir, "expectations")
    data_docs_dir = os.path.join(uncommitted_dir, "data_docs")
    validations_dir = os.path.join(uncommitted_dir, "validations")

    _delete_and_recreate_dir(expectations_dir)
    _delete_and_recreate_dir(data_docs_dir)
    _delete_and_recreate_dir(validations_dir)

    context = DataContext(ge_dir)
    assert context.list_expectation_suite_keys() == []

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--no-view", "-d", project_dir],
        input="1\nsink_me\n\n\n".format(os.path.join(project_dir, "data/Titanic.csv")),
    )
    stdout = result.stdout

    assert result.exit_code == 0

    assert "Always know what to expect from your data" in stdout
    assert "Which table would you like to use?" in stdout
    assert "Profiling.." in stdout
    assert "The following Data Docs sites were built" in stdout
    assert "Great Expectations is now set up" in stdout
    assert "A new Expectation suite 'sink_me' was added to your project" in stdout

    assert "Error: invalid input" not in stdout
    assert "This looks like an existing project that" not in stdout

    assert_no_logging_messages_or_tracebacks(caplog, result)

    context = DataContext(ge_dir)
    assert len(context.list_expectation_suite_keys()) == 1
