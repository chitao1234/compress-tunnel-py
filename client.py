from tunnel import ClientTunnel

if __name__ == '__main__':
    client = ClientTunnel()
    # Default compression is zstd
    # To use LZ4 instead: client = ClientTunnel('lz4')
    client.run(1080, 'localhost', 19898)  # Listen on 1080, forward to server 19898
