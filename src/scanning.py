import datetime
import io
import os
import pwd
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from string import Template
from urllib import request

import sane
from pypdf import PdfReader, PdfWriter

from .config import EXTENSIONS, Config, print_autoconfig
from .http_comms import push_server_options, query_user_options, server_refresh
from .proxy import exit_proxies, modsaneopen, start_proxies
from .snmp import query_printer_scan_status
from .state import state

# angelnu: my scanner takes very long to find -> cache
config = Config()

# t-k: method to automatically determine translation from scanner command (received by server) to sane command
#     was written for sizes but may be adapted to other translations
def autoconfig_dic(dic_name, xml_key, preferred):
    if dic_name not in globals():
        try:
            # t-k: get available options from XML file that may be received by server
            capxmlfile = request.urlopen(f'http://{config.REAL_SCANNER_IP}/IDS/CAP.XML')
            capxmldata = capxmlfile.read()
            capxmlfile.close()
            xmlroot = ET.fromstring(capxmldata)
            sizes = []
            for size in xmlroot.iter(xml_key):
                sizes.append(size.attrib['ID'])
            # t-k: get available size options for SANE device
            sane_sizes = get_sane_instance()["page_format"].constraint
            # t-k: match these two sets together and save as dic_name (e.g. SIZE2SANE)
            dic = {}
            for sizeID in sizes:
                size_lst = sizeID.split('_')[1:]  # t-k: may be > 1 part, e.g. ['B5', 'JIS']
                for saneSize in sane_sizes:
                    flag = True
                    for sizeElm in size_lst:
                        flag = flag and sizeElm.lower() in saneSize.lower()
                    # t-k: save combination if all parts match sane size and
                    #     rotated takes precedence over non-rotated
                    if flag and (sizeID not in dic or preferred in saneSize.lower()):
                        dic[sizeID] = saneSize
            if dic == {}:
                raise ValueError(f'{dic_name} dictionary must not be empty!')
            globals()[dic_name] = dic
            print_autoconfig(dic, dic_name, no_quotes=True)
        except Exception as e:
            print('Error while trying to configure scanning options:', file=sys.stderr)
            print(f'    {type(e).__name__}: {e}', file=sys.stderr)
            print(f"You should manually configure {dic_name}.", file=sys.stderr)
            sys.exit(1)


def get_sane_instance() -> sane.SaneDev:
    if state.sane_singleton:
        return state.sane_singleton
    else:
        print("Init SANE ...")
        sane.init()

        print("Connecting to scanner ...")
        while True:
            try:
                # t-k: use modified open method to use modified sane classes
                sane_instance = modsaneopen(config.SCANNER_SANE_NAME) \
                    if config.MODIFIED_SANE \
                    else sane.open(config.SCANNER_SANE_NAME)
                state.sane_singleton = sane_instance

                # t-k: if SIZE2SANE / ... haven't been given in config file, try to automatically configure them
                # t-k: f.l.t.r. -> name of dict, xml key, preferred sane option
                autoconfig_dic('SIZE2SANE', 'Size', 'rotated')
                print("Connected to scanner.")
                return sane_instance
            except Exception as e:
                if config.MODIFIED_SANE and e.message.startswith('no such scan device'):
                    print("Proxy scan 'device' not found, restarting proxies and trying again ...", file=sys.stderr)
                    # t-k: restart proxies
                    exit_proxies()
                    start_proxies()
                else:
                    print('Problem connecting to scanner, trying again in 10s ...', file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                    time.sleep(10)

        


def scan_and_save(user_selection, imgs=None):
    config = Config()

    # t-k: to raise file index independent of file extension
    #     so no more same file names with different extensions
    def exists_file_with_other_extension(base_filename):
        from glob import glob
        search_pattern = base_filename + '.*'
        return bool(glob(search_pattern))

    # t-k: change ownership of scan file
    def chown_file(filename):
        uid = config.OWNER_UID
        gid = pwd.getpwuid(uid).pw_gid
        os.chown(filename, uid, gid)

    # print 'Device options:   ', s.get_options()
    # print 'Device parameters:', s.get_parameters()

    mode = config.MODES2SANE[user_selection["color"]]
    print("MODE: " + mode)
    dpi = int(user_selection["resolution"].replace('DPI_', ''))
    print("DPI: " + str(dpi))
    size = config.SIZE2SANE[user_selection["size"]]
    print("SIZE: " + size)

    # Initialize scan

    def init_scan():
        print("Scanning ...")
        s = get_sane_instance()
        s.mode = mode
        s.resolution = dpi
        s.page_format = size  # t-k: bugfix page_format is correct (not page-format)
        imgs = s.multi_scan()
        return imgs, s

    if not imgs:
        imgs, _s = init_scan()

    # Process images
    output_files = []
    index = 1
    now = datetime.datetime.now()
    while True:
        try:
            for im in imgs:
                file_exists = True
                while file_exists:
                    base_filename = Template(user_selection["output"])\
                        .safe_substitute(date=now.strftime("%Y-%m-%d"), 
                                         datetime=now.strftime("%Y-%m-%d %H-%M-%S"),
                                         uid=f"{index:02d}",  # t-k: index formatted with padding zero
                                         homedir=config.HOME_DIR)  # t-k: automatically detect home dir ('~')
                    filename = base_filename + '.' + EXTENSIONS[user_selection["format"]]  # t-k: seperate base_filename
                    file_exists = exists_file_with_other_extension(
                        base_filename)  # t-k: raise index independent of file extension
                    index += 1
                # t-k: rotate image if necessary
                if re.match('.*rotate', size, re.IGNORECASE):
                    im = im.rotate(270)
                # t-k: print log of applying user filters only if there are any
                if len(user_selection['filters']):
                    print("Applying user filters to " + filename + " ...")
                    for userFilter in user_selection['filters']:
                        im = userFilter(im)  # t-k: replaced img with im
                print("Saving " + filename + " ...")
                im.info['dpi'] = (dpi, dpi)
                im.info['resolution'] = (dpi, dpi)
                im.save(filename, dpi=(dpi, dpi), resolution=dpi)
                chown_file(filename)  # t-k: change ownership of scan file
                print("Done.")
                output_files.append(filename)
        except Exception as e:
            if e == 'Error during device I/O':
                if config.MODIFIED_SANE:
                    print('SANE ' + str(e) + '. Restarting proxies and retrying ...', file=sys.stderr)
                    exit_proxies()
                    start_proxies()
                else:
                    print('SANE ' + str(e) + '. Retrying ...', file=sys.stderr)
                # s.close() # <- this causes seg fault
                state.sane_singleton = None
                imgs, _s = init_scan()
            else:
                print('Whoops! Problem scanning (maybe version Samsung device driver >= 4.1 and multi-scan?):',
                      file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                break
        else:
            break

    # t-k: if more than one server is used on the same scanner
    #     does not yet work, since closing the session raises a seg fault
    #     maybe poor programming of the C based sane extension?
    #     (not incrementing reference count when new references to scanner object are created?)
    if not config.SCANNER_CACHING:
        # t-k: end session with scanner and reset cache
        # s.close() # <- this causes seg fault
        state.sane_singleton = None
        # print('Scanner session closed and cache reset.')
        print('Scanner cache reset.')

    # Concat PDFs and delete temp ones
    if (user_selection["format"] == "FORMAT_M_PDF" or user_selection["format"] == "FORMAT_PDF") and (
            len(output_files) > 1):
        print("Concatenating PDF files ...")
        output = PdfWriter()
        for aFile in output_files:
            with open(aFile, "rb") as f:
                input_pdf = PdfReader(f)
                output.add_page(input_pdf.pages[0])
        output_stream = io.BytesIO()  # file(output_files[0], "wb")
        output.write(output_stream)
        # output_stream.close()

        for aFile in output_files:
            print("Deleting " + aFile + " ...")
            os.remove(aFile)

        print("Writing final PDF " + output_files[0] + " ...")
        with open(output_files[0], "wb") as output_file:
            output_file.write(output_stream.getvalue())
        chown_file(output_files[0])  # t-k: change ownership of scan file
        print("Done.")


# Function for a single scan task
def scann_worker():
    server_refresh()

    # t-k: a little more descriptive logging
    print("Waiting for scan job ...")
    # print ' '*4 + 'printer scan status: ' + str(query_printer_scan_status(SERVER_INSTANCE_ID)) + ' -- waiting for 1
    # ...'
    i = 0
    while query_printer_scan_status(state.server_instance_id) != 1:
        i += 1
        if i % 300 == 0:  # t-k: refresh every > 5 mins (server get's auto. unregistered after ~30 mins)
            server_refresh()
        time.sleep(1)
    print(' ' * 4 + 'Got it!')

    push_server_options()

    # t-k: a little more descriptive logging
    print("Waiting for user selection ...")
    # print ' '*4 + 'printer scan status: ' + str(query_printer_scan_status(SERVER_INSTANCE_ID)) + ' -- waiting for 2
    # ...'

    # t-k: may be canceled by user: check if status changes back to 1
    i = 0
    while True:
        pps = query_printer_scan_status(state.server_instance_id)
        if pps == 2:
            break
        elif pps == 1:
            push_server_options()
            print('Reconnected, waiting for user selection ...')
            # print ' '*4 + 'printer scan status: ' + str(query_printer_scan_status(SERVER_INSTANCE_ID)) + ' --
            # waiting for 2 ...'
            continue
        i += 1
        if i % 300 == 0:
            server_refresh()
        time.sleep(1)
    print(' ' * 4 + 'Got it!')

    user_selection = query_user_options()
    print('Options selected by user:', user_selection)

    scan_and_save(user_selection)

