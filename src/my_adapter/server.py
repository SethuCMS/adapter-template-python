"""Runs your adapter as a gRPC server the gateway can call."""
from __future__ import annotations

import logging
import os
import signal
from concurrent import futures
from typing import Callable, Optional

import grpc

from .base import Adapter
from .example import InMemoryAdapter
from .generated import adapter_pb2_grpc as pb_grpc
from .servicer import AdapterService

MAX_MESSAGE_BYTES = 8 * 1024 * 1024


def build_server(
    factory: Callable[[], Adapter],
    adapter_type: str,
    address: str = "127.0.0.1:0",
    *,
    tls_cert: Optional[bytes] = None,
    tls_key: Optional[bytes] = None,
    client_ca: Optional[bytes] = None,
    max_workers: int = 16,
):
    """Create (but do not start) a server. Returns (server, bound_port, service).

    Without TLS the server only makes sense on localhost or a private socket. For any other
    network give it ``tls_cert`` and ``tls_key``, and ``client_ca`` to require the gateway's
    client certificate (mutual TLS).
    """
    service = AdapterService(factory, adapter_type)
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=[
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
        ],
    )
    pb_grpc.add_AdapterServicer_to_server(service, server)
    if tls_cert and tls_key:
        credentials = grpc.ssl_server_credentials(
            [(tls_key, tls_cert)], root_certificates=client_ca, require_client_auth=client_ca is not None
        )
        port = server.add_secure_port(address, credentials)
    else:
        host = address.rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1") and not address.startswith("unix:"):
            raise ValueError("Refusing to serve plain-text gRPC on a non-local address. Provide TLS certificates.")
        port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"Could not listen on {address}")
    return server, port, service


def _read(path_var: str) -> Optional[bytes]:
    path = os.environ.get(path_var)
    if not path:
        return None
    with open(path, "rb") as handle:
        return handle.read()


def main() -> None:
    """Entry point: ``my-adapter``. Configure with environment variables:

    ADAPTER_ADDRESS   default 127.0.0.1:50051
    ADAPTER_TLS_CERT, ADAPTER_TLS_KEY, ADAPTER_CLIENT_CA   paths to PEM files
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    address = os.environ.get("ADAPTER_ADDRESS", "127.0.0.1:50051")
    # Replace InMemoryAdapter and "memory" with your adapter and its type name.
    server, port, service = build_server(
        InMemoryAdapter, "memory", address,
        tls_cert=_read("ADAPTER_TLS_CERT"), tls_key=_read("ADAPTER_TLS_KEY"), client_ca=_read("ADAPTER_CLIENT_CA"),
    )
    server.start()
    logging.getLogger("my_adapter").info("listening on port %s", port)

    stop = {"now": False}

    def shutdown(*_args):
        stop["now"] = True
        server.stop(grace=5)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server.wait_for_termination()
    service.close_all()


if __name__ == "__main__":
    main()
