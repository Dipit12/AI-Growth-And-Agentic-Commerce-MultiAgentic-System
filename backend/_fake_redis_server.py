"""Local-dev convenience: a real TCP server speaking the Redis protocol, backed by fakeredis, so
the unmodified app (which expects a real Redis at REDIS_URL) works without Docker. Not part of the
app itself — for docker-compose-free local exploration only. Run with: python _fake_redis_server.py
"""

from fakeredis import TcpFakeServer

if __name__ == "__main__":
    server_address = ("127.0.0.1", 6379)
    server = TcpFakeServer(server_address, server_type="redis")
    print(f"Fake Redis server listening on {server_address[0]}:{server_address[1]}")
    server.serve_forever()
