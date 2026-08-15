import multiprocessing
from dataclasses import dataclass, field

from sane import SaneDev

from .config import Config
from .utils import server_uid_gen


@dataclass
class AppState:
    server_instance_id: int = 0
    server_uid: str = field(default_factory=lambda: server_uid_gen(Config().SERVER_NAME))
    sane_singleton: SaneDev | None = None
    caught_sigquit: bool = False # t-k: keep track of a caught SIGQUIT, so temp. files (PID file) will not be removed
    proxies: list = field(default_factory=list)
    query_q: multiprocessing.Queue = field(default_factory=multiprocessing.Queue)
    result_q: multiprocessing.Queue = field(default_factory=multiprocessing.Queue)

state = AppState()