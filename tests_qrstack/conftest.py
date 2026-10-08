import socket

import pytest
import requests
from curl_cffi import Curl
from curl_cffi import requests as curl_requests


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Offline tests must never access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(requests.Session, "send", blocked)
    monkeypatch.setattr(curl_requests.Session, "request", blocked)
    monkeypatch.setattr(Curl, "perform", blocked)
    # Network is forbidden and publication pacing has separate simulated-clock
    # tests. Do not turn the ordinary offline suite into real-time waits.
    monkeypatch.setattr("qrstack_instagram.publisher.time.sleep", lambda _: None)
