from .config import Config


def query_snmp_variable(ip, oid):
    from pysnmp.entity.rfc3413.oneliner import cmdgen

    error_indication, error_status, _error_index, var_binds = cmdgen.CommandGenerator().getCmd(
        cmdgen.CommunityData('my-agent', 'public', 0),
        cmdgen.UdpTransportTarget((ip, 161)),
        oid)

    return_value = None
    if error_indication:
        raise NameError(f'Error indication in SNMP query: {error_indication}')  # t-k: %s to avoid TypeError
    elif error_status:
        raise NameError(f'Error status in SNMP query: {error_status}')  # t-k: %s to avoid TypeError
    else:
        return_value = var_binds
    return return_value


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

