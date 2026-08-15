import errno
import json
import os
import platform
import pwd
import re
import socket
import sys
import time
from optparse import OptionGroup, OptionParser, Values
from string import Template
from typing import Any, Callable, TypedDict

import sane

from . import __version__


def _env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        print(
            "Warning: invalid integer for %s='%s', using default %d" % (name, val, default),
            file=sys.stderr,
        )
        return default


# File-format → extension translation table
EXTENSIONS = {
    'FORMAT_S_PDF': 'pdf',
    'FORMAT_M_PDF': 'pdf',
    'FORMAT_PDF': 'pdf',
    'FORMAT_JPEG': 'jpg',
    'FORMAT_S_TIFF': 'tiff',
    'FORMAT_M_TIFF': 'tiff',
}

# Parse options
parser = OptionParser(usage="usage: %prog [options]",
                      version="%prog " + __version__)
parser.add_option("-d", "--daemon", action='store_true', dest="daemon",
                  help="Fork a daemon")
parser.add_option("-p", "--pidfile", dest="pidfile",
                  help="File to write the daemon PID")

group = OptionGroup(parser, "Debug Options",
                    "Caution: use these options at your own risk.  "
                    "These options are expected to be only for debugging. ")

group.add_option("--imageFiles", action="append", dest="imageFiles",
                 help="Image files to process instead of scanning. When this option is used the program " +
                      "will apply the selected filters, store the result and terminate.")
group.add_option("--optionsIndex", type="int", dest="optionsIndex", default=0,
                 help="What of the OPTIONS[] to use for processing the --imageFiles.")

parser.add_option_group(group)


ScanFilter = Callable[..., Any]


class ScanOption(TypedDict):
    name: str
    color: str
    resolution: str
    format: str
    size: str
    output: str
    filters: list[ScanFilter]


class Config:
    """Singleton class to manage configuration settings."""

    _instance: "Config | None" = None

    ENABLED_SERVER: bool
    MODIFIED_SANE: bool
    PROXY_DEBUGLEVEL: int
    SCANNER_CACHING: bool
    SCANNER_SANE_NAME: str
    SERVER_NAME: str
    OWNER_UID: int
    OWNER: str

    MODES2SANE: dict[str, str]
    SIZE2SANE: dict[str, str]
    OPTIONS: list[ScanOption]
    CLI_OPTIONS: Values
    

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.update()
            cls._instance.read_cli_options()
        return cls._instance


    def update_owner_info(self) -> None:
        """Update owner UID and username from environment variables."""
        env_owner_uid = _env_int('OWNER_UID', None)
        if env_owner_uid is not None:
            self.OWNER_UID = env_owner_uid
            self.OWNER = pwd.getpwuid(self.OWNER_UID).pw_name
            print_autoconfig(self.OWNER, 'OWNER')
        else:
            env_owner = os.environ.get('OWNER')
            if env_owner:
                self.OWNER = env_owner
                self.OWNER_UID = pwd.getpwnam(env_owner).pw_uid
                print_autoconfig(self.OWNER_UID, 'OWNER_UID')
            else:
                self.OWNER_UID = 1000  # t-k: first ubuntu user as default
                print_autoconfig(self.OWNER_UID, 'OWNER_UID')
                self.OWNER = pwd.getpwuid(self.OWNER_UID).pw_name
                print_autoconfig(self.OWNER, 'OWNER')


    def update_scanner_name(self) -> None:
        scanner_sane_name = os.environ.get('SCANNER_SANE_NAME')

        # t-k: Get scanner name automatically, try again if nothing found (e.g. no network connection)
        while scanner_sane_name is None:
            print("Init SANE ...")
            sane.init()  # t-k: bugfix, can't find any devs without init
            devs = sane.get_devices()
            for dev in devs:
                if dev[1].upper() == 'SAMSUNG':
                    scanner_sane_name = dev[0]
                    print_autoconfig(scanner_sane_name, 'SCANNER_SANE_NAME')
                    break
            if devs:
                tmpinsert = ' SAMSUNG'
            else:
                tmpinsert = ''
            sys.stderr.write('No%s Scanner found. Trying again in 30s.\n' % tmpinsert)
            time.sleep(30)

        self.SCANNER_SANE_NAME = scanner_sane_name


    def update_server_name(self) -> None:
        env_server_name = os.environ.get('SERVER_NAME')

        if env_server_name is not None:
            self.SERVER_NAME = env_server_name
        else:
            self.SERVER_NAME = platform.node()  # t-k: = hostname
            print_autoconfig(self.SERVER_NAME, 'SERVER_NAME')


    def update_mode2sane(self) -> None:
        self.MODES2SANE = {
            'COLOR_MONO': 'Black and White - Line Art',
            'COLOR_GRAY': 'Grayscale - 256 Levels',
            'COLOR_TRUE': 'Color - 16 Million Colors',
        }

        # Colour-mode → SANE mode translation table
        modes2sane_env = os.environ.get('MODES2SANE')
        if modes2sane_env:
            try:
                self.MODES2SANE = json.loads(modes2sane_env)
            except json.JSONDecodeError as e:
                print("Warning: could not parse MODES2SANE JSON: %s" % e, file=sys.stderr)


    def update_size2sane(self) -> None:
        # SIZE2SANE: auto-configured from the device later when not provided here
        _size2sane_env = os.environ.get('SIZE2SANE')
        if _size2sane_env:
            try:
                self.SIZE2SANE = json.loads(_size2sane_env)
            except json.JSONDecodeError as e:
                print("Warning: could not parse SIZE2SANE JSON: %s" % e, file=sys.stderr)


    def update_options(self) -> None:
        # OPTIONS: loaded from a mounted Python file, a JSON env var, or built-in defaults.
        # The Python file may define filter functions; the JSON path cannot.
        _OPTIONS_FILE = os.environ.get('OPTIONS_FILE', '/etc/samsungScannerServer.options.py')
        if os.path.exists(_OPTIONS_FILE):
            try:
                # Isolated namespace to capture definitions from the external file
                local_namespace: dict[str, Any] = {}
                with open(_OPTIONS_FILE) as f:
                    exec(
                        compile(f.read(), _OPTIONS_FILE, "exec"),
                        {},
                        local_namespace,
                    )

                if "OPTIONS" not in local_namespace:
                    raise KeyError(
                        f"'{_OPTIONS_FILE}' does not define an 'OPTIONS' variable."
                    )

                self.OPTIONS = local_namespace["OPTIONS"]
                print("Loaded OPTIONS from file: %s" % _OPTIONS_FILE)
            except Exception as e:
                print(
                    "Error loading OPTIONS file '%s': %s"
                    % (_OPTIONS_FILE, e),
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            _options_env = os.environ.get('SCAN_OPTIONS')
            if _options_env:
                try:
                    _options_raw = json.loads(_options_env)
                    self.OPTIONS = []
                    for _opt in _options_raw:
                        self.OPTIONS.append({
                            'name': _opt.get('name', 'Unnamed'),
                            'color': _opt.get('color', 'COLOR_GRAY'),
                            'resolution': _opt.get('resolution', 'DPI_300'),
                            'format': _opt.get('format', 'FORMAT_M_PDF'),
                            'size': _opt.get('size', 'SIZE_A4'),
                            'output': _opt.get('output', self.OUTPUT_PREFIX),
                            'filters': [],
                        })
                except json.JSONDecodeError as e:
                    print("Error parsing SCAN_OPTIONS JSON: %s" % e, file=sys.stderr)
                    sys.exit(1)
            else:
                # Built-in defaults — covers the most common use-cases out of the box
                self.OPTIONS = [
                    {'name': 'Gray-M_PDF-300',  'color': 'COLOR_GRAY', 'resolution': 'DPI_300', 'format': 'FORMAT_M_PDF', 'size': 'SIZE_A4', 'output': self.OUTPUT_PREFIX, 'filters': []},
                    {'name': 'Color-M_PDF-300', 'color': 'COLOR_TRUE', 'resolution': 'DPI_300', 'format': 'FORMAT_M_PDF', 'size': 'SIZE_A4', 'output': self.OUTPUT_PREFIX, 'filters': []},
                    {'name': 'Gray-JPEG-300',   'color': 'COLOR_GRAY', 'resolution': 'DPI_300', 'format': 'FORMAT_JPEG',  'size': 'SIZE_A4', 'output': self.OUTPUT_PREFIX, 'filters': []},
                    {'name': 'Color-JPEG-300',  'color': 'COLOR_TRUE', 'resolution': 'DPI_300', 'format': 'FORMAT_JPEG',  'size': 'SIZE_A4', 'output': self.OUTPUT_PREFIX, 'filters': []},
                    {'name': 'Gray-M_PDF-75',   'color': 'COLOR_GRAY', 'resolution': 'DPI_75',  'format': 'FORMAT_M_PDF', 'size': 'SIZE_A4', 'output': self.OUTPUT_PREFIX, 'filters': []},
                    {'name': 'Gray-S_PDF-75',   'color': 'COLOR_GRAY', 'resolution': 'DPI_75',  'format': 'FORMAT_S_PDF', 'size': 'SIZE_A4', 'output': self.OUTPUT_PREFIX, 'filters': []},
                ]


    def update_scanner_ip(self) -> None:
        # t-k: updated IP extraction method (thanks to frankentux)
        try:
            self.SCANNER_IP = extractIPs(self.SCANNER_SANE_NAME)[0]
        except IndexError:  # regex failed?
            print("Couldn't recognize IPv4 of scanner '%s'." % self.SCANNER_SANE_NAME, file=sys.stderr)
            sys.exit(1)
        print_autoconfig(self.SCANNER_IP, 'SCANNER_IP')
        self.REAL_SCANNER_IP = self.SCANNER_IP  # t-k: preserve IP if changed by MODIFIED_SANE method


    def update_modified_sane(self) -> None:
        # t-k: get own server IP and change scanner name to include that
        #     (so later sane connects to scanner via proxy not directly)
        print('Getting server IP and setting scanner name so that SANE uses proxy.')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        while True:
            try:
                sock.connect((self.SCANNER_IP, 80))
            except socket.error as e:
                if e.errno == errno.EALREADY:  # operation already in progress
                    time.sleep(1)
                    continue
                if isinstance(e, socket.timeout):
                    sock.close()
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2.0)
                sys.stderr.write('Problem contacting Scanner over network: %s, retrying in 10s ...\n' % e)
                time.sleep(10)
            else:
                break
        # only way to get server IP without knowing network device name
        #     which IP connects to scanner? (/etc/hosts not working because: hostname 127.0.0.1)
        self.SERVER_IP = sock.getsockname()[0]
        sock.close()
        del sock
        print_autoconfig(self.SERVER_IP, 'SERVER_IP')
        self.SCANNER_SANE_NAME = ' '.join(self.SCANNER_SANE_NAME.split(' ')[:-1] + [self.SERVER_IP])
        print_autoconfig(self.SCANNER_SANE_NAME, 'SCANNER_SANE_NAME')
    

    def ensure_output_directory_exists(self) -> None:
        # t-k: check to see if scanning directory exists to create it if neccessary
        # angelnu: support scanning outside home
        dirsToMake = Template(self.OUTPUT_PREFIX).safe_substitute(homedir=self.HOME_DIR).split('/')[1:-1]
        for i in range(1, len(dirsToMake)):
            dirToMake = "/" + "/".join(dirsToMake[0:i + 1])
            if os.path.lexists(dirToMake):
                if not os.path.isdir(dirToMake):
                    raise OSError('Invalid OUTPUT_PREFIX given in configuration file.\n' +
                                ' ' * 9 + 'The path specified exists, but is not a directory!\n' +
                                ' ' * 9 + "You should either change OUTPUT_PREFIX or check '%s' and move or rename it."
                                % dirToMake)
            else:
                os.mkdir(dirToMake)
                uid = int(self.OWNER_UID)
                gid = pwd.getpwuid(uid).pw_gid
                os.chown(dirToMake, uid, gid)
                print("Created the directory '%s'." % dirToMake)


    def update(self) -> None:
        """Update configuration settings from environment variables."""
        # Core feature flags
        self.ENABLED_SERVER = _env_bool('ENABLED_SERVER', True)
        self.MODIFIED_SANE = _env_bool('MODIFIED_SANE', False)
        self.PROXY_DEBUGLEVEL = _env_int('PROXY_DEBUGLEVEL', 1)
        self.SCANNER_CACHING = _env_bool('SCANNER_CACHING', True)

        # Scanner / server identity (both optional — auto-detected when absent)
        self.update_scanner_name()
        self.update_server_name()

        # Owner of produced scan files
        self.update_owner_info()

        # Output path: directory + filename template are joined to form OUTPUT_PREFIX
        self.OUTPUT_PREFIX = os.path.join(
            os.environ.get('SCAN_OUTPUT_DIR', '/scans'),
            os.environ.get('SCAN_FILENAME_TEMPLATE', 'SCAN_${date}__${uid}')
        )

        # Logging
        self.LOG_NAME = os.environ.get('LOG_NAME', '/var/log/samsungScannerServer.log') or None
        self.LOG_MAXBYTES = _env_int('LOG_MAXBYTES', 100000)
        self.LOG_BACKUPCOUNT = _env_int('LOG_BACKUPCOUNT', 1)

        
        self.update_mode2sane()
        self.update_size2sane()
        
        self.update_options()

        # t-k: always automatically retrieve home dir and extract IP
        self.HOME_DIR = pwd.getpwuid(self.OWNER_UID).pw_dir
        print_autoconfig(self.HOME_DIR, 'HOME_DIR')

        self.update_scanner_ip()

        if (self.MODIFIED_SANE):
            self.update_modified_sane()

        self.ensure_output_directory_exists()


    def read_cli_options(self) -> None:
        (options, args) = parser.parse_args()
        if len(args) != 0:
            parser.error("incorrect number of arguments")
        self.CLI_OPTIONS = options


# t-k: Automatic configuration -> Log
def print_autoconfig(variable, variable_name, no_quotes=False):
    """
    add automatically configured VARIABLE to a list that is later printed to log
    """
    if no_quotes:
        quote = ""
    else:
        quote = "'"
    print("AUTOCONFIG: %(variable_name)s = %(quote)s%(variable)s%(quote)s" % locals())


# t-k: function to extract valid IPv4s
def extractIPs(file_content):
    re_ip = r"((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)([ (\[]?(\.|dot)[ )\]]?(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3})"
    ips = [each[0] for each in re.findall(re_ip, file_content)]
    # print(ips)
    for item in ips:
        location = ips.index(item)
        ip = re.sub(r"[ ()\[\]]", "", item)
        ip = re.sub("dot", ".", ip)
        ips.remove(item)
        ips.insert(location, ip)
    return ips
