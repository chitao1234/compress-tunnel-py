"""Common tunnel functionality for both client and server."""
import socket
import threading
import traceback
import time
import select
from compression import get_compressor, FlushMode


class ConnectionClosed(Exception):
    """Exception raised when a connection is closed."""
    pass


class TunnelConnection:
    """Represents a single tunnel connection with its own compression context."""
    
    def __init__(self, tunnel, conn_id, source_sock, target_sock, compression_algo, compression_level, 
                 low_latency, buffer_size, direction1to2_compressed=True):
        # Connection identifiers
        self.conn_id = conn_id
        self.source_sock = source_sock
        self.target_sock = target_sock
        
        # Create dedicated compressor for this connection
        self.compressor = get_compressor(compression_algo, compression_level)
        
        # Connection settings
        self.low_latency = low_latency
        self.buffer_size = buffer_size
        self.direction1to2_compressed = direction1to2_compressed
        
        # Labels for logging
        if direction1to2_compressed:
            self.source_label = f"local-{conn_id}"
            self.target_label = f"server-{conn_id}"
        else:
            self.source_label = f"client-{conn_id}"
            self.target_label = f"target-{conn_id}"
            
        # Reference to parent tunnel
        self.tunnel = tunnel
        
        # Compression streams
        self.compress_stream = None
        self.decompress_stream = None
        
        # Coordination
        self.shutdown_event = threading.Event()
        self.threads = []

    def forward_compressed(self):
        """
        Compress data from source_sock and forward to target_sock (streaming).
        This method is used in either direction based on direction1to2_compressed.
        """
        # For direction1to2_compressed=True: source->target
        # For direction1to2_compressed=False: target->source (we're called in reverse)
        
        # When this is used for the target->source flow in server mode, swap the sockets
        if not self.direction1to2_compressed:
            # We're handling target->source flow in server mode
            in_sock = self.target_sock
            out_sock = self.source_sock
            in_label = self.target_label
            out_label = self.source_label
        else:
            in_sock = self.source_sock
            out_sock = self.target_sock
            in_label = self.source_label
            out_label = self.target_label
        
        if in_sock is None or out_sock is None:
            self.shutdown_event.set()
            return
            
        # Create a fresh compress stream for this connection
        self.compress_stream = self.compressor.create_compress_stream()
        
        error_count = 0
        max_errors = 3  # Maximum consecutive errors before giving up
        
        try:
            print(f"Starting compression from {in_label} to {out_label}")
            while not self.shutdown_event.is_set():
                # Check if target is still connected
                if not self.tunnel.check_socket_alive(out_sock):
                    print(f"Target {out_label} disconnected, stopping compression")
                    self.shutdown_event.set()
                    break
                
                # Use select to wait for data with a timeout
                try:
                    readable, _, _ = select.select([in_sock], [], [], 0.5)
                    if not readable:
                        continue  # No data available, check again
                except Exception as e:
                    print(f"Select error in compression from {in_label} to {out_label}: {e}")
                    if not self.tunnel.check_socket_alive(in_sock):
                        print(f"Source {in_label} disconnected during select")
                        self.shutdown_event.set()
                        break
                    continue  # Try again if source is still alive
                
                try:
                    # Set a timeout for receiving data
                    in_sock.settimeout(0.5)
                    data = in_sock.recv(self.buffer_size)
                    
                    if not data:
                        print(f"No data from {in_label}, closing connection")
                        self.shutdown_event.set()
                        break
                    
                    # Compress chunk
                    compressed_chunk = self.compress_stream.compress(data)
                    if compressed_chunk:
                        out_sock.sendall(compressed_chunk)
                    
                    # For low latency mode, flush after each chunk to ensure data is sent immediately
                    if self.low_latency:
                        flush_chunk = self.compress_stream.flush(FlushMode.PARTIAL_FLUSH)
                        if flush_chunk:
                            out_sock.sendall(flush_chunk)
                            
                    # Reset error count on successful operation
                    error_count = 0
                    
                except socket.timeout:
                    # Timeout is expected, just continue
                    continue
                except socket.error as e:
                    print(f"Socket error during compression from {in_label} to {out_label}: {e}")
                    if isinstance(e, ConnectionResetError) or not self.tunnel.check_socket_alive(out_sock):
                        self.shutdown_event.set()
                        break
                    error_count += 1
                    if error_count > max_errors:
                        print(f"Too many socket errors, stopping compression from {in_label} to {out_label}")
                        self.shutdown_event.set()
                        break
                except Exception as e:
                    print(f"Error during compression from {in_label} to {out_label}: {e}")
                    traceback.print_exc()
                    
                    # Try to reset the compressor and continue
                    if not self.handle_compression_error(self.compress_stream, "compression"):
                        error_count += 1
                        if error_count > max_errors:
                            print(f"Too many compression errors, stopping compression from {in_label} to {out_label}")
                            self.shutdown_event.set()
                            break
            
            # Explicitly flush the compressor when the loop ends
            try:
                if self.tunnel.check_socket_alive(out_sock):
                    final_chunk = self.compress_stream.end()
                    if final_chunk:
                        out_sock.sendall(final_chunk)
                    print(f"Compression stream from {in_label} to {out_label} ended cleanly")
            except Exception as e:
                print(f"Error during final compression flush from {in_label} to {out_label}: {e}")
                traceback.print_exc()
                
        except Exception as e:
            print(f"Error in compression stream from {in_label} to {out_label}: {e}")
            traceback.print_exc()
        finally:
            # Signal that this thread is done
            self.shutdown_event.set()
            print(f"Compression forward from {in_label} to {out_label} ended")
    
    def forward_decompressed(self):
        """
        Decompress data from source_sock and forward to target_sock (streaming).
        This method is used in either direction based on direction1to2_compressed.
        """
        # For direction1to2_compressed=True: target->source
        # For direction1to2_compressed=False: source->target (we're called in reverse)

        # When this is used for the source->target flow in server mode, swap the sockets
        if not self.direction1to2_compressed:
            # We're handling source->target flow in server mode
            in_sock = self.source_sock
            out_sock = self.target_sock
            in_label = self.source_label
            out_label = self.target_label
        else:
            in_sock = self.target_sock
            out_sock = self.source_sock
            in_label = self.target_label
            out_label = self.source_label
        
        if in_sock is None or out_sock is None:
            self.shutdown_event.set()
            return
            
        # Create a fresh decompress stream for this connection
        self.decompress_stream = self.compressor.create_decompress_stream()
        
        error_count = 0
        max_errors = 3  # Maximum consecutive errors before giving up
        
        # Buffer for incomplete frames
        buffer = bytearray()
        
        try:
            print(f"Starting decompression from {in_label} to {out_label}")
            while not self.shutdown_event.is_set():
                # Check if target is still connected
                if not self.tunnel.check_socket_alive(out_sock):
                    print(f"Target {out_label} disconnected, stopping decompression")
                    self.shutdown_event.set()
                    break
                
                # Use select to wait for data with a timeout
                try:
                    readable, _, _ = select.select([in_sock], [], [], 0.5)
                    if not readable:
                        continue  # No data available, check again
                except Exception as e:
                    print(f"Select error in decompression from {in_label} to {out_label}: {e}")
                    if not self.tunnel.check_socket_alive(in_sock):
                        print(f"Source {in_label} disconnected during select")
                        self.shutdown_event.set()
                        break
                    continue  # Try again if source is still alive
                
                try:
                    # Set a timeout for receiving data
                    in_sock.settimeout(0.5)
                    compressed_data = in_sock.recv(self.buffer_size)
                    
                    if not compressed_data:
                        print(f"No data from {in_label}, closing connection")
                        self.shutdown_event.set()
                        break
                    
                    # Add to buffer
                    buffer.extend(compressed_data)
                    
                    # Decompress chunk
                    try:
                        decompressed_chunk = self.decompress_stream.decompress(buffer)
                        if decompressed_chunk:
                            out_sock.sendall(decompressed_chunk)
                        # Clear buffer on successful decompression
                        buffer.clear()
                        # Reset error count on successful operation
                        error_count = 0
                    except Exception as decomp_err:
                        print(f"Decompression error from {in_label} to {out_label}: {decomp_err}")
                        traceback.print_exc()
                        
                        # Try to reset the decompressor and continue with next frame
                        if not self.handle_compression_error(self.decompress_stream, "decompression"):
                            error_count += 1
                            if error_count > max_errors:
                                print(f"Too many decompression errors, stopping decompression from {in_label} to {out_label}")
                                self.shutdown_event.set()
                                break
                                
                        # Clear buffer and try to get a new frame
                        buffer.clear()
                except socket.timeout:
                    # Timeout is expected, just continue
                    continue
                except socket.error as e:
                    print(f"Socket error during decompression from {in_label} to {out_label}: {e}")
                    if isinstance(e, ConnectionResetError) or not self.tunnel.check_socket_alive(out_sock):
                        self.shutdown_event.set()
                        break
                    error_count += 1
                    if error_count > max_errors:
                        print(f"Too many socket errors, stopping decompression from {in_label} to {out_label}")
                        self.shutdown_event.set()
                        break
                except Exception as e:
                    print(f"Error during decompression from {in_label} to {out_label}: {e}")
                    traceback.print_exc()
                    error_count += 1
                    if error_count > max_errors:
                        print(f"Too many errors, stopping decompression from {in_label} to {out_label}")
                        self.shutdown_event.set()
                        break
            
            # Try to flush the decompressor if supported
            try:
                if hasattr(self.decompress_stream, 'flush') and self.tunnel.check_socket_alive(out_sock):
                    final_chunk = self.decompress_stream.flush()
                    if final_chunk:
                        out_sock.sendall(final_chunk)
                    print(f"Decompression stream from {in_label} to {out_label} flushed")
            except Exception as e:
                print(f"Error during final decompression flush from {in_label} to {out_label}: {e}")
                traceback.print_exc()
            
        except Exception as e:
            print(f"Error in decompression stream from {in_label} to {out_label}: {e}")
            traceback.print_exc()
        finally:
            # Signal that this thread is done
            self.shutdown_event.set()
            print(f"Decompression forward from {in_label} to {out_label} ended")
    
    def handle_compression_error(self, stream, operation_type):
        """Handle compression errors by resetting the compression stream."""
        try:
            # Try to reset the stream to recover from errors
            stream.reset()
            print(f"Reset {operation_type} stream for connection #{self.conn_id}")
            return True
        except Exception as e:
            print(f"Failed to reset {operation_type} stream for connection #{self.conn_id}: {e}")
            traceback.print_exc()
            return False

    def start(self):
        """Start the tunnel connection threads."""
        # Create and start threads based on compression direction
        if self.direction1to2_compressed:
            # Client to server: compress source to target, decompress target to source
            compress_thread = threading.Thread(
                target=self.forward_compressed,
                name=f"compress_{self.conn_id}_{self.source_label}_to_{self.target_label}"
            )
            
            decompress_thread = threading.Thread(
                target=self.forward_decompressed,
                name=f"decompress_{self.conn_id}_{self.target_label}_to_{self.source_label}"
            )
        else:
            # Server to client: decompress source to target, compress target to source
            compress_thread = threading.Thread(
                target=self.forward_decompressed,  # Note: Using decompression for source to target
                name=f"decompress_{self.conn_id}_{self.source_label}_to_{self.target_label}"
            )
            
            decompress_thread = threading.Thread(
                target=self.forward_compressed,  # Note: Using compression for target to source
                name=f"compress_{self.conn_id}_{self.target_label}_to_{self.source_label}"
            )
        
        compress_thread.daemon = True
        decompress_thread.daemon = True
        
        compress_thread.start()
        decompress_thread.start()
        
        self.threads.append(compress_thread)
        self.threads.append(decompress_thread)
        
        print(f"Connection #{self.conn_id} established: {self.source_label} <-> {self.target_label}")
        return compress_thread, decompress_thread
    
    def stop(self):
        """Stop the tunnel connection."""
        if self.shutdown_event:
            self.shutdown_event.set()
        
        # Close sockets
        self.cleanup()
        
        # Wait for threads to finish
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        print(f"Connection #{self.conn_id} stopped: {self.source_label} <-> {self.target_label}")
        
    def cleanup(self):
        """Clean up resources."""
        # Flush compress stream if it exists
        if self.compress_stream:
            try:
                final_chunk = self.compress_stream.end()
                if final_chunk and self.tunnel.check_socket_alive(self.target_sock):
                    self.target_sock.sendall(final_chunk)
            except Exception as e:
                print(f"Error during final compression flush for connection #{self.conn_id}: {e}")
        
        # Close sockets
        if self.source_sock:
            self.tunnel.safe_close(self.source_sock, self.source_label)
            self.source_sock = None
            
        if self.target_sock:
            self.tunnel.safe_close(self.target_sock, self.target_label)
            self.target_sock = None


class Tunnel:
    """Base tunnel class with compression support."""
    
    def __init__(self, compression_algo='zstd', compression_level=3, low_latency=True, buffer_size=1024):
        self.compression_algo = compression_algo
        self.compression_level = compression_level
        self.low_latency = low_latency
        self.buffer_size = buffer_size
        # Dictionary to store socket labels by id for standalone socket operations
        self.socket_labels = {}
        # Lock only for the socket_labels dictionary
        self.socket_lock = threading.Lock()
    
    def get_socket_label(self, sock):
        """Get the label for a socket outside of a connection context."""
        if sock is None:
            return "None"
        with self.socket_lock:
            return self.socket_labels.get(id(sock), str(id(sock)))
    
    def set_socket_label(self, sock, label):
        """Set a label for a socket outside of a connection context."""
        if sock is None:
            return
        with self.socket_lock:
            self.socket_labels[id(sock)] = label
    
    def create_socket(self):
        """Create a standard socket with optimized settings for low latency."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Enable address reuse to avoid TIME_WAIT issues
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Disable Nagle's algorithm to reduce latency
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock
    
    def optimize_socket(self, sock):
        """Apply low-latency optimizations to an existing socket."""
        if sock is None:
            return
        try:
            # Enable address reuse to avoid TIME_WAIT issues
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Disable Nagle's algorithm to reduce latency
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # Set a timeout to ensure we can detect disconnects
            sock.settimeout(30.0)
        except Exception as e:
            print(f"Warning: Could not optimize socket: {e}")
            traceback.print_exc()
    
    def check_socket_alive(self, sock):
        """Check if a socket is still connected."""
        if sock is None:
            return False
        try:
            # Use select with zero timeout to check if the socket is readable
            readable, _, exceptional = select.select([sock], [], [sock], 0)
            if exceptional:
                return False
            if readable:
                # Try to peek at the socket - if it returns no data, it's disconnected
                data = sock.recv(1, socket.MSG_PEEK)
                return bool(data)
            return True
        except:
            return False
    
    def safe_close(self, sock, label=None):
        """Safely close a socket with error handling."""
        if sock is None:
            return
            
        # Use the provided label or get the stored label
        sock_label = label or self.get_socket_label(sock)
        
        try:
            # Try to shutdown gracefully first
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except:
                pass
                
            sock.close()
            print(f"Closed {sock_label}")
            
            # Remove from socket labels dictionary
            with self.socket_lock:
                if id(sock) in self.socket_labels:
                    del self.socket_labels[id(sock)]
                    
        except Exception as e:
            print(f"Error closing {sock_label}: {e}")
    
    def setup_tunnel(self, sock1, sock2, direction1to2_compressed=True, conn_id=0):
        """Set up bidirectional tunneling between two sockets."""
        if sock1 is None or sock2 is None:
            print(f"Cannot setup tunnel #{conn_id}: one or both sockets are None")
            return None, None, None, threading.Event()
            
        # Create a new TunnelConnection instance for this connection
        # with its own dedicated compressor/decompressor
        connection = TunnelConnection(
            tunnel=self,
            conn_id=conn_id,
            source_sock=sock1,
            target_sock=sock2,
            compression_algo=self.compression_algo,
            compression_level=self.compression_level,
            low_latency=self.low_latency,
            buffer_size=self.buffer_size,
            direction1to2_compressed=direction1to2_compressed
        )
        
        # Start the connection
        t1, t2 = connection.start()
        
        def cleanup_monitor():
            """Monitor thread to handle cleanup when tunneling ends."""
            # Wait for the connection to signal shutdown
            while not connection.shutdown_event.is_set():
                time.sleep(0.5)
            
            # Give threads a moment to finish any pending operations
            time.sleep(0.2)
            
            # Clean up the connection resources
            connection.cleanup()
            
            print(f"Tunnel #{conn_id} between {connection.source_label} and {connection.target_label} closed")
        
        # Start the cleanup monitor thread
        monitor = threading.Thread(
            target=cleanup_monitor,
            name=f"cleanup-{conn_id}"
        )
        monitor.daemon = True
        monitor.start()
        
        return t1, t2, monitor, connection.shutdown_event


class ClientTunnel(Tunnel):
    """Client-side tunnel implementation."""
    
    def run(self, bind_port, server_host, server_port, compression_algo='zstd', 
            compression_level=3, low_latency=True, buffer_size=1024):
        """Run the client tunnel."""
        # Initialize with settings
        self.compression_algo = compression_algo
        self.compression_level = compression_level
        self.low_latency = low_latency
        self.buffer_size = buffer_size
        
        client = self.create_socket()
        try:
            client.bind(('0.0.0.0', bind_port))
            client.listen(5)
            print(f"Client listening on port {bind_port}")
            print(f"Using {compression_algo} compression (level {compression_level}, {'low-latency' if low_latency else 'high-compression'} mode)")

            # Connection counter for logging
            conn_id = 0
            
            while True:
                try:
                    # Use a timeout to make sure we can handle interrupts
                    client.settimeout(1.0)
                    local_sock, addr = client.accept()
                    conn_id += 1
                    
                    # Apply optimizations to the connected socket
                    self.optimize_socket(local_sock)
                    print(f"Connection #{conn_id}: Accepted local connection from {addr}")
                    
                    # Handle each connection in its own thread
                    connection_thread = threading.Thread(
                        target=self.handle_connection,
                        args=(local_sock, server_host, server_port, conn_id)
                    )
                    connection_thread.daemon = True
                    connection_thread.start()
                    
                except socket.timeout:
                    # This is expected, just continue
                    continue
                except KeyboardInterrupt:
                    print("Keyboard interrupt received, shutting down client")
                    break
                except Exception as e:
                    print(f"Error in client accept loop: {e}")
                    traceback.print_exc()
                    # Small delay to prevent CPU spinning on repeated errors
                    time.sleep(1)
        finally:
            self.safe_close(client, "client listener")
    
    def handle_connection(self, local_sock, server_host, server_port, conn_id=0):
        """Handle a new client connection."""
        server_sock = None
        connection = None
        
        try:
            # Connect to server
            server_sock = self.create_socket()
            try:
                server_sock.connect((server_host, server_port))
                self.optimize_socket(server_sock)
            except Exception as e:
                print(f"Connection #{conn_id}: Failed to connect to server: {e}")
                traceback.print_exc()
                self.safe_close(local_sock, "local")
                return

            print(f"Connection #{conn_id}: Connected to server {server_host}:{server_port}")
            
            # Create a new connection with its own dedicated compressor
            connection = TunnelConnection(
                tunnel=self,
                conn_id=conn_id,
                source_sock=local_sock,
                target_sock=server_sock,
                compression_algo=self.compression_algo,
                compression_level=self.compression_level,
                low_latency=self.low_latency,
                buffer_size=self.buffer_size,
                direction1to2_compressed=True
            )
            
            # Start the connection
            connection.start()
            
            # Wait for the connection to complete
            while not connection.shutdown_event.is_set():
                time.sleep(0.5)
                
            print(f"Connection #{conn_id}: Tunnel closed")
            
        except Exception as e:
            print(f"Connection #{conn_id}: Error in connection handler: {e}")
            traceback.print_exc()
            
            # Make sure connection is stopped and resources are cleaned up
            if connection:
                connection.stop()
            else:
                # Make sure sockets are closed if connection wasn't created
                self.safe_close(local_sock, f"local-{conn_id}")
                self.safe_close(server_sock, f"server-{conn_id}")


class ServerTunnel(Tunnel):
    """Server-side tunnel implementation."""
    
    def run(self, bind_port, target_host, target_port, compression_algo='zstd', 
            compression_level=3, low_latency=True, buffer_size=1024):
        """Run the server tunnel."""
        # Initialize with settings
        self.compression_algo = compression_algo
        self.compression_level = compression_level
        self.low_latency = low_latency
        self.buffer_size = buffer_size
        
        server = self.create_socket()
        try:
            server.bind(('0.0.0.0', bind_port))
            server.listen(5)
            print(f"Server listening on port {bind_port}")
            print(f"Using {compression_algo} compression (level {compression_level}, {'low-latency' if low_latency else 'high-compression'} mode)")
            print(f"Target: {target_host}:{target_port}")

            # Connection counter for logging
            conn_id = 0
            
            while True:
                try:
                    # Use a timeout to make sure we can handle interrupts
                    server.settimeout(1.0)
                    client_sock, addr = server.accept()
                    conn_id += 1
                    
                    # Apply optimizations to the connected socket
                    self.optimize_socket(client_sock)
                    print(f"Connection #{conn_id}: Accepted connection from {addr}")
                    
                    # Handle each connection in its own thread
                    connection_thread = threading.Thread(
                        target=self.handle_connection,
                        args=(client_sock, target_host, target_port, conn_id)
                    )
                    connection_thread.daemon = True
                    connection_thread.start()
                    
                except socket.timeout:
                    # This is expected, just continue
                    continue
                except KeyboardInterrupt:
                    print("Keyboard interrupt received, shutting down server")
                    break
                except Exception as e:
                    print(f"Error in server accept loop: {e}")
                    traceback.print_exc()
                    # Small delay to prevent CPU spinning on repeated errors
                    time.sleep(1)
        finally:
            self.safe_close(server, "server listener")
    
    def handle_connection(self, client_sock, target_host, target_port, conn_id=0):
        """Handle a new server connection."""
        target_sock = None
        connection = None
        
        try:
            # Connect to target
            target_sock = self.create_socket()
            try:
                target_sock.connect((target_host, target_port))
                self.optimize_socket(target_sock)
            except Exception as e:
                print(f"Connection #{conn_id}: Failed to connect to target: {e}")
                traceback.print_exc()
                self.safe_close(client_sock, "client")
                return
            
            print(f"Connection #{conn_id}: Connected to target {target_host}:{target_port}")
            
            # Create a new connection with its own dedicated compressor
            connection = TunnelConnection(
                tunnel=self,
                conn_id=conn_id,
                source_sock=client_sock,
                target_sock=target_sock,
                compression_algo=self.compression_algo,
                compression_level=self.compression_level,
                low_latency=self.low_latency,
                buffer_size=self.buffer_size,
                direction1to2_compressed=False
            )
            
            # Start the connection
            connection.start()
            
            # Wait for the connection to complete
            while not connection.shutdown_event.is_set():
                time.sleep(0.5)
                
            print(f"Connection #{conn_id}: Tunnel closed")
            
        except Exception as e:
            print(f"Connection #{conn_id}: Error in connection handler: {e}")
            traceback.print_exc()
            
            # Make sure connection is stopped and resources are cleaned up
            if connection:
                connection.stop()
            else:
                # Make sure sockets are closed if connection wasn't created
                self.safe_close(client_sock, f"client-{conn_id}")
                self.safe_close(target_sock, f"target-{conn_id}") 