import asyncio

from pysnmp.hlapi.v3arch import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
)

from .config import Config

# The scan loop polls the printer once per second. Creating a new SnmpEngine,
# UDP transport and asyncio event loop for every single query is expensive
# (the engine reloads its MIB modules each time), so all of them are created
# once per process and reused. On unexpected errors everything is reset and
# rebuilt on the next call.
_loop: asyncio.AbstractEventLoop | None = None
_engine: SnmpEngine | None = None
_community: CommunityData | None = None
_context: ContextData | None = None
_transports: dict[str, UdpTransportTarget] = {}


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


def _reset():
    global _engine, _community, _context
    if _engine is not None:
        try:
            _engine.close_dispatcher()
        except Exception:
            pass
    _engine = None
    _community = None
    _context = None
    _transports.clear()


async def _async_query_snmp(ip: str, oid_str: str):
    global _engine, _community, _context
    if _engine is None:
        _engine = SnmpEngine()
        _community = CommunityData("public", mpModel=0)
        _context = ContextData()

    transport = _transports.get(ip)
    if transport is None:
        transport = await UdpTransportTarget.create((ip, 161), timeout=2.0, retries=1)
        _transports[ip] = transport

    error_indication, error_status, _error_index, var_binds = await get_cmd(
        _engine,
        _community,
        transport,
        _context,
        ObjectType(ObjectIdentity(oid_str)),
    )

    if error_indication:
        raise NameError(f"Error indication in SNMP query: {error_indication}")
    elif error_status:
        raise NameError(f"Error status in SNMP query: {error_status}")

    return var_binds


def query_snmp_variable(ip, oid):
    if isinstance(oid, (tuple, list)):
        oid = ".".join(str(x) for x in oid)
    loop = _get_loop()
    try:
        return loop.run_until_complete(_async_query_snmp(ip, str(oid)))
    except NameError:
        # Regular SNMP error (e.g. timeout): engine is still fine, just re-raise
        raise
    except Exception:
        # Anything unexpected: rebuild engine/transport on the next call
        _reset()
        raise


def query_printer_scan_status(instance_id):
    config = Config()
    # t-k: more descriptive Error handling and logging
    try:
        result = query_snmp_variable(config.SCANNER_IP, (1, 3, 6, 1, 4, 1, 236, 11, 5, 11, 81, 11, 7, 2, 1, 2, instance_id))
        # (ObjectName('1.3.6.1.4.1.236.11.5.11.81.11.7.2.1.2.29'), OctetString('\x00\x00\x00\x00'))
        return result[0][1][0]
    except Exception as e:
        if 'result' not in locals():
            result = None
        raise Exception("Could not query printer scan status.\n" + ' ' * 4 +
                         f"Result was '{result}'.\n" + ' ' * 4 +
                         f"Error message: {e}.")
