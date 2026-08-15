import errno  # t-k: needed for error handling in TCP proxy
import multiprocessing  # t-k: need subprocesses for TCP and UDP proxy
import queue
import socket  # t-k: needed for TCP and UDP proxy to interfere with scanner commands needed for multipage
import sys
import time

import sane

from .config import Config
from .state import state


# t-k: class that handles hex messages
class HexMessage(object):
    """
    instances can store, return and prettyprint hex messages
    """

    def __init__(self, hex_in, raw_in=False, enlarge_to: int = False):
        """
        hex_in should be either in the form '1b:a8:13:fb' or
        '1b a8 13 fb' (raw_in=False, default) or as decoded python
        character string '\x1b\xa8\x13\xfb' (raw_in=True)

        default message size is length of hex_in, set enlarge_to > 0
        to enable enlarging with zero bytes, e.g. enlarge_to=255 to
        end up with message length of at least 255 bytes
        """
        if raw_in:
            msg = hex_in
        else:
            msg = hex_in.replace(':', '').replace(' ', '').replace('\n', '')
            msg = msg.decode('hex_codec')
        if enlarge_to:
            bytes_left = enlarge_to - len(msg)
            self.msg = (msg + '\x00' * bytes_left)
        else:
            self.msg = msg

    def get_msg(self):
        """
        return hex message as decoded character string
        """
        return self.msg

    def startswith(self, prefix, start=0, end=sys.maxsize):
        """
        analogous to str.startswith, takes HexMessage instance as prefix
        might also be a tuple of HexMessage instances
        """
        if isinstance(prefix, tuple):
            msg_lst = []
            for p in prefix:
                msg_lst.append(p.get_msg())
            prefix = tuple(msg_lst)
        else:
            prefix = prefix.get_msg()
        return self.msg.startswith(prefix, start, end)

    def __eq__(self, other):
        return self.msg == other.get_msg()

    def __hash__(self):
        return hash(self.msg)

    def __str__(self):
        msg_encoded = self.msg.encode('hex_codec')
        res = ''
        for i in range(2, len(msg_encoded) + 1, 2):
            res += msg_encoded[i - 2:i] + ' '
            if (i % 10) == 0:
                res += ' '
            if (i % 40) == 0:
                res += '\n'
        return res.rstrip(' \n')


# t-k: proxy subprocess classes

class ProxyError(Exception):
    """
    exception to raise for handling proxy specific errors
    """
    pass


class ProxyProcess(multiprocessing.Process):
    config = Config()
    """
    a subprocess that acts as a man in the middle (MITM) proxy between
    scanner and workstation, so it can interfere with the messages
    being sent back and forth
    """
    BUFFERSIZE = 1240
    SERVER_IP = ''
    SCANNER_IP = config.SCANNER_IP
    DEBUGLEVEL = config.PROXY_DEBUGLEVEL  # 0 -> no | 1 -> a bit | 2 -> a bit more | 3 -> lots of printing

    def __init__(self):
        super(ProxyProcess, self).__init__()
        self._stoprequest = multiprocessing.Event()

    def join(self, timeout=None):
        self._stoprequest.set()
        super(ProxyProcess, self).join(timeout)

    def _printLog(self, debug_level, *content):
        """
        debug_level -> of this log entry
        """
        if self.DEBUGLEVEL >= debug_level:
            print(str(self.__class__).split('.')[1][:-2] + ':', end=' ')
            for element in content[:-1]:
                print(element, end=' ')
            print(content[-1])


class UDProxy(ProxyProcess):
    """
    MITM UDP proxy on port 161 (SNMP)
    """
    PORT = 161
    PROTOCOL = 'UDP'

    def __init__(self):
        super(UDProxy, self).__init__()
        self.serverConn = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.serverConn.bind((self.SERVER_IP, self.PORT))
        self.serverConn.settimeout(1.0)
        self.clientConn = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def run(self):
        self._printLog(1, 'Initated server listening on port %d (%s) ...' % (self.PORT, self.PROTOCOL))
        self._printLog(1, 'Initiated client connection to scanner port %d (%s) ...' % (self.PORT, self.PROTOCOL))
        while not self._stoprequest.is_set():
            try:
                from_ws, addr_ws = self.serverConn.recvfrom(self.BUFFERSIZE)
                self._printLog(3, 'received %4d bytes from %s:%5d' % (len(from_ws), addr_ws[0], addr_ws[1]))
            except socket.timeout:
                continue
            self.serverConn.settimeout(None)
            sent_size_client = self.clientConn.sendto(from_ws, (self.SCANNER_IP, self.PORT))
            self._printLog(3, 'sent     %4d bytes to   %s:%5d' % (sent_size_client, self.SCANNER_IP, self.PORT))
            from_scanner, addr_sc = self.clientConn.recvfrom(self.BUFFERSIZE)
            self._printLog(3, 'received %4d bytes from %s:%5d' % (len(from_scanner), addr_sc[0], addr_sc[1]))
            sent_size_server = self.serverConn.sendto(from_scanner, (addr_ws[0], addr_ws[1]))
            self._printLog(3, 'sent     %4d bytes to   %s:%5d' % (sent_size_server, addr_ws[0], addr_ws[1]))
            self.serverConn.settimeout(1.0)
        # execute when process is joined (closed)
        self.serverConn.close()
        self.clientConn.close()
        self._printLog(1, 'closed!')


class TCProxy(ProxyProcess):
    """
    MITM TCP proxy on port 9400
    """
    PORT = 9400
    PROTOCOL = 'TCP'
    SRCPORT = 0  # 2270 # for client connection with scanner, set to 0 if dynamic source port wanted

    def __init__(self):
        super(TCProxy, self).__init__()
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind((self.SERVER_IP, self.PORT))
        self.server.listen(1)
        self.server.settimeout(1.0)
        self.clientConn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_conn = self.server_conn_addr = None
        if self.SRCPORT:
            self.clientConn.bind((self.SERVER_IP, self.SRCPORT))
        self.npRequest = HexMessage('1b a8 20 fb 01 2c 01', enlarge_to=255)  # np = next page

    def _check_next_page_status(self):
        please_wait = HexMessage('a8 08 00 00 00 f9 00 00 01 00 1e', enlarge_to=255)
        yes_new_page = HexMessage('a8 00 00 00 00 f9 00 00 01 00 1e', enlarge_to=255)
        no_more_pages = HexMessage('a8 04 00 00 00 f9 00 00 01 00 1e', enlarge_to=255)
        canceling = HexMessage('a8 04 f9 00 00 00 00 01', enlarge_to=255)
        result = None
        self.clientConn.settimeout(1.0)
        while not self._stoprequest.is_set():
            try:
                sent_size_client = self.clientConn.send(self.npRequest.get_msg())
                self._printLog(3, 'checking if there are more pages to come ...')
                self._printLog(3, 'sent     %4d bytes to   scanner' % sent_size_client)
                self._printLog(3, str(self.npRequest).split('\n')[0])
                from_scanner = self.clientConn.recv(self.BUFFERSIZE)
                fr_sc_hx_msg = HexMessage(from_scanner, raw_in=True)
                self._printLog(3, 'received %4d bytes from scanner' % (len(from_scanner)))
                self._printLog(3, str(fr_sc_hx_msg).split('\n')[0])
            except socket.timeout:
                continue
            if fr_sc_hx_msg == please_wait:
                self._printLog(3, '"please wait"')
                time.sleep(0.5)
                continue
            elif fr_sc_hx_msg == yes_new_page:
                result = 'yes new page'
                self._printLog(1, '"%s"' % result)
                break
            elif fr_sc_hx_msg in [no_more_pages, canceling]:
                result = 'no more pages'
                self._printLog(1, '"%s"' % result)
                break
            else:
                self._printLog(1, 'could not interpret answer from scanner,\n' + str(fr_sc_hx_msg) + '\nretrying ...')
                continue
        self.clientConn.settimeout(None)
        return result

    def run(self):
        result = None
        nr_connect = 1
        error3byte_msg = HexMessage('a8 28 00')
        init_msg1 = HexMessage('1b a8 12 00')  # first sent by sane
        init_msg2 = HexMessage('1b a8 16 00')  # second sent by sane
        spec_msg = HexMessage('1b a8 13 fb', enlarge_to=255)  # not sent by sane, but needed after init_msg1
        # self.npRequest needed after init_msg2
        self._printLog(1, 'Initating server listening on port %d (%s) ...' % (self.PORT, self.PROTOCOL))
        # main loop starting with connection initiation with workstation (proxy as server)
        while not self._stoprequest.is_set():
            try:
                self.server_conn, self.server_conn_addr = self.server.accept()
            except socket.timeout:
                continue
            self.server_conn.settimeout(1.0)
            self._printLog(2, 'Accepted connection nr. %d from:' % nr_connect, self.server_conn_addr)
            # (re)connect with scanner (proxy as client) if necessary
            while not self._stoprequest.is_set():
                try:
                    self.clientConn.connect((self.SCANNER_IP, self.PORT))
                except socket.error as e:
                    # already connected, no need to reconnect
                    if e.errno == errno.EISCONN:
                        pass
                    else:
                        raise
                else:
                    self.clientConn.settimeout(None)
                    self._printLog(1, 'Initiated client connection to scanner port %d (%s) ...' %
                                   (self.PORT, self.PROTOCOL))
                try:
                    self.clientConn.send('')
                except socket.error as e:
                    # broken pipe error (remote disconnect (or not yet connected)) or
                    #     bad file descriptor (socket already closed by self)
                    if e.errno in [errno.EPIPE, errno.EBADF]:
                        self.clientConn.close()
                        self.clientConn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        continue
                    else:
                        raise
                break
            # actual start: get package from workstation and send to scanner
            #     loop back here continuously to check if _stoprequest is set
            while not self._stoprequest.is_set():
                try:
                    try:
                        from_ws = self.server_conn.recv(self.BUFFERSIZE)
                    except socket.timeout:
                        try:
                            # blocking Queue.get call that times out after 50 ms
                            query = state.query_q.get(True, 0.05)
                        except queue.Empty:
                            continue
                        if query == 'check if page is coming':
                            result = self._check_next_page_status()
                            if result:
                                state.result_q.put(result)
                        continue
                    except socket.error as e:
                        # connection reset by peer
                        if e.errno == errno.ECONNRESET:
                            self.server_conn.close()
                            break
                        else:
                            raise
                    self.server_conn.settimeout(None)
                    self._printLog(3, 'received %4d bytes from workstation' % (len(from_ws)))
                    if HexMessage(from_ws, raw_in=True) == error3byte_msg:
                        self._printLog(3, error3byte_msg)
                        # connected workstation too early before file chunk was complete
                        raise ProxyError('connected workstation too early, returning to communication with scanner')
                except ProxyError as e:
                    self._printLog(3, 'ProxyError:', e)
                    pass  # did't send these 3 bytes to scanner, continue with scanner as if nothing happened
                else:
                    sent_size_client = self.clientConn.send(from_ws)
                    self._printLog(3, 'sent     %4d bytes to   scanner' % sent_size_client)
                    self._printLog(3, str(HexMessage(from_ws, raw_in=True)).split('\n')[0])
                    if sent_size_client == 0:
                        nr_connect += 1
                        self.server_conn.close()
                        break
                # 250 ms timeout for (all but 1st) data packages from scanner to come in
                self.clientConn.settimeout(0.25)
                sent_sizeserver = 0
                retry_after1240 = 0
                sending_file = False
                # get package from scanner and send to workstation
                #     loop if not timed out -> sending a file
                while not self._stoprequest.is_set():
                    try:
                        from_scanner = self.clientConn.recv(self.BUFFERSIZE)
                        self._printLog(3, 'received %4d bytes from scanner' % (len(from_scanner)))
                    except socket.timeout:
                        # endless retry if the 1st package was timed out (effectively no timeout at all for 1st package)
                        if not sending_file:
                            continue
                        # retry 2 times if last package was 1240 bytes (effectively 3*250ms = 0.75s timeout
                        #     for these 1240 bytes follow up packages)
                        if sent_sizeserver == 1240 and retry_after1240 < 3:
                            retry_after1240 += 1
                            continue
                        sending_file = False
                        self.clientConn.settimeout(None)
                        break
                    retry_after1240 = 0
                    sent_sizeserver = self.server_conn.send(from_scanner)
                    self._printLog(3, 'sent     %4d bytes to   workstation' % sent_sizeserver)
                    self._printLog(3, str(HexMessage(from_scanner, raw_in=True)).split('\n')[0])
                    sending_file = True  # after 1st data package
                # special intermediate packages needed to be sent during the beginning after init_msg1/2
                from_ws_hx_msg = HexMessage(from_ws, raw_in=True)
                if from_ws_hx_msg in [init_msg1, init_msg2]:
                    if from_ws_hx_msg == init_msg1:
                        to_send = spec_msg
                    elif from_ws_hx_msg == init_msg2:
                        to_send = self.npRequest
                    sent_size_client = self.clientConn.send(to_send.get_msg())
                    self._printLog(3, 'sent     %4d bytes to   scanner' % sent_size_client)
                    self._printLog(3, str(to_send).split('\n')[0])
                    from_scanner = self.clientConn.recv(self.BUFFERSIZE)
                    self._printLog(3, 'received %4d bytes from scanner' % (len(from_scanner)))
                    self._printLog(3, str(HexMessage(from_scanner, raw_in=True)).split('\n')[0])
                self.server_conn.settimeout(1.0)
        # execute when process is joined (closed)
        try:
            self.server_conn.close()
        except AttributeError:
            pass
        self.server.close()
        self.clientConn.close()
        self._printLog(1, 'closed!')


# t-k: modifications to sane module's scanner handling
class _ModSaneIterator(sane._SaneIterator):
    """
    modified next method communicating with TCP proxy subprocess
    to enable multipage scanning
    """

    def __init__(self, device):
        sane._SaneIterator.__init__(self, device)
        self.iteration = 0

    def __next__(self):
        try:
            if self.iteration != 0:
                print('Another page coming?')
                # tell TCP proxy to ...
                state.query_q.put('check if page is coming')
                # get result back, with blocking Queue.get call
                result = state.result_q.get(True)
                self.device.cancel()
                if result == 'yes new page':
                    pass
                elif result == 'no more pages':
                    raise StopIteration
            self.device.start()
        except sane.error as v:
            if v == 'Document feeder out of documents':
                raise StopIteration
            else:
                raise Exception('Starting scan not possible: ' + v)
        else:
            self.iteration += 1
        # no_cancel=1: leaving scanner in some sort of limbo - scan is done,
        #     but not finished properly - perfect for TCP proxy querying
        #     whether there are more pages to come
        return self.device.snap(no_cancel=1)

    def next(self):
        return self.__next__()


class ModSaneDev(sane.SaneDev):
    """
    use modified _SaneIterator class
    """

    def __init__(self, devname):
        sane.SaneDev.__init__(self, devname)

    def multi_scan(self):
        return _ModSaneIterator(self)


def modsaneopen(devname):
    """
    Open a device for scanning using modified SaneDev class
    """
    new = ModSaneDev(devname)
    return new


def start_proxies():
    while True:
        try:
            state.proxies = [UDProxy(), TCProxy()]
        except socket.error as e:
            if e.errno == errno.EADDRINUSE:  # address already in use
                print('TCP proxy was restarted too soon, waiting 10s ...')
                time.sleep(10)
            else:
                raise
        else:
            break
    for p in state.proxies:
        p.start()

# angelnu proxies not defined without MODIFIED_SANE
def exit_proxies():
    for p in state.proxies:
        p.join()