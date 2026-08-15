import multiprocessing
from dataclasses import dataclass, field

from sane import SaneDev

from .utils import server_uid_gen


@dataclass
class AppState:
    server_instance_id: int = 0
    server_uid: str = field(default_factory=server_uid_gen)
    sane_singleton: SaneDev | None = None
    caught_sigquit: bool = False
    proxies: list = field(default_factory=list)
    query_q: multiprocessing.Queue = field(default_factory=multiprocessing.Queue)
    result_q: multiprocessing.Queue = field(default_factory=multiprocessing.Queue)

state = AppState()