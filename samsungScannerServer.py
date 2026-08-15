#!/usr/bin/env python3
# samsungScannerServer.py
# Tool to interact with the "scan to PC" option in Samsung MFP like the CLX 3300
#
# Copyright (C) 2022-2023 Steffen Klee
# Copyright (C) 2012-2013 angelnu & Totally King
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import atexit
import logging
import multiprocessing
import os
import os.path
import sys
import time

from PIL import Image

from .src import __version__
from .src.config import Config
from .src.http_comms import server_register, server_unregister
from .src.log import LogFile, listener_configurer, listener_process, worker_configurer
from .src.proxy import exit_proxies, start_proxies
from .src.scanning import get_sane_instance, scan_and_save, scann_worker
from .src.utils import del_pid_file, server_uid_gen

"""
Summary of messages exchanged in order to scan

register server: server -> scanner (HTTP POST) with:
<?xml version="1.0" encoding="UTF-8" ?>
<root>
<S2PC_Regi UserID="Server-XP" UniqueID="ac16b1c1824380e7" RegiType="ADD" />
</root>

scanner answer:
<?xml version="1.0" encoding="UTF-8"?><root><S2PC_Regi UserID ="Server-XP" Result="ADD_OK" InstanceID="27" /></root>

query SNMP 1,3,6,1,4,1,236,11,5,11,81,11,7,2,1,2,<InstanceID> until is we get "1" in the first byte (user selected the
server to scan).
Samsung Windows driver does this every 1/2 second

send configuration options to scan: sever -> scanner (HTTP Post) with:
<?xml version="1.0" encoding="UTF-8" ?>
<root>
    <S2PC_AppList>
        <List>
            <AppIndex Value="1" />
            <AppName Value="My Documents" />
            <AppType Value="MAC" />
            <Resolution Value="DPI_300" />
            <Color Value="COLOR_GRAY" />
            <FileFormat Value="FORMAT_M_PDF" />
            <ScanSize Value="SIZE_A4" />
            <DuplexScan Value="DUPLEX_OFF" />
            <Orientation Value="ORIENTATION_SIDEWAY" />
        </List>
    </S2PC_AppList>
</root>

scanner answer:
closes connection

query SNMP 1,3,6,1,4,1,236,11,5,11,81,11,7,2,1,2,<InstanceID> until is we get "2" in the first byte (user has selected
scan options based on the offered template).

start a scan using SANE

register server again

if we want to unregister the server: sever -> scanner (HTTP Post) with:
<?xml version="1.0" encoding="UTF-8" ?>
<root>
<S2PC_Regi UserID="Server-XP" UniqueID="ac16b1c1824380e7" RegiType="DELETE" />
</root>

"""



# ---------------------------------------------------------------------------
# Configuration — read entirely from environment variables.
# An optional Python file (OPTIONS_FILE) may be mounted for advanced scan
# option definitions that include filter functions.
# ---------------------------------------------------------------------------
HOME_DIR = os.getenv("HOME", os.path.expanduser("~"))


# ############################## LOGGING ################################

if __name__ == '__main__':
    config = Config()

    # Daemon mode
    if config.CLI_OPTIONS.daemon:
        try:
            pid = os.fork()
            if pid > 0:
                # exit from second parent
                sys.exit(0)
        except OSError:
            logging.exception("Error forking daemon")
            sys.exit(1)

    # t-k: Logging supporting multiprocessing
    logQ = multiprocessing.Queue()
    listener = multiprocessing.Process(target=listener_process,
                                       args=(logQ, listener_configurer))
    listener.start()

    worker_configurer(logQ)

    sys.stdout = LogFile('stdout')
    sys.stderr = LogFile('stderr')


    def exit_listener():
        logQ.put_nowait(None)


    # Print version and active configuration
    print("###########################")
    print("# Initiating version " + __version__)
    print("###########################")

    print('At program termination joining log listener process with:\n' + ' ' * 4 +
          str(atexit.register(exit_listener)))

    # Log active configuration
    print("Configuration loaded from environment variables:")
    _env_config_vars = [
        'ENABLED_SERVER',
        'SCANNER_SANE_NAME', 'SERVER_NAME',
        'OWNER_UID', 'OWNER',
        'SCAN_OUTPUT_DIR', 'SCAN_FILENAME_TEMPLATE',
        'MODIFIED_SANE', 'PROXY_DEBUGLEVEL', 'SCANNER_CACHING',
        'LOG_NAME', 'LOG_MAXBYTES', 'LOG_BACKUPCOUNT',
        'OPTIONS_FILE', 'SCAN_OPTIONS',
        'SIZE2SANE', 'MODES2SANE',
    ]
    for _var in _env_config_vars:
        _val = os.environ.get(_var, '(not set — using default)')
        print(f"CONFIG: {_var}={_val}")

    # Debug mode
    if config.CLI_OPTIONS.imageFiles:
        print("Running in debug mode!")
        HOME_DIR = "/tmp/"
        imgs = []
        for imageFile in config.CLI_OPTIONS.imageFiles:
            imgs.append(Image.open(imageFile))
        scan_and_save(config.OPTIONS[config.CLI_OPTIONS.optionsIndex], imgs)
        sys.exit(0)

    # Daemon mode
    if config.CLI_OPTIONS.daemon and config.CLI_OPTIONS.pidfile:
        print("Write PID to file: " + config.CLI_OPTIONS.pidfile)
        print('At program termination removing PID file (if it still exists and not caught SIGQUIT) with:\n' +
              ' ' * 4 + str(atexit.register(del_pid_file)))
        pid = str(os.getpid())
        with open(config.CLI_OPTIONS.pidfile, 'w+') as f:
            f.write(f"{pid}\n")

# ######################### AUTO CONFIGURATION ##########################

# t-k: some automatic configuration providing default values
#     which takes place if values were not given in conf file

print('The following was automatically configured.')

if __name__ == '__main__':
    # Is enabled?
    if not config.ENABLED_SERVER:
        print("Server not enabled")
        sys.exit(0)


    # Calculate "ID" based on SERVER_NAME and hostname
    # t-k: use md5 hashing to get real unique IDs that take into account
    #     the whole strings rather than just the last 8 letters
    SERVER_UID = server_uid_gen()

    while True:
        try:
            SERVER_INSTANCE_ID = server_register()
        except Exception:
            logging.exception("Network or scanner not available: waiting 10s and trying again ...")
            time.sleep(10)  # Wait 10 seconds
        else:
            break

    # Unregister #t-k: with new function - easier to understand
    print('At program termination unregistering server with:\n' + ' ' * 4 +
          str(atexit.register(server_unregister)))

    # t-k: initiate queues for communication with subprocesses and
    #     start the proxy server processes
    if config.MODIFIED_SANE:
        start_proxies()
        
        print('At program termination joining proxy processes with:\n' + ' ' * 4 +
              str(atexit.register(exit_proxies)))

    # angelnu Test the Sane connection (also works as a chache to be ready at scan time)
    # t-k: can only do this after proxies are established if modified sane method is used
    #     + only applicable if one server is used
    if config.SCANNER_CACHING and not get_sane_instance():
        print("Could not connect to Scanner (" + config.SCANNER_SANE_NAME + ") via SANE.", file=sys.stderr)
        sys.exit(1)

    # Main program: keep scanning
    while True:
        try:
            scann_worker()
        except Exception:
            logging.exception("Something awful happened! Waiting 10 seconds before trying again.")
            time.sleep(10)  # Wait 10 seconds
