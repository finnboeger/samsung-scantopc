
import os
import platform
import signal
import sys
from typing import NoReturn


def del_pid_file(pidfile: str):
    from .state import state
    # t-k: delete PID only if it exists and if not caught signal SIGQUIT (3, with Strg+\)
    if state.caught_sigquit:
        print('Did not remove PID file: ' + pidfile + '\n' + ' ' * 4 +
              'because of the SIGQUIT signal caught.')
    else:
        if os.path.exists(pidfile):
            os.remove(pidfile)
            print('Removed PID file: ' + pidfile)
        else:
            print('Could not remove PID file: ' + pidfile + '\n' + ' ' * 4 +
                  "because it was already deleted (probably by 'sudo service samsungScannerServer stop').")


def server_uid_gen(servername: str):
    """
    generate a UniqueID for this server based on SERVER_NAME and hostname using md5 as hash method
    """
    from hashlib import md5
    hostname = platform.node()

    def hash2half_length2int(hash_string):
        """
        convert hash string to half its length
           watch out: returns int (not str)
        """
        half_length = int(len(hash_string) / 2)
        part1 = hash_string[:half_length]
        part2 = hash_string[half_length:]
        half_hash_int = int(part1, 16) + int(part2, 16)
        # restrict to max. 16 (hex) characters
        half_hash_int %= (256 ** 8)
        return half_hash_int

    # use md5 as hash method -> length 32
    server_hash = md5(servername.encode('utf-8')).hexdigest()
    host_hash = md5(hostname.encode('utf-8')).hexdigest()
    server_hash_half_int = hash2half_length2int(server_hash)
    host_hash_half_int = hash2half_length2int(host_hash)
    server_uid_int = server_hash_half_int + host_hash_half_int
    # restrict to max. 16 (hex) characters
    server_uid_int %= (256 ** 8)
    # convert to hex
    return hex(server_uid_int).replace("0x", "").replace("L", "")


# t-k: handle some signals to trigger normal exit (and atexit then triggers its own stuff (delPID, server_unregister))
def sig_handler(signum, stack=None):
    from .state import state
    sig = convSignum2Sig[signum]
    if sig in ['SIGHUP', 'SIGINT', 'SIGQUIT', 'SIGTERM']:
        exit_code = convSig2exitCode.get(sig, 1)
        print(f"Caught signal {signum} '{sig}', exiting with code {exit_code} ...")
        if sig == 'SIGQUIT':
            state.caught_sigquit = True
        sys.exit(exit_code)
    # for other signals
    else:
        pass


# t-k: set up dictionary to convert from signal number (e.g. 15) to signal (e.g. 'SIGTERM')
convSignum2Sig = {}
# t-k: set up dictionary to convert from signal (e.g. 'SIGTERM') to proper exit code (e.g. 143)
convSig2exitCode = {
    'SIGINT': 1,
    'SIGQUIT': 131,
    'SIGTERM': 143,
    'SIGHUP': 129,
}

# t-k: handle SIG... signals correctly (like SIGTERM)
#     thus also handles 'sudo service samsungScannerServer stop'

# ~ for i in [x for x in dir(signal) if x.startswith("SIG") and not x.startswith('SIG_')]:
for i in ['SIGINT', 'SIGQUIT', 'SIGTERM', 'SIGHUP']:
    try:
        signum = getattr(signal, i)
        signal.signal(signum, sig_handler)
        convSignum2Sig[signum] = i
    except RuntimeError:
        pass  # t-k: do not consider signals like SIGKILL, which cannot be handled (by definition)


def fail(reason: str, error_constructor: type[Exception] = Exception) -> NoReturn:
    raise error_constructor(reason)