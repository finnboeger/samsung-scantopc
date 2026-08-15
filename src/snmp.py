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


async def _async_query_snmp(ip: str, oid_str: str):
    snmp_engine = SnmpEngine()
    transport = await UdpTransportTarget.create((ip, 161), timeout=2.0, retries=1)
    
    iterator = get_cmd(
        snmp_engine,
        CommunityData("public", mpModel=0),
        transport,
        ContextData(),
        ObjectType(ObjectIdentity(oid_str)),
    )
    
    error_indication, error_status, _error_index, var_binds = await iterator
    snmp_engine.close_dispatcher()
    
    if error_indication:
        raise NameError(f"Error indication in SNMP query: {error_indication}")
    elif error_status:
        raise NameError(f"Error status in SNMP query: {error_status}")
        
    return var_binds

def query_snmp_variable(ip, oid):
    if isinstance(oid, (tuple, list)):
        oid = ".".join(str(x) for x in oid)
    return asyncio.run(_async_query_snmp(ip, str(oid)))


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

