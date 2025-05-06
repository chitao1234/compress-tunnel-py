from tunnel import ServerTunnel

if __name__ == '__main__':
    server = ServerTunnel()
    # Default compression is zstd
    # To use LZ4 instead: server = ServerTunnel('lz4')
    server.run(19898, 'localhost', 80)  # Listen on 19898, forward to localhost:80
