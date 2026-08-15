import http.client as http_client
import re
import xml.etree.ElementTree as ET

from .config import Config, ScanOption
from .state import state
from .utils import fail

# HTTP Post functions

def post_multipart(host, selector, fields, files, exact_response=True):
    """
    Post fields and files to an http host as multipart/form-data.
    fields is a sequence of (name, value) elements for regular form fields.
    files is a sequence of (name, filename, value) elements for data to be uploaded as files
    Return the server's response page.
    """
    # t-k: print a better error message to the log
    try:
        content_type, body = encode_multipart_formdata(fields, files)
        h = http_client.HTTPConnection(host)
        # h.set_debuglevel(1)
        h.putrequest('POST', selector)
        h.putheader('content-type', content_type)
        h.putheader('content-length', str(len(body)))
        h.endheaders()
        h.send(body)
        if exact_response:
            response = h.getresponse()
            return response.read()
        else:
            return None
    except Exception as e:
        raise Exception(f'Problem contacting Scanner over network: {e}')


def encode_multipart_formdata(fields, files):
    """
    fields is a sequence of (name, value) elements for regular form fields.
    files is a sequence of (name, filename, value) elements for data to be uploaded as files
    Return (content_type, body) ready for httplib.HTTP instance
    """
    boundary = b'----------ThIs_Is_tHe_bouNdaRY_$'
    crlf = b'\r\n'
    stream = []
    for (key, value) in fields:
        stream.append(b'--' + boundary)
        stream.append(b'Content-Disposition: form-data; name="%d"' % key)
        stream.append(b'')
        if type(value) != bytes:
            value = value.encode("utf-8")
        stream.append(value)
    for (key, filename, value) in files:
        stream.append(b'--' + boundary)
        stream.append(b'Content-Disposition: form-data; name="%d"; filename="%b"' % (key, filename.encode("utf-8")))
        stream.append(b'Content-Type: application/octet-stream')
        stream.append(b'')
        if type(value) != bytes:
            value = value.encode("utf-8")
        stream.append(value)
    stream.append(b'--' + boundary + b'--')
    stream.append(b'')
    body = crlf.join(stream)
    content_type = b'multipart/form-data; boundary=%b' % boundary
    return content_type, body


def server_register(printing=True):
    config = Config()
    msg = '<?xml version="1.0" encoding="UTF-8" ?>'
    msg += '<root>'
    msg += '<S2PC_Regi UserID="' + config.SERVER_NAME + '" UniqueID="' + state.server_uid + '" RegiType="ADD" />'
    msg += '</root>'

    result = str(post_multipart(config.SCANNER_IP, '/IDS/ScanFaxToPC.cgi', [], [(1, "c:\\IDS.XML", msg)]))
    # print result
    # <?xml version="1.0" encoding="UTF-8"?><root><S2PC_Regi UserID ="W510" Result="ADD_OK" InstanceID="29" /></root>

    m = re.match(r'.*Result="ADD_OK" InstanceID="(\d+)"', result)
    if not m:
        raise NameError("Error registering server: " + result)
    else:
        if printing:
            print(f"Newly registered server '{config.SERVER_NAME}' with UniqueID '{state.server_uid}' has got")
            print(f"    InstanceID '{m.group(1)}'.")  # t-k: better readability and understanding
        return int(m.group(1))


# t-k: restructered function to be real refresh
def server_refresh():
    config = Config()
    old_instance_id = state.server_instance_id
    state.server_instance_id = server_register(printing=False)
    if state.server_instance_id != old_instance_id:
        print(f"Refreshed server '{config.SERVER_NAME}' with UniqueID '{state.server_uid}' has got")
        print("    new InstanceID '" + str(state.server_instance_id) + "'.")
    return state.server_instance_id


# t-k: new function = easier to understand
def server_unregister():
    config = Config()
    unique_id = state.server_uid
    msg = '<?xml version="1.0" encoding="UTF-8" ?>'
    msg += '<root>'
    msg += '<S2PC_Regi UserID="' + state.server_uid + '" UniqueID="' + unique_id + '" RegiType="DELETE" />'
    msg += '</root>'

    result = post_multipart(config.SCANNER_IP, '/IDS/ScanFaxToPC.cgi', [], [(1, "c:\\IDS.XML", msg)])
    # print result
    # <?xml version="1.0" encoding="UTF-8"?>
    #   <root><S2PC_Regi UserID ="server" Result="DELETE_OK" InstanceID="140" /></root>

    if result is None:
        # scanner is unreachable, can't unregister
        return

    m = re.match(b'.*Result="DELETE_OK"', result)
    if not m:
        raise NameError("Error unregistering server: " + result.decode("utf-8"))
    else:
        print(f"Unregistered server '{config.SERVER_NAME}' with UniqueID '{state.server_uid}'.")


def push_server_options():
    config = Config()
    """<?xml version="1.0" encoding="UTF-8" ?>
       <root>
         <S2PC_AppList>
           <List>
            <AppIndex Value="1" />
            <AppName Value="Gray default" />
            <AppType Value="MAC" />
            <Resolution Value="DPI_300" />
            <Color Value="COLOR_GRAY" />
            <FileFormat Value="FORMAT_M_PDF" />
            <ScanSize Value="SIZE_A4" />
            <DuplexScan Value="DUPLEX_OFF" />
            <Orientation Value="ORIENTATION_SIDEWAY" />
          </List>
        </S2PC_AppList>
     </root>"""
    root = ET.Element('root')
    app_list = ET.SubElement(root, 'S2PC_AppList')

    for index, option in enumerate(config.OPTIONS, start=1):
        list_element = ET.SubElement(app_list, 'List')
        ET.SubElement(list_element, 'AppIndex').attrib['Value'] = str(index)
        ET.SubElement(list_element, 'AppName').attrib['Value'] = option["name"]
        ET.SubElement(list_element, 'AppType').attrib['Value'] = 'MAC'
        ET.SubElement(list_element, 'Resolution').attrib['Value'] = option["resolution"]
        ET.SubElement(list_element, 'Color').attrib['Value'] = option["color"]
        ET.SubElement(list_element, 'FileFormat').attrib['Value'] = option["format"]
        ET.SubElement(list_element, 'ScanSize').attrib['Value'] = option["size"]
        ET.SubElement(list_element, 'DuplexScan').attrib['Value'] = "DUPLEX_OFF"
        ET.SubElement(list_element, 'Orientation').attrib['Value'] = "ORIENTATION_SIDEWAY"

    msg = b'<?xml version="1.0" encoding="UTF-8" ?>\r\n' + ET.tostring(root)
    # msg=ET.tostring(root, encoding="UTF-8")
    post_multipart(config.SCANNER_IP, '/IDS/ScanFaxToPC.cgi', [], [(1, "scantopc", msg)], False)


def query_user_options() -> ScanOption:
    config = Config()

    # {'name':'Gray-S_PDF-75','color':'GRAY','resolution':'75','format':'S_PDF','size','a4'}
    # result='<?xml version="1.0" encoding="UTF-8"?><root><S2PC_Select><AppIndex Value="1"/>
    #   <Resolution Value="DPI_300"/><Color Value="COLOR_GRAY"/><FileFormat Value="FORMAT_M_PDF"/>
    #   <ScanSize Value="SIZE_A4"/></S2PC_Select></root>'
    # print result
    
    raw_result = post_multipart(
        config.SCANNER_IP, "/IDS/UserSelect.xml", [], [(1, "scantopc", "")]
    )
    if not raw_result:
        raise RuntimeError("No options selected by user (empty response)")

    # Decode if bytes
    result_str = (
        raw_result.decode("utf-8")
        if isinstance(raw_result, bytes)
        else raw_result
    )

    xml_root = ET.fromstring(result_str)
    s2pc_select = xml_root.find("S2PC_Select")
    if s2pc_select is None:
        raise RuntimeError(f"S2PC_Select tag not found in scanner response: {result_str}")

    def get_val(tag_name: str, default: str | None = None) -> str:
        elem = s2pc_select.find(tag_name)
        if elem is not None and "Value" in elem.attrib:
            return elem.attrib["Value"]
        elif elem is not None and "value" in elem.attrib:
            return elem.attrib["value"]
        elif default is not None:
            return default
        raise RuntimeError(f"Tag or Value attribute for '{tag_name}' not found in XML: {result_str}")

    index_str = get_val("AppIndex")
    index = int(index_str) - 1

    # Copy template option from config
    user_options = config.OPTIONS[index].copy()

    # Override with options returned by scanner if present
    user_options["color"] = get_val("Color", user_options.get("color"))
    user_options["resolution"] = get_val(
        "Resolution", user_options.get("resolution")
    )
    user_options["format"] = get_val("FileFormat", user_options.get("format"))
    user_options["size"] = get_val("ScanSize", user_options.get("size"))

    return user_options
