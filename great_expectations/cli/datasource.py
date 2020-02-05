import datetime
import enum
import importlib
import json
import logging
import os
import sys

import click

import great_expectations.exceptions as ge_exceptions
from great_expectations import DataContext, rtd_url_ge_version
from great_expectations.cli.docs import build_docs
from great_expectations.cli.init_messages import NO_DATASOURCES_FOUND
from great_expectations.cli.util import (
    _offer_to_install_new_template,
    cli_message,
)
from great_expectations.data_context.types.resource_identifiers import ValidationResultIdentifier, \
    ExpectationSuiteIdentifier
from great_expectations.datasource import (
    PandasDatasource,
    SparkDFDatasource,
    SqlAlchemyDatasource,
)
from great_expectations.datasource.generator import (
    ManualGenerator,
)
from great_expectations.exceptions import DatasourceInitializationError
from great_expectations.profile.basic_dataset_profiler import (
    SampleExpectationsDatasetProfiler,
)

from great_expectations.validator.validator import Validator
from great_expectations.core import ExpectationSuite
from great_expectations.datasource.generator.table_generator import TableGenerator
from great_expectations.exceptions import BatchKwargsError

logger = logging.getLogger(__name__)

# FIXME: This prevents us from seeing a huge stack of these messages in python 2. We'll need to fix that later.
# tests/test_cli.py::test_cli_profile_with_datasource_arg
#   /Users/abe/Documents/superconductive/tools/great_expectations/tests/test_cli.py:294: Warning: Click detected the use of the unicode_literals __future__ import.  This is heavily discouraged because it can introduce subtle bugs in your code.  You should instead use explicit u"" literals for your unicode strings.  For more information see https://click.palletsprojects.com/python3/
#     cli, ["profile", "my_datasource", "-d", project_root_dir])
click.disable_unicode_literals_warning = True


class DatasourceTypes(enum.Enum):
    PANDAS = "pandas"
    SQL = "sql"
    SPARK = "spark"
    # TODO DBT = "dbt"


DATASOURCE_TYPE_BY_DATASOURCE_CLASS = {
    "PandasDatasource": DatasourceTypes.PANDAS,
    "SparkDFDatasource": DatasourceTypes.SPARK,
    "SqlAlchemyDatasource": DatasourceTypes.SQL,
}

MANUAL_GENERATOR_CLASSES = (ManualGenerator)


class SupportedDatabases(enum.Enum):
    MYSQL = 'MySQL'
    POSTGRES = 'Postgres'
    REDSHIFT = 'Redshift'
    SNOWFLAKE = 'Snowflake'
    OTHER = 'other - Do you have a working SQLAlchemy connection string?'
    # TODO MSSQL
    # TODO BigQuery


@click.group()
def datasource():
    """datasource operations"""
    pass


@datasource.command(name="new")
@click.option(
    '--directory',
    '-d',
    default=None,
    help="The project's great_expectations directory."
)
def datasource_new(directory):
    """Add a new datasource to the data context."""
    try:
        context = DataContext(directory)
    except ge_exceptions.ConfigNotFoundError as err:
        cli_message("<red>{}</red>".format(err.message))
        return
    except ge_exceptions.ZeroDotSevenConfigVersionError as err:
        _offer_to_install_new_template(err, context.root_directory)

    datasource_name, data_source_type = add_datasource(context)

    if datasource_name:
        cli_message("A new datasource '{}' was added to your project.".format(datasource_name))
    else:  # no datasource was created
        sys.exit(1)


@datasource.command(name="list")
@click.option(
    '--directory',
    '-d',
    default=None,
    help="The project's great_expectations directory."
)
def datasource_list(directory):
    """List known datasources."""
    try:
        context = DataContext(directory)
        datasources = context.list_datasources()
        # TODO Pretty up this console output
        cli_message(str([d for d in datasources]))
    except ge_exceptions.ConfigNotFoundError as err:
        cli_message("<red>{}</red>".format(err.message))
        return
    except ge_exceptions.ZeroDotSevenConfigVersionError as err:
        _offer_to_install_new_template(err, context.root_directory)


@datasource.command(name="profile")
@click.argument('datasource_name', default=None, required=False)
@click.option(
    "--generator_name",
    "-g",
    default=None,
    help="The name of the batch kwarg generator configured in the datasource. The generator will list data assets in the datasource"
)
@click.option('--data_assets', '-l', default=None,
              help='Comma-separated list of the names of data assets that should be profiled. Requires datasource_name specified.')
@click.option('--profile_all_data_assets', '-A', is_flag=True, default=False,
              help='Profile ALL data assets within the target data source. '
                   'If True, this will override --max_data_assets.')
@click.option(
    "--directory",
    "-d",
    default=None,
    help="The project's great_expectations directory."
)
@click.option(
    "--view/--no-view",
    help="By default open in browser unless you specify the --no-view flag",
    default=True
)
@click.option('--batch_kwargs', default=None,
              help='Additional keyword arguments to be provided to get_batch when loading the data asset. Must be a valid JSON dictionary')
def datasource_profile(datasource_name, generator_name, data_assets, profile_all_data_assets, directory, view, batch_kwargs):
    """
    Profile a datasource

    If the optional data_assets and profile_all_data_assets arguments are not specified, the profiler will check
    if the number of data assets in the datasource exceeds the internally defined limit. If it does, it will
    prompt the user to either specify the list of data assets to profile or to profile all.
    If the limit is not exceeded, the profiler will profile all data assets in the datasource.

    :param datasource_name: name of the datasource to profile
    :param data_assets: if this comma-separated list of data asset names is provided, only the specified data assets will be profiled
    :param profile_all_data_assets: if provided, all data assets will be profiled
    :param directory:
    :param view: Open the docs in a browser
    :param batch_kwargs: Additional keyword arguments to be provided to get_batch when loading the data asset.
    :return:
    """

    try:
        context = DataContext(directory)
    except ge_exceptions.ConfigNotFoundError as err:
        cli_message("<red>{}</red>".format(err.message))
        return
    except ge_exceptions.ZeroDotSevenConfigVersionError as err:
        _offer_to_install_new_template(err, context.root_directory)
        return

    if batch_kwargs is not None:
        batch_kwargs = json.loads(batch_kwargs)

    if datasource_name is None:
        datasources = [datasource["name"] for datasource in context.list_datasources()]
        if not datasources:
            cli_message(NO_DATASOURCES_FOUND)
            sys.exit(1)
        elif len(datasources) > 1:
            cli_message(
                "<red>Error: please specify the datasource to profile. "\
                "Available datasources: " + ", ".join(datasources) + "</red>"
            )
            sys.exit(1)
        else:
            profile_datasource(
                context,
                datasources[0],
                generator_name=generator_name,
                data_assets=data_assets,
                profile_all_data_assets=profile_all_data_assets,
                open_docs=view,
                additional_batch_kwargs=batch_kwargs
            )
    else:
        profile_datasource(
            context,
            datasource_name,
            generator_name=generator_name,
            data_assets=data_assets,
            profile_all_data_assets=profile_all_data_assets,
            open_docs=view,
            additional_batch_kwargs=batch_kwargs
        )


def add_datasource(context, choose_one_data_asset=False):
    """
    Interactive flow for adding a datasource to an existing context.

    :param context:
    :param choose_one_data_asset: optional - if True, this signals the method that the intent
            is to let user choose just one data asset (e.g., a file) and there is no need
            to configure a generator that comprehensively scans the datasource for data assets
    :return: a tuple: datasource_name, data_source_type
    """

    msg_prompt_where_is_your_data = """
What data would you like Great Expectations to connect to?    
    1. Files on a filesystem (for processing with Pandas or Spark)
    2. Relational database (SQL)
"""

    msg_prompt_files_compute_engine = """
What are you processing your files with?
    1. Pandas
    2. PySpark
"""

    data_source_location_selection = click.prompt(
        msg_prompt_where_is_your_data,
        type=click.Choice(["1", "2"]),
        show_choices=False
    )

    datasource_name = None
    data_source_type = None

    if data_source_location_selection == "1":
        data_source_compute_selection = click.prompt(
            msg_prompt_files_compute_engine,
            type=click.Choice(["1", "2"]),
            show_choices=False
        )

        if data_source_compute_selection == "1":  # pandas

            data_source_type = DatasourceTypes.PANDAS

            datasource_name = _add_pandas_datasource(context, passthrough_generator_only=choose_one_data_asset)

        elif data_source_compute_selection == "2":  # Spark

            data_source_type = DatasourceTypes.SPARK

            datasource_name = _add_spark_datasource(context, passthrough_generator_only=choose_one_data_asset)
    else:
        data_source_type = DatasourceTypes.SQL
        datasource_name = _add_sqlalchemy_datasource(context, prompt_for_datasource_name=True)

    return datasource_name, data_source_type


def _add_pandas_datasource(context, passthrough_generator_only=True, prompt_for_datasource_name=True):
    if passthrough_generator_only:
        datasource_name = "files_datasource"
        configuration = PandasDatasource.build_configuration()

    else:
        path = click.prompt(
            msg_prompt_filesys_enter_base_path,
            type=click.Path(
                exists=True,
                file_okay=False,
                dir_okay=True,
                readable=True
            ),
            show_default=True
        )
        if path.startswith("./"):
            path = path[2:]

        if path.endswith("/"):
            basenamepath = path[:-1]
        else:
            basenamepath = path

        datasource_name = os.path.basename(basenamepath) + "__dir"
        if prompt_for_datasource_name:
            datasource_name = click.prompt(
                msg_prompt_datasource_name,
                default=datasource_name,
                show_default=True
            )

        configuration = PandasDatasource.build_configuration(generators={
    "subdir_reader": {
        "class_name": "SubdirReaderGenerator",
        "base_directory": os.path.join("..", path)
    }
}
)


    context.add_datasource(name=datasource_name, class_name='PandasDatasource', **configuration)
    return datasource_name


def load_library(library_name, install_instructions_string=None):
    """
    Dynamically load a module from strings or raise a helpful error.

    :param library_name: name of the library to load
    :param install_instructions_string: optional - used when the install instructions
            are different from 'pip install library_name'
    :return: True if the library was loaded successfully, False otherwise
    """
    # TODO remove this nasty python 2 hack
    try:
        ModuleNotFoundError
    except NameError:
        ModuleNotFoundError = ImportError

    try:
        loaded_module = importlib.import_module(library_name)
        return True
    except ModuleNotFoundError as e:
        if install_instructions_string:
            cli_message("""<red>ERROR: Great Expectations relies on the library `{}` to connect to your data.</red>
            - Please `{}` before trying again.""".format(library_name, install_instructions_string))
        else:
            cli_message("""<red>ERROR: Great Expectations relies on the library `{}` to connect to your data.</red>
      - Please `pip install {}` before trying again.""".format(library_name, library_name))

        return False


def _add_sqlalchemy_datasource(context, prompt_for_datasource_name=True):
    msg_success_database = "\n<green>Great Expectations connected to your database!</green>"

    if not load_library("sqlalchemy"):
        return None

    # TODO remove this nasty python 2 hack
    try:
        ModuleNotFoundError
    except NameError:
        ModuleNotFoundError = ImportError

    db_choices = [str(x) for x in list(range(1, 1 + len(SupportedDatabases)))]
    selected_database = int(
        click.prompt(
            msg_prompt_choose_database,
            type=click.Choice(db_choices),
            show_choices=False
        )
    ) - 1  # don't show user a zero index list :)

    selected_database = list(SupportedDatabases)[selected_database]

    datasource_name = "my_{}_db".format(selected_database.value.lower())
    if selected_database == SupportedDatabases.OTHER:
        datasource_name = "my_database"
    if prompt_for_datasource_name:
        datasource_name = click.prompt(
            msg_prompt_datasource_name,
            default=datasource_name,
            show_default=True
        )

    credentials = {}
    # Since we don't want to save the database credentials in the config file that will be
    # committed in the repo, we will use our Variable Substitution feature to store the credentials
    # in the credentials file (that will not be committed, since it is in the uncommitted directory)
    # with the datasource's name as the variable name.
    # The value of the datasource's "credentials" key in the config file (great_expectations.yml) will
    # be ${datasource name}.
    # GE will replace the ${datasource name} with the value from the credentials file in runtime.

    while True:
        cli_message(msg_db_config.format(datasource_name))

        if selected_database == SupportedDatabases.MYSQL:
            if not load_library("pymysql"):
                return None
            credentials = _collect_mysql_credentials(default_credentials=credentials)
        elif selected_database == SupportedDatabases.POSTGRES:
            if not load_library("psycopg2"):
                return None
            credentials = _collect_postgres_credentials(default_credentials=credentials)
        elif selected_database == SupportedDatabases.REDSHIFT:
            if not load_library("psycopg2"):
                return None
            credentials = _collect_redshift_credentials(default_credentials=credentials)
        elif selected_database == SupportedDatabases.SNOWFLAKE:
            if not load_library("snowflake", install_instructions_string="pip install snowflake-sqlalchemy"):
                return None
            credentials = _collect_snowflake_credentials(default_credentials=credentials)
        elif selected_database == SupportedDatabases.OTHER:
            sqlalchemy_url = click.prompt(
"""What is the url/connection string for the sqlalchemy connection?
(reference: https://docs.sqlalchemy.org/en/latest/core/engines.html#database-urls)
""",
                show_default=False)
            credentials = {
                "url": sqlalchemy_url
            }

        context.save_config_variable(datasource_name, credentials)

        message = """
<red>Cannot connect to the database.</red>
  - Please check your environment and the configuration you provided.
  - Database Error: {0:s}"""
        try:
            cli_message("<cyan>Attempting to connect to your database. This may take a moment...</cyan>")
            configuration = SqlAlchemyDatasource.build_configuration(credentials="${" + datasource_name + "}")
            context.add_datasource(name=datasource_name, class_name='SqlAlchemyDatasource', **configuration)
            cli_message(msg_success_database)
            break
        except ModuleNotFoundError as de:
            cli_message(message.format(str(de)))
            return None

        except DatasourceInitializationError as de:
            cli_message(message.format(str(de)))
            if not click.confirm(
                    "Enter the credentials again?".format(str(de)),
                    default=True
            ):
                context.add_datasource(datasource_name,
                                       initialize=False,
                                       module_name="great_expectations.datasource",
                                       class_name="SqlAlchemyDatasource",
                                       data_asset_type={
                                           "class_name": "SqlAlchemyDataset"},
                                       credentials="${" + datasource_name + "}",
                                       )
                # TODO this message about continuing may not be accurate
                cli_message(
                    """
We saved datasource {0:s} in {1:s} and the credentials you entered in {2:s}.
Since we could not connect to the database, you can complete troubleshooting in the configuration files documented here:
<blue>https://docs.greatexpectations.io/en/latest/tutorials/add-sqlalchemy-datasource.html?utm_source=cli&utm_medium=init&utm_campaign={3:s}#{4:s}</blue> .

After you connect to the datasource, run great_expectations datasource profile to continue.

""".format(datasource_name, DataContext.GE_YML, context.get_config()["config_variables_file_path"], rtd_url_ge_version, selected_database.value.lower()))
                return None

    return datasource_name


def _collect_postgres_credentials(default_credentials={}):
    credentials = {
        "drivername": "postgres"
    }

    credentials["host"] = click.prompt("What is the host for the postgres connection?",
                        default=default_credentials.get("host", "localhost"),
                        show_default=True)
    credentials["port"] = click.prompt("What is the port for the postgres connection?",
                        default=default_credentials.get("port", "5432"),
                        show_default=True)
    credentials["username"] = click.prompt("What is the username for the postgres connection?",
                            default=default_credentials.get("username", "postgres"),
                            show_default=True)
    credentials["password"] = click.prompt("What is the password for the postgres connection?",
                            default="",
                            show_default=False, hide_input=True)
    credentials["database"] = click.prompt("What is the database name for the postgres connection?",
                            default=default_credentials.get("database", "postgres"),
                            show_default=True)

    return credentials

def _collect_snowflake_credentials(default_credentials={}):
    credentials = {
        "drivername": "snowflake"
    }

    # required

    credentials["username"] = click.prompt("What is the user login name for the snowflake connection?",
                        default=default_credentials.get("username", ""),
                        show_default=True)
    credentials["password"] = click.prompt("What is the password for the snowflake connection?",
                            default="",
                            show_default=False, hide_input=True)
    credentials["host"] = click.prompt("What is the account name for the snowflake connection?",
                        default=default_credentials.get("host", ""),
                        show_default=True)


    # optional

    #TODO: database is optional, but it is not a part of query
    credentials["database"] = click.prompt("What is database name for the snowflake connection?",
                        default=default_credentials.get("database", ""),
                        show_default=True)

    # # TODO: schema_name is optional, but it is not a part of query and there is no obvious way to pass it
    # credentials["schema_name"] = click.prompt("What is schema name for the snowflake connection?",
    #                     default=default_credentials.get("schema_name", ""),
    #                     show_default=True)

    credentials["query"] = {}
    credentials["query"]["warehouse_name"] = click.prompt("What is warehouse name for the snowflake connection?",
                        default=default_credentials.get("warehouse_name", ""),
                        show_default=True)
    credentials["query"]["role_name"] = click.prompt("What is role name for the snowflake connection?",
                        default=default_credentials.get("role_name", ""),
                        show_default=True)

    return credentials

def _collect_mysql_credentials(default_credentials={}):

    # We are insisting on pymysql driver when adding a MySQL datasource through the CLI
    # to avoid overcomplication of this flow.
    # If user wants to use another driver, they must create the sqlalchemy connection
    # URL by themselves in config_variables.yml
    credentials = {
        "drivername": "mysql+pymysql"
    }

    credentials["host"] = click.prompt("What is the host for the MySQL connection?",
                        default=default_credentials.get("host", "localhost"),
                        show_default=True)
    credentials["port"] = click.prompt("What is the port for the MySQL connection?",
                        default=default_credentials.get("port", "3306"),
                        show_default=True)
    credentials["username"] = click.prompt("What is the username for the MySQL connection?",
                            default=default_credentials.get("username", ""),
                            show_default=True)
    credentials["password"] = click.prompt("What is the password for the MySQL connection?",
                            default="",
                            show_default=False, hide_input=True)
    credentials["database"] = click.prompt("What is the database name for the MySQL connection?",
                            default=default_credentials.get("database", ""),
                            show_default=True)

    return credentials

def _collect_redshift_credentials(default_credentials={}):

    # We are insisting on psycopg2 driver when adding a Redshift datasource through the CLI
    # to avoid overcomplication of this flow.
    # If user wants to use another driver, they must create the sqlalchemy connection
    # URL by themselves in config_variables.yml
    credentials = {
        "drivername": "postgresql+psycopg2"
    }

    # required

    credentials["host"] = click.prompt("What is the host for the Redshift connection?",
                        default=default_credentials.get("host", ""),
                        show_default=True)
    credentials["port"] = click.prompt("What is the port for the Redshift connection?",
                        default=default_credentials.get("port", "5439"),
                        show_default=True)
    credentials["username"] = click.prompt("What is the username for the Redshift connection?",
                            default=default_credentials.get("username", ""),
                            show_default=True)
    credentials["password"] = click.prompt("What is the password for the Redshift connection?",
                            default="",
                            show_default=False, hide_input=True)
    credentials["database"] = click.prompt("What is the database name for the Redshift connection?",
                            default=default_credentials.get("database", ""),
                            show_default=True)

    # optional

    credentials["query"] = {}
    credentials["query"]["sslmode"] = click.prompt("What is sslmode name for the Redshift connection?",
                        default=default_credentials.get("sslmode", "prefer"),
                        show_default=True)

    return credentials

def _add_spark_datasource(context, passthrough_generator_only=True, prompt_for_datasource_name=True):
    if not load_library("pyspark"):
        return None

    if passthrough_generator_only:
        datasource_name = "files_spark_datasource"

        # configuration = SparkDFDatasource.build_configuration(generators={
        #     "default": {
        #         "class_name": "PassthroughGenerator",
        #     }
        # }
        # )
        configuration = SparkDFDatasource.build_configuration()

    else:
        path = click.prompt(
            msg_prompt_filesys_enter_base_path,
            # default='/data/',
            type=click.Path(
                exists=True,
                file_okay=False,
                dir_okay=True,
                readable=True
            ),
            show_default=True
        )
        if path.startswith("./"):
            path = path[2:]

        if path.endswith("/"):
            basenamepath = path[:-1]
        else:
            basenamepath = path

        datasource_name = os.path.basename(basenamepath) + "__dir"
        if prompt_for_datasource_name:
            datasource_name = click.prompt(
                msg_prompt_datasource_name,
                default=datasource_name,
                show_default=True
            )

        configuration = SparkDFDatasource.build_configuration(generators={
    "subdir_reader": {
        "class_name": "SubdirReaderGenerator",
        "base_directory": os.path.join("..", path)
    }
}
)


    context.add_datasource(name=datasource_name, class_name='SparkDFDatasource', **configuration)
    return datasource_name


def select_datasource(context, datasource_name=None):
    msg_prompt_select_data_source = "Select data source"
    msg_no_datasources_configured = "No datasources"

    data_source = None

    if datasource_name is None:
        data_sources = context.list_datasources()
        if len(data_sources) == 0:
            cli_message(msg_no_datasources_configured)
        elif len(data_sources) ==1:
            datasource_name = data_sources[0]["name"]
        else:
            choices = "\n".join(["    {}. {}".format(i, data_source["name"]) for i, data_source in enumerate(data_sources, 1)])
            option_selection = click.prompt(
                msg_prompt_select_data_source + "\n" + choices + "\n",
                type=click.Choice([str(i) for i, data_source in enumerate(data_sources, 1)]),
                show_choices=False
            )
            datasource_name = data_sources[int(option_selection)-1]["name"]

    data_source = context.get_datasource(datasource_name)

    return data_source

def select_generator(context, datasource_name, available_data_assets_dict=None):
    msg_prompt_select_generator = "Select generator"

    if available_data_assets_dict is None:
        available_data_assets_dict = context.get_available_data_asset_names(datasource_names=datasource_name)

    available_data_asset_names_by_generator = {}
    for key, value in available_data_assets_dict[datasource_name].items():
        if len(value["names"]) > 0:
            available_data_asset_names_by_generator[key] = value["names"]

    if len(available_data_asset_names_by_generator.keys()) == 0:
        return None
    elif len(available_data_asset_names_by_generator.keys()) == 1:
        return list(available_data_asset_names_by_generator.keys())[0]
    else:  # multiple generators
        generator_names = list(available_data_asset_names_by_generator.keys())
        choices = "\n".join(["    {}. {}".format(i, generator_name) for i, generator_name in enumerate(generator_names, 1)])
        option_selection = click.prompt(
            msg_prompt_select_generator + "\n" + choices,
            type=click.Choice([str(i) for i, generator_name in enumerate(generator_names, 1)]),
            show_choices=False
        )
        generator_name = generator_names[int(option_selection)-1]

        return generator_name

def get_batch_kwargs(context,
                     datasource_name=None,
                     generator_name=None,
                     generator_asset=None,
                     additional_batch_kwargs=None):
    """
    This method manages the interaction with user necessary to obtain batch_kwargs for a batch of a data asset.

    In order to get batch_kwargs this method needs datasource_name, generator_name and generator_asset
    to combine them into a fully qualified data asset identifier(datasource_name/generator_name/generator_asset).
    All three arguments are optional. If they are present, the method uses their values. Otherwise, the method
    prompts user to enter them interactively. Since it is possible for any of these three components to be
    passed to this method as empty values and to get their values after interacting with user, this method
    returns these components' values in case they changed.

    If the datasource has generators that can list available data asset names, the method lets user choose a name
    from that list (note: if there are multiple generators, user has to choose one first). If a name known to
    the chosen generator is selected, the generator will be able to yield batch_kwargs. The method also gives user
    an alternative to selecting the data asset name from the generator's list - user can type in a name for their
    data asset. In this case a passthrough batch kwargs generator will be used to construct a fully qualified data asset
    identifier (note: if the datasource has no passthrough generator configured, the method will exist with a failure).
    Since no generator can yield batch_kwargs for this data asset name, the method prompts user to specify batch_kwargs
    by choosing a file (if the datasource is pandas or spark) or by writing a SQL query (if the datasource points
    to a database).

    :param context:
    :param datasource_name:
    :param generator_name:
    :param generator_asset:
    :param additional_batch_kwargs:
    :return: a tuple: (datasource_name, generator_name, generator_asset, batch_kwargs). The components
                of the tuple were passed into the methods as optional arguments, but their values might
                have changed after this method's execution. If the returned batch_kwargs is None, it means
                that the generator will know to yield batch_kwargs when called.
    """

    msg_prompt_enter_data_asset_name = "\nWhich data would you like to use? (Choose one)\n"

    msg_prompt_enter_data_asset_name_suffix = "    Don't see the data asset in the list above? Just type the name.\n"

    data_source = select_datasource(context, datasource_name=datasource_name)

    batch_kwargs = None

    try:
        available_data_assets_dict = context.get_available_data_asset_names(datasource_names=datasource_name)
    except ValueError:
        # the datasource has no generators
        available_data_assets_dict = {datasource_name: {}}

    if generator_name is None:
        generator_name = select_generator(context, datasource_name,
                                          available_data_assets_dict=available_data_assets_dict)

    # if the user provided us with the generator name and the generator asset, we have everything we need -
    # let's ask the generator to build batch kwargs for this asset - we are done.
    if generator_name is not None and generator_asset is not None:
        generator = datasource.get_generator(generator_name)
        batch_kwargs = generator.build_batch_kwargs(generator_asset, **additional_batch_kwargs)
        return batch_kwargs

    if isinstance(context.get_datasource(datasource_name), (PandasDatasource, SparkDFDatasource)):
        generator_asset, batch_kwargs = _get_batch_kwargs_from_generator_or_from_file_path(
            context,
            datasource_name,
            generator_name=generator_name,
        )

    elif isinstance(context.get_datasource(datasource_name), SqlAlchemyDatasource):
        generator_asset, batch_kwargs = _load_query_as_data_asset_from_sqlalchemy_datasource(context,
                                                                                             datasource_name,
                                                                                             generator_name=generator_name,
                                                                                             additional_batch_kwargs=additional_batch_kwargs)
    else:
        raise ge_exceptions.DataContextError("Datasource {0:s} is expected to be a PandasDatasource or SparkDFDatasource, but is {1:s}".format(datasource_name, str(type(context.get_datasource(datasource_name)))))

    return (datasource_name, generator_name, generator_asset, batch_kwargs)


def create_expectation_suite(
    context,
    datasource_name=None,
    generator_name=None,
    generator_asset=None,
    batch_kwargs=None,
    expectation_suite_name=None,
    additional_batch_kwargs=None,
    show_intro_message=False,
    open_docs=False
):

    """
    Create a new expectation suite.

    :param context:
    :param datasource_name:
    :param generator_name:
    :param generator_asset:
    :param batch_kwargs:
    :param expectation_suite_name:
    :param additional_batch_kwargs:
    :return: a tuple: (success, suite name)
    """

    msg_intro = """
<cyan>========== Create sample Expectations ==========</cyan>


"""

    msg_some_data_assets_not_found = """Some of the data assets you specified were not found: {0:s}    
    """

    msg_prompt_what_will_profiler_do = """
Great Expectations will choose a couple of columns and generate expectations about them
to demonstrate some examples of assertions you can make about your data. 
    
Press Enter to continue...
"""

    msg_prompt_expectation_suite_name = """
Name the new expectation suite"""

    msg_data_doc_intro = """
<cyan>========== Data Docs ==========</cyan>"""

    if show_intro_message:
        cli_message(msg_intro)

    data_source = select_datasource(context, datasource_name=datasource_name)
    if data_source is None:
        raise ge_exceptions.DataContextError("No datasources found in the context")

    datasource_name = data_source.name

    if generator_name is None or generator_asset is None or batch_kwargs is None:
        datasource_name, generator_name, generator_asset, batch_kwargs = get_batch_kwargs(context,
                                                                                           datasource_name=datasource_name,
                                                                                           generator_name=generator_name,
                                                                                           generator_asset=generator_asset,
                                                                                           additional_batch_kwargs=additional_batch_kwargs)

    if expectation_suite_name is None:
        expectation_suite_name = click.prompt(msg_prompt_expectation_suite_name, default="warning", show_default=True)

    profiler = SampleExpectationsDatasetProfiler

    click.prompt(msg_prompt_what_will_profiler_do, default="Enter", hide_input=True)

    cli_message("\nProfiling...")
    run_id = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S.%fZ")

    profiling_results = context.profile_data_asset(
        datasource_name,
        generator_name=generator_name,
        data_asset_name=generator_asset,
        batch_kwargs=batch_kwargs,
        profiler=profiler,
        expectation_suite_name=expectation_suite_name,
        run_id=run_id,
        additional_batch_kwargs=additional_batch_kwargs
    )

    if profiling_results['success']:
        build_docs(context, view=open_docs)
        if open_docs:  # This is mostly to keep tests from spawning windows
            expectation_suite_identifier = ExpectationSuiteIdentifier(
                expectation_suite_name=expectation_suite_name
            )

            validation_result_identifier = ValidationResultIdentifier(
                expectation_suite_identifier=expectation_suite_identifier,
                run_id=run_id,
                batch_identifier=None
            )
            context.open_data_docs(resource_identifier=validation_result_identifier)

        return True, expectation_suite_name

    if profiling_results['error']['code'] == DataContext.PROFILING_ERROR_CODE_SPECIFIED_DATA_ASSETS_NOT_FOUND:
        raise ge_exceptions.DataContextError(msg_some_data_assets_not_found.format(",".join(profiling_results['error']['not_found_data_assets'])))
    if not profiling_results['success']:  # unknown error
        raise ge_exceptions.DataContextError("Unknown profiling error code: " + profiling_results['error']['code'])




def _get_batch_kwargs_from_generator_or_from_file_path(context, datasource_name,
                                                       generator_name=None,
                                                       additional_batch_kwargs={}):
    msg_prompt_generator_or_file_path =  """
Would you like to enter the path of the file or choose from the list of data assets in this datasource? 
    1. I want a list of data assets in this datasource
    2. I will enter the path of a data file
"""
    msg_prompt_file_path = """
Enter the path (relative or absolute) of a data file
"""

    msg_prompt_enter_data_asset_name = "\nWhich data would you like to use? (Choose one)\n"

    msg_prompt_enter_data_asset_name_suffix = "    Don't see the name of the data asset in the list above? Just type it\n"

    msg_prompt_file_type = """
We could not determine the format of the file. What is it?
    1. CSV
    2. Parquet
    3. Excel
    4. JSON
"""

    reader_method_file_extensions = {
        "1": "csv",
        "2": "parquet",
        "3": "xlsx",
        "4": "json",
    }

    generator_asset = None

    datasource = context.get_datasource(datasource_name)
    if generator_name is not None:
        generator = datasource.get_generator(generator_name)

        option_selection = click.prompt(
            msg_prompt_generator_or_file_path,
            type=click.Choice(["1", "2"]),
            show_choices=False
        )

        if option_selection == "1":

            available_data_asset_names = generator.get_available_data_asset_names()["names"]
            available_data_asset_names_str = ["{} ({})".format(name[0], name[1]) for name in
                                              available_data_asset_names]

            data_asset_names_to_display = available_data_asset_names_str[:50]
            choices = "\n".join(["    {}. {}".format(i, name) for i, name in enumerate(data_asset_names_to_display, 1)])
            prompt = msg_prompt_enter_data_asset_name + choices + "\n" + msg_prompt_enter_data_asset_name_suffix.format(
                len(data_asset_names_to_display))

            generator_asset_selection = click.prompt(prompt, default=None, show_default=False)

            generator_asset_selection = generator_asset_selection.strip()
            try:
                data_asset_index = int(generator_asset_selection) - 1
                try:
                    generator_asset = \
                        [name[0] for name in available_data_asset_names][data_asset_index]
                except IndexError:
                    pass
            except ValueError:
                generator_asset = generator_asset_selection

            batch_kwargs = generator.build_batch_kwargs(generator_asset, **additional_batch_kwargs)
            return (generator_asset, batch_kwargs)

    # No generator name was passed or the user chose to enter a file path

    # We should allow a directory for Spark, but not for Pandas
    dir_okay = isinstance(datasource, SparkDFDatasource)

    path = click.prompt(
        msg_prompt_file_path,
        type=click.Path(
            exists=True,
            file_okay=True,
            dir_okay=dir_okay,
            readable=True
        ),
        show_default=True
    )

    path = os.path.abspath(path)

    batch_kwargs = {
        "path": path,
        "datasource": datasource_name
    }

    reader_method = None
    try:
        reader_method = datasource.guess_reader_method_from_path(path)["reader_method"]
    except BatchKwargsError:
        pass

    if reader_method is None:

        while True:

            option_selection = click.prompt(
                msg_prompt_file_type,
                type=click.Choice(["1", "2", "3", "4"]),
                show_choices=False
            )

            try:
                reader_method = datasource.guess_reader_method_from_path(path + "." + reader_method_file_extensions[option_selection])["reader_method"]
            except BatchKwargsError:
                pass

            if reader_method is not None:
                batch_kwargs["reader_method"] = reader_method
                batch = datasource.get_batch(batch_kwargs=batch_kwargs)
                break
    else:
        # TODO: read the file and confirm with user that we read it correctly (headers, columns, etc.)
        batch = datasource.get_batch(batch_kwargs=batch_kwargs)


    return (generator_asset, batch_kwargs)


def _load_query_as_data_asset_from_sqlalchemy_datasource(context, datasource_name,
                                                         generator_name=None,
                                                         additional_batch_kwargs={}):
    msg_prompt_query = """
Enter an SQL query
"""
    msg_prompt_data_asset_name = """
    Give your new data asset a short name
"""
    msg_prompt_enter_data_asset_name = "\nWhich table would you like to use? (Choose one)\n"

    msg_prompt_enter_data_asset_name_suffix = "    Don't see the table in the list above? Just type the SQL query\n"

    generator_asset = None

    datasource = context.get_datasource(datasource_name)

    temp_generator = TableGenerator(name="temp", datasource=datasource)

    available_data_asset_names = temp_generator.get_available_data_asset_names()["names"]
    available_data_asset_names_str = ["{} ({})".format(name[0], name[1]) for name in
                                      available_data_asset_names]

    data_asset_names_to_display = available_data_asset_names_str[:5]
    choices = "\n".join(["    {}. {}".format(i, name) for i, name in enumerate(data_asset_names_to_display, 1)])
    prompt = msg_prompt_enter_data_asset_name + choices + "\n" + msg_prompt_enter_data_asset_name_suffix.format(
        len(data_asset_names_to_display))

    while True:
        try:
            query = None

            if len(available_data_asset_names) > 0:
                selection = click.prompt(prompt, default=None, show_default=False)

                selection = selection.strip()
                try:
                    data_asset_index = int(selection) - 1
                    try:
                        generator_asset = \
                            [name[0] for name in available_data_asset_names][data_asset_index]
                    except IndexError:
                        pass
                except ValueError:
                    query = selection

            else:
                query = click.prompt(msg_prompt_query, default=None, show_default=False)


            if query is None:
                batch_kwargs = temp_generator.build_batch_kwargs(generator_asset, **additional_batch_kwargs)
            else:
                batch_kwargs = {
                    "query": query,
                    "datasource": datasource_name
                }

                Validator(batch=datasource.get_batch(batch_kwargs), expectation_suite=ExpectationSuite("throwaway")).get_dataset()

            break
        except Exception as error: # TODO: catch more specific exception
            cli_message("""<red>ERROR: {}</red>""".format(str(error)))

    return (generator_asset, batch_kwargs)


def profile_datasource(
    context,
    datasource_name,
    generator_name=None,
    data_assets=None,
    profile_all_data_assets=False,
    max_data_assets=20,
    additional_batch_kwargs=None,
    open_docs=False,
):
    """"Profile a named datasource using the specified context"""
    msg_intro = """
<cyan>========== Profiling ==========</cyan>

Profiling '{0:s}' will create expectations and documentation.
"""

    msg_confirm_ok_to_proceed = """Would you like to profile '{0:s}'?"""

    msg_skipping = "Skipping profiling for now. You can always do this later " \
                   "by running `<green>great_expectations datasource profile</green>`."

    msg_some_data_assets_not_found = """Some of the data assets you specified were not found: {0:s}    
"""

    msg_too_many_data_assets = """There are {0:d} data assets in {1:s}. Profiling all of them might take too long.    
"""

    msg_error_multiple_generators_found = """<red>More than one batch kwarg generators found in datasource {0:s}.
Specify the one you want the profiler to use in generator_name argument.</red>      
"""

    msg_error_no_generators_found = """<red>No batch kwarg generators can list available data assets in datasource {0:s}.
The datasource might be empty or a generator not configured in the config file.</red>    
"""

    msg_prompt_enter_data_asset_list = """Enter comma-separated list of data asset names (e.g., {0:s})   
"""

    msg_options = """Choose how to proceed:
  1. Specify a list of the data assets to profile
  2. Exit and profile later
  3. Profile ALL data assets (this might take a while)
"""

    msg_data_doc_intro = """
<cyan>========== Data Docs ==========</cyan>

Great Expectations is building Data Docs from the data you just profiled!"""

    cli_message(msg_intro.format(datasource_name, rtd_url_ge_version))

    if data_assets:
        data_assets = [item.strip() for item in data_assets.split(",")]

    # Call the data context's profiling method to check if the arguments are valid
    profiling_results = context.profile_datasource(
        datasource_name,
        generator_name=generator_name,
        data_assets=data_assets,
        profile_all_data_assets=profile_all_data_assets,
        max_data_assets=max_data_assets,
        dry_run=True,
        additional_batch_kwargs=additional_batch_kwargs
    )

    if profiling_results["success"] is True:  # data context is ready to profile - run profiling
        if data_assets or profile_all_data_assets or click.confirm(msg_confirm_ok_to_proceed.format(datasource_name), default=True):
            profiling_results = context.profile_datasource(
                datasource_name,
                data_assets=data_assets,
                profile_all_data_assets=profile_all_data_assets,
                max_data_assets=max_data_assets,
                dry_run=False,
                additional_batch_kwargs=additional_batch_kwargs
            )
        else:
            cli_message(msg_skipping)
            return
    else:  # we need to get arguments from user interactively
        do_exit = False
        while not do_exit:
            if profiling_results['error']['code'] == DataContext.PROFILING_ERROR_CODE_SPECIFIED_DATA_ASSETS_NOT_FOUND:
                cli_message(msg_some_data_assets_not_found.format("," .join(profiling_results['error']['not_found_data_assets'])))
            elif profiling_results['error']['code'] == DataContext.PROFILING_ERROR_CODE_TOO_MANY_DATA_ASSETS:
                cli_message(msg_too_many_data_assets.format(profiling_results['error']['num_data_assets'], datasource_name))
            elif profiling_results['error']['code'] == DataContext.PROFILING_ERROR_CODE_MULTIPLE_GENERATORS_FOUND:
                cli_message(
                    msg_error_multiple_generators_found.format(datasource_name))
                sys.exit(1)
            elif profiling_results['error']['code'] == DataContext.PROFILING_ERROR_CODE_NO_GENERATOR_FOUND:
                cli_message(
                    msg_error_no_generators_found.format(datasource_name))
                sys.exit(1)
            else: # unknown error
                raise ValueError("Unknown profiling error code: " + profiling_results['error']['code'])

            option_selection = click.prompt(
                msg_options,
                type=click.Choice(["1", "2", "3"]),
                show_choices=False
            )

            if option_selection == "1":
                data_assets = click.prompt(
                    msg_prompt_enter_data_asset_list.format(", ".join([data_asset[0] for data_asset in profiling_results['error']['data_assets']][:3])),
                    default=None,
                    show_default=False
                )
                if data_assets:
                    data_assets = [item.strip() for item in data_assets.split(",")]
            elif option_selection == "3":
                profile_all_data_assets = True
                data_assets = None
            elif option_selection == "2": # skip
                cli_message(msg_skipping)
                return
            else:
                raise ValueError("Unrecognized option: " + option_selection)

            # after getting the arguments from the user, let's try to run profiling again
            # (no dry run this time)
            profiling_results = context.profile_datasource(
                datasource_name,
                generator_name=generator_name,
                data_assets=data_assets,
                profile_all_data_assets=profile_all_data_assets,
                max_data_assets=max_data_assets,
                dry_run=False,
                additional_batch_kwargs=additional_batch_kwargs
            )

            if profiling_results["success"]:  # data context is ready to profile
                break

    cli_message(msg_data_doc_intro.format(rtd_url_ge_version))
    build_docs(context, view=open_docs)
    if open_docs:  # This is mostly to keep tests from spawning windows
        context.open_data_docs()


msg_prompt_choose_datasource = """Configure a datasource:
    1. Pandas DataFrame
    2. Relational database (SQL)
    3. Spark DataFrame
    4. Skip datasource configuration
"""


msg_prompt_choose_database = """
Which database backend are you using?
{}
""".format("\n".join(["    {}. {}".format(i, db.value) for i, db in enumerate(SupportedDatabases, 1)]))

#     msg_prompt_dbt_choose_profile = """
# Please specify the name of the dbt profile (from your ~/.dbt/profiles.yml file Great Expectations \
# should use to connect to the database
#     """

#     msg_dbt_go_to_notebook = """
# To create expectations for your dbt models start Jupyter and open notebook
# great_expectations/notebooks/using_great_expectations_with_dbt.ipynb -
# it will walk you through next steps.
#     """

msg_prompt_filesys_enter_base_path = """
Enter the path (relative or absolute) of the root directory where the data files are stored.
"""

msg_prompt_datasource_name = """
Give your new data source a short name.
"""

msg_db_config = """
Next, we will configure database credentials and store them in the `{0:s}` section
of this config file: great_expectations/uncommitted/config_variables.yml:
"""

msg_unknown_data_source = """
Do we not have the type of data source you want?
    - Please create a GitHub issue here so we can discuss it!
    - <blue>https://github.com/great-expectations/great_expectations/issues/new</blue>"""
