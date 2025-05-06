"""Compression adapters for the tunnel."""
import abc
from enum import Enum, auto
import zstandard as zstd
import lz4.frame

class FlushMode(Enum):
    """Standardized flush modes across different compression libraries."""
    NO_FLUSH = auto()      # Don't flush, keep buffering
    PARTIAL_FLUSH = auto() # Flush data so far without ending the stream
    FULL_FLUSH = auto()    # Complete flush, finishing the stream


class CompressStream:
    """Standardized compression stream interface."""
    
    def __init__(self, level):
        """Initialize with the library-specific compressor object."""
        self.level = level
    
    def compress(self, data):
        """Compress data and return compressed bytes."""
        pass
    
    def flush(self, mode=FlushMode.PARTIAL_FLUSH):
        """Flush the compressor with the specified mode."""
        pass
    
    def end(self):
        """End the compression stream, flushing any remaining data."""
        pass
    
    def reset(self):
        """Reset the compressor state if supported."""
        pass


class DecompressStream:
    """Standardized decompression stream interface."""
    
    def __init__(self):
        """Initialize with the library-specific decompressor object."""
        pass
    
    def decompress(self, data):
        """Decompress data and return decompressed bytes."""
        pass
    
    def flush(self):
        """Flush any buffered decompressed data (if supported)."""
        pass
    
    def reset(self):
        """Reset the decompressor state if supported."""
        pass


class Compressor(abc.ABC):
    """Base compression interface."""
    
    @property
    def algorithm(self):
        """Get the algorithm name used by this compressor."""
        return "unknown"
        
    @abc.abstractmethod
    def create_compress_stream(self):
        """Create a standardized streaming compressor."""
        pass
        
    @abc.abstractmethod
    def create_decompress_stream(self):
        """Create a standardized streaming decompressor."""
        pass


class ZstdCompressStream(CompressStream):
    """Zstandard compression stream implementation."""
    
    def __init__(self, level):
        super().__init__(level)
        self.compressor = zstd.ZstdCompressor(level=level, threads=0).compressobj()


    def compress(self, data):
        try:
            return self.compressor.compress(data)
        except Exception as e:
            print(f"ZstdCompressStream compression error: {e}")
            self.reset()
            raise
    
    def flush(self, mode=FlushMode.PARTIAL_FLUSH):
        try:
            if mode == FlushMode.NO_FLUSH:
                return b''
            elif mode == FlushMode.PARTIAL_FLUSH:
                # zstd doesn't have a partial flush, but we can use regular flush
                # which seems to work well for streaming without ending the stream
                return self.compressor.flush(flush_mode=zstd.COMPRESSOBJ_FLUSH_BLOCK)
            elif mode == FlushMode.FULL_FLUSH:
                return self.compressor.flush()
        except Exception as e:
            print(f"ZstdCompressStream flush error: {e}")
            self.reset()
            raise
    
    def end(self):
        return self.flush(FlushMode.FULL_FLUSH)
    
    def reset(self):
        """Reset the compressor to a clean state."""
        try:
            self.compressor = zstd.ZstdCompressor(level=self.level).compressobj()
            print("ZstdCompressStream reset successfully")
        except Exception as e:
            print(f"Failed to reset ZstdCompressStream: {e}")


class ZstdDecompressStream(DecompressStream):
    """Zstandard decompression stream implementation."""
    
    def __init__(self):
        super().__init__()
        self.decompressor = zstd.ZstdDecompressor().decompressobj()
    
    def decompress(self, data):
        try:
            result = self.decompressor.decompress(data)
            return result
        except Exception as e:
            print(f"ZstdDecompressStream decompression error: {e}")
            self.reset()
            # For corrupted data, we can't recover the current frame
            # but we can reset and be ready for the next frame
            raise
    
    def flush(self):
        # Not needed for zstd decompression
        return b''
    
    def reset(self):
        """Reset the decompressor to a clean state."""
        try:
            # Create a fresh decompressor
            self.decompressor = zstd.ZstdDecompressor().decompressobj()
            print("ZstdDecompressStream reset successfully")
        except Exception as e:
            print(f"Failed to reset ZstdDecompressStream: {e}")


class LZ4CompressStream(CompressStream):
    """LZ4 compression stream implementation."""
    
    def __init__(self, level):
        super().__init__(level)
        self.context = lz4.frame.create_compression_context()
        # LZ4 needs to track if we've begun the frame
        self.frame_started = False
        self.frame_ended = False
    
    def compress(self, data):
        try:
            if self.frame_ended:
                # If we've ended the frame but got more data, we need to start a new one
                self.reset()
                
            if not self.frame_started:
                # Begin a new frame if needed
                self.frame_started = True
                compressed_data = lz4.frame.compress_begin(self.context, compression_level=self.level)
                # print(f"debug: lz4 begin {compressed_data}")
                compressed_data += lz4.frame.compress_chunk(self.context, data)
                # print(f"debug: lz4 compressed {compressed_data}")
            else:
                compressed_data = lz4.frame.compress_chunk(self.context, data)
            return compressed_data
        except Exception as e:
            print(f"LZ4CompressStream compression error: {e}")
            self.reset()
            raise
    
    def flush(self, mode=FlushMode.PARTIAL_FLUSH):
        try:
            if not self.frame_started or self.frame_ended:
                return b''
                
            if mode == FlushMode.NO_FLUSH:
                return b''
            elif mode == FlushMode.PARTIAL_FLUSH:
                data = lz4.frame.compress_flush(self.context, end_frame=False)
                # print(f"debug: lz4 flushed {data}")
                return data
            elif mode == FlushMode.FULL_FLUSH:
                self.frame_ended = False
                self.frame_started = False
                return lz4.frame.compress_flush(self.context, end_frame=True)
        except Exception as e:
            print(f"LZ4CompressStream flush error: {e}")
            self.reset()
            raise
    
    def end(self):
        try:
            if not self.frame_started or self.frame_ended:
                return b''
            self.frame_ended = False
            self.frame_started = False
            data = lz4.frame.compress_flush(self.context, end_frame=True)
            # print(f"debug: lz4 flushed end {data}")
            return data
        except Exception as e:
            print(f"LZ4CompressStream end error: {e}")
            self.reset()
            raise
    
    def reset(self):
        """Reset internal state and recreate compressor if needed."""
        try:
            self.context = lz4.frame.create_compression_context()
            self.frame_started = False
            self.frame_ended = False
            print("LZ4CompressStream reset successfully")
        except Exception as e:
            print(f"Failed to reset LZ4CompressStream: {e}")


class LZ4DecompressStream(DecompressStream):
    """LZ4 decompression stream implementation."""
    
    def __init__(self):
        super().__init__()
        self.decompressor = lz4.frame.LZ4FrameDecompressor()

    def decompress(self, data):
        try:
            # print(f"debug: lz4 raw data: {data}")
            return self.decompressor.decompress(data)
        except Exception as e:
            print(f"LZ4DecompressStream decompression error: {e}")
            self.reset()
            raise
    
    def flush(self):
        # Not needed for LZ4 decompression
        return b''
    
    def reset(self):
        """Reset the decompressor to a clean state."""
        try:
            self.decompressor.reset()
            print("LZ4DecompressStream reset successfully")
        except Exception as e:
            print(f"Failed to reset LZ4DecompressStream: {e}")


class ZstdCompressor(Compressor):
    """Zstandard compression implementation."""
    
    def __init__(self, level=3):
        self.level = level
    
    @property
    def algorithm(self):
        return "zstd"
        
    def create_compress_stream(self):
        return ZstdCompressStream(self.level)
        
    def create_decompress_stream(self):
        return ZstdDecompressStream()


# FIXME: lz4 compressor is not working
class LZ4Compressor(Compressor):
    """LZ4 compression implementation."""
    
    def __init__(self, level=1):
        self.level = level
    
    @property
    def algorithm(self):
        return "lz4"
        
    def create_compress_stream(self):
        return LZ4CompressStream(self.level)
        
    def create_decompress_stream(self):
        return LZ4DecompressStream()


# Factory function to get the appropriate compressor
def get_compressor(algorithm='zstd', level=3):
    """Get a compressor instance based on the specified algorithm."""
    if algorithm.lower() == 'zstd':
        return ZstdCompressor(level=level)
    elif algorithm.lower() == 'lz4':
        return LZ4Compressor(level=level)
    else:
        raise ValueError(f"Unsupported compression algorithm: {algorithm}") 