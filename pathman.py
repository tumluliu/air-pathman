"""
Usage:
    pathman -r ROUTER -p PROFILE -i INPUT_FILE [-o MONGO_CONN] [-x PARAMS] [-v | --verbose]
    pathman -h | --help
    pathman --version

Fetch machine-planned routes between the origins and destinations defined
in a well-formated CSV file (a sample can be found under the ./input
directory) from a specific online directions service provider, i.e.
the ROUTER for the transit mode speficed by PROFILE. The routing responses
will be stored as JSON documents in MongoDB whose connection string is
specified by MONGO_CONN. The extra parameters for different routing
services can be provided in the json file specified by the PARAMS argument.

Options:
    -r ROUTER      Set routing service provider (required)
    -p PROFILE     Set the preferred routing profile indicating the
                   transportation mode to use for routing (required)
                   [default: walking]
    -i INPUT_FILE  Set the input csv file containing all the point pairs (required)
    -o MONGO_CONN  Set the output MongoDB server connection string.(required)
                   [default: mongodb://localhost:27017/]
    -x PARAMS      Extra parameters for the router, a plain text file in JSON
                   format (optional)
    -v --verbose   Show running log in detail
    -h --help      Show this help
    --version      Show version number

Arguments:
    ROUTER         Routing service API provider name. The valid values are
                   configured in appconf.json file
    PROFILE        Routing profile name indicating what kind of transportation
                   mode should be use, default to walking
    INPUT_FILE     Points information file in csv format. Must have start_lon,
                   start_lat, end_lon, end_lat and id fields at least to
                   record the longtitude, latitude coordinates of the origin 
                   and destination pairs
    MONGO_CONN     MongoDB connection string, default to mongodb://localhost:27017/
    PARAMS         JSON file containing extra parameters for the router

Examples:
    pathman -r mapbox -p walking -i ./input/routing_samples.csv
    pathman -r openrouteservice -p cycling -i ./input/routing_samples.csv -o mongodb://localhost:27017/ -v
    pathman -r google -p driving -i ./input/routing_samples.csv -o mongodb://192.168.0.1:27017/ -v
"""
import json
import os
import csv
import datetime
import logging.config
import logging
from pymongo import MongoClient
from docopt import docopt, DocoptExit
try:
    from schema import Schema, And, Or, Optional, Use, SchemaError
except ImportError:
    exit('This example requires that `schema` data-validation library'
         ' is installed: \n    pip install schema\n'
         'https://github.com/halst/schema')
from rap import __version__, RoutingServiceFactory

DEFAULT_LOG_CONF_FILE = 'logging.json'
DEFAULT_LOGGING_LVL = logging.WARNING
LOG_CONF_FILE = DEFAULT_LOG_CONF_FILE
LOG_CONF_ENV_VAR = os.getenv('PATHMAN_LOG_CFG', None)
if LOG_CONF_ENV_VAR:
    LOG_CONF_FILE = LOG_CONF_ENV_VAR
if os.path.exists(LOG_CONF_FILE):
    with open(LOG_CONF_FILE, 'rt') as f:
        logging.config.dictConfig(json.load(f))
else:
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def validate_arguments(raw_args, conf):
    """ Command line arguments validator
    """
    LOGGER.info("Validating input arguments")
    sch = Schema({
        '-r': And(Use(str.lower),
                  lambda r: str.lower(r) in conf['routers'],
                  error="ROUTER should be one of {0}".format(', '.join(conf[
                      'routers']))),
        '-p': And(Use(str.lower),
                  lambda p: str.lower(p) in conf['profiles'],
                  error="PROFILE should be one of {0}".format(', '.join(conf[
                      'profiles']))),
        '-i': And(os.path.isfile,
                  error="INPUT_FILE file {0} does not exist".format(raw_args['-i'])),
        Optional('-o', default='mongodb://localhost:27017/'): And(
            lambda o: str.lower(o).startswith('mongodb://'),
            error="{0} is not a valid mongodb connection string, e.g. mongodb://localhost:27017/"
            .format(raw_args['-o'])),
        Optional('-x'): Or(None, os.path.isfile,
                           error="Parameters file {0} does not exist".format(raw_args['-x'])),
        Optional('--help'): Or(True, False),
        Optional('--version'): Or(True, False),
        Optional('--verbose'): Or(True, False)
    })
    try:
        args = sch.validate(raw_args)
    except DocoptExit as err:
        LOGGER.error("Parse command-line arguments failed!")
        print(err.usage)
    except SchemaError as err:
        LOGGER.error("Validate command-line arguments failed!")
        print(err.autos)
        exit(err)
    return args


def save_route_to(route, collection):
    collection.insert_one(route)


def crawl_route(router, source_x, source_y, target_x, target_y, mongo, params=None, custom_id=0):
    LOGGER.debug("Try searching for a path from (%s, %s) to (%s, %s)",
                 str(source_x), str(source_y), str(target_x), str(target_y))
    res = router.find_path(source_x, source_y, target_x, target_y,
                           params)
    if res is None:
        res = {}
    # The found routes will be stored in MongoDB,
    # `pathman` database, {router} collection
    db = mongo.pathman
    paths = db[router.__class__.__name__]
    LOGGER.debug("Save the found route information to MongDB %s, collection %s",
                 mongo.address, paths.full_name)
    res["date"] = datetime.datetime.utcnow()
    res["cid"] = custom_id
    save_route_to(res, paths)


def main():
    """Entrypoint of command line interface.
    """
    args = docopt(__doc__, version=__version__)
    if args['--verbose']:
        logging.getLogger().setLevel(logging.DEBUG)
        LOGGER.info(
            "==== Path-Man eats paths as Pac-Man eats dots ====")
        LOGGER.info("== Running in verbose mode with DEBUG info ==")
        LOGGER.info("Project page: http://github.com/tumluliu/air-pathman")
        LOGGER.info("Contact: Lu Liu via nudtlliu@gmail.com")
        LOGGER.info("Start working...")
        LOGGER.debug("Arguments for pathman: %s", (str(args)))
    else:
        logging.getLogger().setLevel(logging.WARNING)
    with open('appconf.json', 'r') as f:
        appconf = json.load(f)
    args = validate_arguments(args, appconf)
    LOGGER.debug("Arguments after validation: %s", args)
    router = RoutingServiceFactory(args['-r'], args['-p'])
    LOGGER.debug("Router %s is ready to use", router.__class__.__name__)
    LOGGER.info("Open input data file with origin/destination pairs")
    od_pairs = []
    with open(args['-i'], 'r') as infile:
        LOGGER.debug("Data file %s has been opened for reading", args['-i'])
        dt = csv.DictReader(infile)
        for r in dt:
            LOGGER.debug("Current row in the points info file: %s", str(r))
            od_pairs.append({
                'id': int(r['id']),
                'o_x': float(r['start_lon']),
                'o_y': float(r['start_lat']),
                'd_x': float(r['end_lon']),
                'd_y': float(r['end_lat'])
            })
    LOGGER.info("Load extra parameter file for the current router")
    if args['-x'] is None:
        params = None
    else:
        with open(args['-x']) as f:
            params = json.load(f)
    mongo_client = MongoClient(args['-o'])
    for i, od in enumerate(od_pairs):
        print("Processing progress: {0} / {1}".format(i + 1, len(od_pairs)))
        try:
            crawl_route(router, od['o_x'], od['o_y'],
                        od['d_x'], od['d_y'], mongo_client, params, od['id'])
        except Exception as ex:
            LOGGER.error(
                "Caught exception %s when processing %s", str(ex), str(od))
            with open("errors.txt", "a") as f:
                f.write(str(od))
                f.write('\n')
            continue
    LOGGER.info("All done!")


if __name__ == '__main__':
    main()
