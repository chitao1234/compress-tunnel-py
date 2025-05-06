# ZstdTunnel

A compression tunnel supporting multiple algorithms (Zstandard, LZ4) that can be used to forward traffic between ports with transparent compression.

## Features

- Bidirectional port forwarding with compression
- Streaming compression and decompression for efficiency
- Thread-safe design with dedicated compressors per connection
- Lock-free implementation for better performance
- Configurable latency modes (low-latency vs high-compression)
- Adjustable buffer sizes for network operations
- TCP socket optimizations for minimal latency:
  - Nagle's algorithm disabled (TCP_NODELAY)
  - TIME_WAIT avoidance with SO_REUSEADDR
- Unified compression interface across different libraries
- Robust error handling and connection management:
  - Graceful handling of closed connections
  - Clean resource cleanup
  - Detailed logging of connection states
- Modular design with pluggable compression algorithms
- Currently supports:
  - Zstandard (zstd) compression
  - LZ4 compression
- Easy to extend with additional compression algorithms

## Installation

1. Clone the repository:
```
git clone https://github.com/yourusername/zstdtunnel.git
cd zstdtunnel
```

2. Install dependencies:
```
pip install zstandard lz4
```

## How It Works

The tunnel uses streaming compression, which means:
- No need to buffer complete messages before compression/decompression
- Lower memory usage for large data transfers
- Better performance for real-time applications
- No custom packet format required (uses the compression libraries' native framing)

### Connection Isolation

Each connection through the tunnel gets its own dedicated compression context:

1. **Independent Compression States**
   - Every connection has its own compressor and decompressor instances
   - No shared state between connections eliminates race conditions
   - Lock-free implementation for better performance

2. **Connection Encapsulation**
   - Each tunnel connection is managed as a self-contained `TunnelConnection` object 
   - Includes dedicated compression streams, socket pair, and thread management
   - Allows for clean lifecycle management of connections

This design ensures that:
- One connection's errors won't affect other connections
- Each connection can be independently monitored and managed
- The implementation is more robust against threading issues
- Memory resources are properly isolated between connections

### Latency Optimizations

The tunnel includes several optimizations to minimize latency:

1. **Low-Latency Streaming Mode** (default)
   - Performs frequent partial flushes of compressed data
   - Ensures data is sent as soon as possible rather than waiting to optimize compression

2. **Network Socket Optimizations**
   - Disables Nagle's algorithm which normally buffers small packets
   - Enables socket address reuse to avoid TIME_WAIT state delays
   - These optimizations significantly reduce latency for interactive applications

3. **Small Buffer Sizes**
   - Uses smaller receive buffers by default (1024 bytes)
   - Can be adjusted based on your specific needs

### Latency vs Compression Ratio

By default, the tunnel operates in low-latency mode, which prioritizes minimizing delay over achieving the highest possible compression ratio. This is suitable for:
- Interactive applications (SSH, remote terminals)
- Gaming
- Real-time voice/video

If you prefer to prioritize compression ratio at the expense of some latency (better for large file transfers or bulk data), use the `--high-compression` flag.

## Usage

### Basic Usage

#### Server Side
```
python server.py
```
This starts a server listening on port 19898 and forwarding to localhost:80 using zstd compression.

#### Client Side
```
python client.py
```
This starts a client listening on port 1080 and forwarding to the server at localhost:19898 using zstd compression.

### Advanced Usage with example.py

The `example.py` script provides a command-line interface to run either client or server with customizable options:

```
# Run server with zstd compression
python example.py server

# Run server with LZ4 compression
python example.py server --compression lz4

# Run client with zstd compression
python example.py client

# Run client with LZ4 compression
python example.py client --compression lz4

# Run server with custom ports and compression level
python example.py server --compression zstd --level 5 --server-port 8000 --target-port 8080

# Run client with custom ports
python example.py client --client-port 9000 --server-port 8000

# Run server prioritizing compression ratio over latency
python example.py server --high-compression

# Run client with larger buffer size for potentially better throughput
python example.py client --buffer-size 4096
```

### Command Line Arguments

```
usage: example.py [-h] [--compression {zstd,lz4}] [--level LEVEL]
                  [--client-port CLIENT_PORT] [--server-port SERVER_PORT]
                  [--target-host TARGET_HOST] [--target-port TARGET_PORT]
                  [--high-compression] [--buffer-size BUFFER_SIZE]
                  {client,server}

Run a compression tunnel.

positional arguments:
  {client,server}       Run as client or server

optional arguments:
  -h, --help            show this help message and exit
  --compression {zstd,lz4}, -c {zstd,lz4}
                        Compression algorithm (default: zstd)
  --level LEVEL, -l LEVEL
                        Compression level (default: 3)
  --client-port CLIENT_PORT
                        Client listen port (default: 1080)
  --server-port SERVER_PORT
                        Server listen port (default: 19898)
  --target-host TARGET_HOST
                        Target host for server (default: localhost)
  --target-port TARGET_PORT
                        Target port for server (default: 80)
  --high-compression    Prioritize compression ratio over latency (default: False)
  --buffer-size BUFFER_SIZE
                        Buffer size for network operations in bytes (default: 1024)
```

## Adding a New Compression Algorithm

To add a new compression algorithm:

1. Add a new implementation to `compression.py` by creating a new class that inherits from `Compressor`
2. Implement the required methods:
   - `compress` and `decompress` for one-shot compression
   - `create_compress_stream` and `create_decompress_stream` for streaming
3. Implement adapter classes for your compression library:
   - Extend `CompressStream` and `DecompressStream` with library-specific implementations
   - Handle the library's particular API for streaming compression/decompression
4. Update the `get_compressor` factory function to include your new algorithm

Example:

```python
# First, create stream adapters for your compression library
class MyNewCompressStream(CompressStream):
    def compress(self, data):
        # Handle compression with your library
        return self.compressor.compress(data)
    
    def flush(self, mode=FlushMode.PARTIAL_FLUSH):
        # Handle different flush modes for your library
        if mode == FlushMode.NO_FLUSH:
            return b''
        elif mode == FlushMode.PARTIAL_FLUSH:
            # Implement partial flush if supported by your library
            return self.compressor.partial_flush()
        elif mode == FlushMode.FULL_FLUSH:
            return self.compressor.final_flush()
    
    def end(self):
        # End the compression stream
        return self.compressor.finish()

class MyNewDecompressStream(DecompressStream):
    def decompress(self, data):
        return self.decompressor.decompress(data)
    
    def flush(self):
        # Implement if your decompressor supports flushing
        return b''

# Then create the main compressor implementation
class MyNewCompressor(Compressor):
    def __init__(self, level=1):
        # Import your compression library
        import mynewlib
        self.mynewlib = mynewlib
        self.level = level
        
    def compress(self, data):
        # One-shot compression
        return self.mynewlib.compress(data, level=self.level)
        
    def decompress(self, data):
        # One-shot decompression
        return self.mynewlib.decompress(data)
        
    def create_compress_stream(self):
        # Create and return your compressor adapter
        compressor_obj = self.mynewlib.CompressorObj(level=self.level)
        return MyNewCompressStream(compressor_obj)
        
    def create_decompress_stream(self):
        # Create and return your decompressor adapter
        decompressor_obj = self.mynewlib.DecompressorObj()
        return MyNewDecompressStream(decompressor_obj)
```

## Compression Stream Interface

The tunnel uses a unified compression interface to handle the different APIs of compression libraries:

- `CompressStream` - Standardized interface for compression streams
  - `compress(data)` - Compress a chunk of data
  - `flush(mode)` - Flush with different modes (NO_FLUSH, PARTIAL_FLUSH, FULL_FLUSH)
  - `end()` - End the compression stream

- `DecompressStream` - Standardized interface for decompression streams
  - `decompress(data)` - Decompress a chunk of data
  - `flush()` - Flush any buffered decompressed data (if supported)

This abstraction allows the tunnel to work with any compression library that can be adapted to these interfaces, regardless of their native API differences.

## Error Handling

The tunnel is designed to handle various failure scenarios gracefully:

### Connection Monitoring
- Active connection monitoring using non-blocking socket checks
- Automatic tunnel shutdown when either endpoint disconnects
- Proper cleanup of resources when connections are terminated

### Graceful Shutdown
- All sockets are properly closed with shutdown sequence
- Compression streams are flushed before closing
- Resources are released in all error scenarios

### Failure Scenarios Handled
- Target server disconnects
- Compression server disconnects
- Client disconnects
- Network errors during transmission
- Compression/decompression errors
- Application errors

### Detailed Logging
- Connection status changes are logged
- Error details are captured with stack traces
- Each connection has a unique ID for tracking

This robust error handling ensures that the tunnel operates reliably even in unstable network conditions and allows for clean recovery from failures.

## License

MIT License 