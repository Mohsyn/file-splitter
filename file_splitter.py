import os
import sys
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
import argparse
import time


# ============================================================================
# Error Handling Module
# ============================================================================

class SplitterError(Exception):
    """Base exception for file splitting operations."""
    pass


class FileNotFoundError(SplitterError):
    """Raised when input file is not found."""
    pass


class PermissionDeniedError(SplitterError):
    """Raised when file permissions deny access."""
    pass


class InvalidSizeError(SplitterError):
    """Raised when size specification is invalid."""
    pass


class InsufficientDiskSpaceError(SplitterError):
    """Raised when there's not enough disk space."""
    pass


class FileAlreadyExistsError(SplitterError):
    """Raised when output file already exists."""
    pass


class UserCancelledError(SplitterError):
    """Raised when user cancels operation."""
    pass


class CleanupFailedError(SplitterError):
    """Raised when cleanup fails."""
    pass


# ============================================================================
# Line Boundary Detection Module
# ============================================================================

class LineEnding(Enum):
    """Represents different types of line endings."""
    LF = "LF"       # Unix-style (\\n)
    CR = "CR"       # Classic Mac-style (\\r)
    CRLF = "CRLF"   # Windows-style (\\r\\n)
    
    def as_bytes(self) -> bytes:
        """Returns the byte representation of this line ending."""
        if self == LineEnding.LF:
            return b"\n"
        elif self == LineEnding.CR:
            return b"\r"
        else:  # CRLF
            return b"\r\n"
    
    def as_str(self) -> str:
        """Returns the string representation of this line ending."""
        if self == LineEnding.LF:
            return "\n"
        elif self == LineEnding.CR:
            return "\r"
        else:  # CRLF
            return "\r\n"
    
    def len(self) -> int:
        """Returns the length of this line ending in bytes."""
        return len(self.as_bytes())


def is_line_ending_byte(byte: int) -> bool:
    """Detects if a byte represents a line ending character (CR or LF)."""
    return byte == ord('\n') or byte == ord('\r')


def detect_line_endings(buffer: bytes) -> List[Tuple[int, LineEnding]]:
    """Detects line endings in a buffer and returns their positions and types."""
    endings = []
    i = 0
    
    while i < len(buffer):
        if buffer[i:i+1] == b'\r':
            if i + 1 < len(buffer) and buffer[i+1:i+2] == b'\n':
                endings.append((i, LineEnding.CRLF))
                i += 2
            else:
                endings.append((i, LineEnding.CR))
                i += 1
        elif buffer[i:i+1] == b'\n':
            endings.append((i, LineEnding.LF))
            i += 1
        else:
            i += 1
    
    return endings


def count_line_endings(buffer: bytes) -> Tuple[int, int, int]:
    """Counts the occurrences of different line ending types."""
    crlf_count = 0
    lf_count = 0
    cr_count = 0
    i = 0
    
    while i < len(buffer):
        if buffer[i:i+1] == b'\r':
            if i + 1 < len(buffer) and buffer[i+1:i+2] == b'\n':
                crlf_count += 1
                i += 2
            else:
                cr_count += 1
                i += 1
        elif buffer[i:i+1] == b'\n':
            lf_count += 1
            i += 1
        else:
            i += 1
    
    return (crlf_count, lf_count, cr_count)


def get_predominant_line_ending(buffer: bytes) -> LineEnding:
    """Determines the predominant line ending type used in a buffer."""
    crlf_count, lf_count, cr_count = count_line_endings(buffer)
    
    if crlf_count > 0 and crlf_count >= lf_count and crlf_count >= cr_count:
        return LineEnding.CRLF
    elif lf_count > 0 and lf_count >= cr_count:
        return LineEnding.LF
    elif cr_count > 0:
        return LineEnding.CR
    else:
        return LineEnding.LF  # Default


def find_line_boundary(buffer: bytes, max_pos: int) -> Optional[Tuple[int, LineEnding]]:
    """Finds the position of the last complete line boundary within the buffer."""
    if not buffer or max_pos == 0:
        return None
    
    search_limit = min(max_pos, len(buffer))
    
    for i in range(search_limit - 1, -1, -1):
        if buffer[i:i+1] == b'\n':
            if i > 0 and buffer[i-1:i] == b'\r':
                return (i + 1, LineEnding.CRLF)
            else:
                return (i + 1, LineEnding.LF)
        elif buffer[i:i+1] == b'\r':
            if i + 1 < len(buffer) and buffer[i+1:i+2] == b'\n':
                if i + 2 <= search_limit:
                    return (i + 2, LineEnding.CRLF)
                else:
                    continue
            else:
                return (i + 1, LineEnding.CR)
    
    return None


def find_next_line_boundary(buffer: bytes, start_pos: int) -> Optional[Tuple[int, LineEnding]]:
    """Finds the next line boundary starting from a given position."""
    if start_pos >= len(buffer):
        return None
    
    for i in range(start_pos, len(buffer)):
        if buffer[i:i+1] == b'\n':
            if i > 0 and buffer[i-1:i] == b'\r' and i - 1 >= start_pos:
                return (i + 1, LineEnding.CRLF)
            else:
                return (i + 1, LineEnding.LF)
        elif buffer[i:i+1] == b'\r':
            if i + 1 < len(buffer) and buffer[i+1:i+2] == b'\n':
                return (i + 2, LineEnding.CRLF)
            else:
                return (i + 1, LineEnding.CR)
    
    return None


def preserve_line_endings(source: bytes, start_pos: int, end_pos: int) -> bytes:
    """Preserves line endings when copying data between buffers."""
    if start_pos >= len(source) or start_pos >= end_pos:
        return b''
    
    actual_end = min(end_pos, len(source))
    return source[start_pos:actual_end]


def should_merge_small_remainder(remaining_bytes: int, max_size: int) -> bool:
    """Checks if remaining content should be merged with current file."""
    threshold = int(max_size * 0.1)
    return remaining_bytes <= threshold


def is_valid_line_split(buffer: bytes, split_pos: int) -> bool:
    """Validates that a split position maintains line integrity."""
    if split_pos == 0 or split_pos >= len(buffer):
        return True
    
    prev_byte = buffer[split_pos - 1:split_pos]
    
    if prev_byte == b'\n':
        return True
    
    if prev_byte == b'\r':
        if split_pos < len(buffer) and buffer[split_pos:split_pos+1] == b'\n':
            return False
        else:
            return True
    
    return False


# ============================================================================
# Progress Reporting Module
# ============================================================================

class ProgressReporter:
    """Progress reporter for file splitting operations."""
    
    def __init__(self, total_size: int):
        """Creates a new progress reporter."""
        self.total_size = total_size
        self.processed_size = 0
        self.start_time = time.time()
    
    def update(self, bytes_processed: int) -> None:
        """Updates the progress with the number of bytes processed."""
        self.processed_size = bytes_processed
    
    def display_progress(self) -> None:
        """Displays basic progress information."""
        if self.total_size > 0:
            percentage = (self.processed_size / self.total_size) * 100.0
        else:
            percentage = 0.0
        print(f"Progress: {percentage:.1f}%")
    
    def display_progress_with_file(self, current_file: str, line_aware: Optional[bool] = None) -> None:
        """Displays detailed progress information including current file and ETA."""
        percentage = self.get_progress_percentage()
        mode_indicator = " [Line-aware]" if line_aware else ""
        
        elapsed = time.time() - self.start_time
        if elapsed > 5:
            eta = self.estimated_time_remaining()
            if eta:
                eta_secs = int(eta)
                eta_mins = eta_secs // 60
                eta_secs_remainder = eta_secs % 60
                
                if eta_mins > 0:
                    print(f"Writing: {current_file}{mode_indicator} | Progress: {percentage:.1f}% | ETA: {eta_mins}m {eta_secs_remainder}s")
                else:
                    print(f"Writing: {current_file}{mode_indicator} | Progress: {percentage:.1f}% | ETA: {eta_secs}s")
            else:
                print(f"Writing: {current_file}{mode_indicator} | Progress: {percentage:.1f}%")
        else:
            print(f"Writing: {current_file}{mode_indicator} | Progress: {percentage:.1f}%")
    
    def get_progress_percentage(self) -> float:
        """Calculates the current progress as a percentage."""
        if self.total_size > 0:
            return (self.processed_size / self.total_size) * 100.0
        return 0.0
    
    def estimated_time_remaining(self) -> Optional[float]:
        """Estimates the time remaining for the operation to complete."""
        if self.processed_size == 0:
            return None
        
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return None
        
        rate = self.processed_size / elapsed
        remaining_bytes = self.total_size - self.processed_size
        remaining_seconds = remaining_bytes / rate
        
        return remaining_seconds


# ============================================================================
# Configuration Module
# ============================================================================

@dataclass
class Config:
    """Configuration for file splitting operations."""
    input_file: Path
    max_size: int
    output_dir: Optional[Path] = None
    force_overwrite: bool = False
    quiet_mode: bool = False
    line_aware: bool = False
    
    @staticmethod
    def parse_size(size_str: str) -> int:
        """Parses a size string with units into bytes."""
        size_str = size_str.strip().upper()
        
        if not size_str:
            raise InvalidSizeError("Empty size specification")
        
        try:
            if size_str.endswith("MB"):
                number_part = size_str[:-2]
                return int(number_part) * 1024 * 1024
            elif size_str.endswith("M"):
                number_part = size_str[:-1]
                return int(number_part) * 1024 * 1024
            elif size_str.endswith("KB"):
                number_part = size_str[:-2]
                return int(number_part) * 1024
            elif size_str.endswith("K"):
                number_part = size_str[:-1]
                return int(number_part) * 1024
            elif size_str.endswith("B"):
                number_part = size_str[:-1]
                return int(number_part)
            else:
                return int(size_str)
        except (ValueError, OverflowError) as e:
            raise InvalidSizeError(f"Invalid size specification: {size_str}")
    
    @staticmethod
    def new(input_file: Path, max_size: int, output_dir: Optional[Path] = None, 
            force_overwrite: bool = False, quiet_mode: bool = False, 
            line_aware: bool = False) -> 'Config':
        """Creates a new configuration with validation."""
        config = Config(input_file, max_size, output_dir, force_overwrite, quiet_mode, line_aware)
        config.validate_input_file()
        config.validate_line_aware_config()
        return config
    
    def validate_input_file(self) -> None:
        """Validates that the input file exists and is readable."""
        if not self.input_file.exists():
            raise FileNotFoundError(f"File not found: {self.input_file}")
        
        if not self.input_file.is_file():
            raise InvalidSizeError(f"Path is not a file: {self.input_file}")
        
        try:
            with open(self.input_file, 'rb'):
                pass
        except PermissionError:
            raise PermissionDeniedError(f"Permission denied: {self.input_file}")
    
    def validate_line_aware_config(self) -> None:
        """Validates line-aware configuration settings."""
        if self.line_aware:
            if self.max_size < 1024:
                raise InvalidSizeError(
                    f"Line-aware mode requires a minimum size of 1024 bytes, got {self.max_size} bytes. "
                    "Very small sizes may not accommodate single lines."
                )


# ============================================================================
# File Splitter Module
# ============================================================================

@dataclass
class SplitResult:
    """Result of a file splitting operation."""
    files_created: int
    total_size_processed: int
    output_files: List[Path]
    warnings: List[str]


class FileSplitter:
    """Main file splitter implementation."""
    
    def __init__(self, config: Config):
        """Creates a new FileSplitter with the given configuration."""
        self.config = config
        
        if not self.config.input_file.exists():
            raise FileNotFoundError(f"File not found: {self.config.input_file}")
        
        if not self.config.input_file.is_file():
            raise InvalidSizeError(f"Path is not a file: {self.config.input_file}")
        
        file_size = self.config.input_file.stat().st_size
        self.progress_reporter = ProgressReporter(file_size)
    
    def split(self) -> SplitResult:
        """Splits the input file according to the configuration."""
        file_size = self.config.input_file.stat().st_size
        
        if file_size <= self.config.max_size:
            result = self._split_small_file()
        else:
            if self.config.line_aware:
                result = self._split_line_aware()
            else:
                result = self._split_standard()
        
        self._display_final_summary(result)
        return result
    
    def _split_small_file(self) -> SplitResult:
        """Handles small files that don't need splitting."""
        output_path = self._generate_output_filename(1)
        
        if output_path.exists() and not self.config.force_overwrite:
            raise FileAlreadyExistsError(f"File already exists: {output_path}")
        
        # Copy the file
        with open(self.config.input_file, 'rb') as src:
            with open(output_path, 'wb') as dst:
                dst.write(src.read())
        
        file_size = self.config.input_file.stat().st_size
        
        return SplitResult(
            files_created=1,
            total_size_processed=file_size,
            output_files=[output_path],
            warnings=[]
        )
    
    def _split_standard(self) -> SplitResult:
        """Standard splitting method (not line-aware)."""
        output_files = []
        total_bytes_read = 0
        part_number = 1
        
        with open(self.config.input_file, 'rb') as f:
            while True:
                output_path = self._generate_output_filename(part_number)
                
                if output_path.exists() and not self.config.force_overwrite:
                    raise FileAlreadyExistsError(f"File already exists: {output_path}")
                
                with open(output_path, 'wb') as out_f:
                    current_file_size = 0
                    
                    while current_file_size < self.config.max_size:
                        buffer = f.read(64 * 1024)  # 64KB buffer
                        if not buffer:
                            break
                        
                        bytes_to_write = min(
                            len(buffer),
                            self.config.max_size - current_file_size
                        )
                        
                        out_f.write(buffer[:bytes_to_write])
                        current_file_size += bytes_to_write
                        total_bytes_read += bytes_to_write
                        
                        self.progress_reporter.update(total_bytes_read)
                        
                        if bytes_to_write < len(buffer):
                            # Seek back to the position where we stopped reading
                            f.seek(-(len(buffer) - bytes_to_write), 1)
                            break
                
                if current_file_size > 0:
                    output_files.append(output_path)
                    part_number += 1
                else:
                    # Remove empty file
                    try:
                        os.remove(output_path)
                    except:
                        pass
                    break
        
        return SplitResult(
            files_created=len(output_files),
            total_size_processed=total_bytes_read,
            output_files=output_files,
            warnings=[]
        )
    
    def _split_line_aware(self) -> SplitResult:
        """Line-aware splitting method."""
        output_files = []
        total_bytes_read = 0
        part_number = 1
        warnings = []
        
        file_size = self.config.input_file.stat().st_size
        self.progress_reporter = ProgressReporter(file_size)
        
        pending_buffer = bytearray()
        
        with open(self.config.input_file, 'rb') as f:
            output_path = self._generate_output_filename(part_number)
            
            if output_path.exists() and not self.config.force_overwrite:
                raise FileAlreadyExistsError(f"File already exists: {output_path}")
            
            current_output_file = open(output_path, 'wb')
            current_file_size = 0
            file_has_content = False
            
            while True:
                # Read a chunk
                buffer = f.read(256 * 1024)  # 256KB buffer
                if not buffer:
                    break
                
                # Combine pending buffer with new data
                if pending_buffer:
                    process_buffer = bytes(pending_buffer) + buffer
                    pending_buffer.clear()
                else:
                    process_buffer = buffer
                
                process_len = len(process_buffer)
                
                # Check if adding this chunk would exceed max_size
                if current_file_size + process_len > self.config.max_size:
                    available_space = max(0, self.config.max_size - current_file_size)
                    
                    # Check if there's any line boundary in the entire buffer
                    has_any_line_boundary = b'\n' in process_buffer or b'\r' in process_buffer
                    
                    # Try to find a line boundary within available space
                    boundary_pos = 0
                    boundary_found = False
                    
                    for i in range(min(available_space, process_len) - 1, -1, -1):
                        if process_buffer[i:i+1] in (b'\n', b'\r'):
                            if process_buffer[i:i+1] == b'\n':
                                boundary_pos = i + 1
                            elif i + 1 < process_len and process_buffer[i+1:i+2] == b'\n':
                                boundary_pos = i + 2
                            else:
                                boundary_pos = i + 1
                            boundary_found = True
                            break
                    
                    if boundary_found:
                        # Found boundary within available space
                        current_output_file.write(process_buffer[:boundary_pos])
                        total_bytes_read += boundary_pos
                        file_has_content = True
                        
                        # Finalize current file
                        current_output_file.close()
                        if file_has_content:
                            output_files.append(output_path)
                        
                        # Create new output file
                        part_number += 1
                        output_path = self._generate_output_filename(part_number)
                        current_output_file = open(output_path, 'wb')
                        
                        # Write remainder
                        current_output_file.write(process_buffer[boundary_pos:])
                        current_file_size = process_len - boundary_pos
                        total_bytes_read += process_len - boundary_pos
                        file_has_content = True
                    elif not has_any_line_boundary and current_file_size == 0:
                        # Line exceeds max_size
                        warnings.append(
                            f"Warning: Part {part_number} contains a line longer than the maximum size. "
                            f"Line was split at byte {available_space}"
                        )
                        
                        current_output_file.write(process_buffer[:available_space])
                        total_bytes_read += available_space
                        file_has_content = True
                        
                        # Finalize current file
                        current_output_file.close()
                        if file_has_content:
                            output_files.append(output_path)
                        
                        # Create new output file
                        part_number += 1
                        output_path = self._generate_output_filename(part_number)
                        current_output_file = open(output_path, 'wb')
                        
                        # Write remainder
                        current_output_file.write(process_buffer[available_space:])
                        current_file_size = process_len - available_space
                        total_bytes_read += process_len - available_space
                        file_has_content = True
                    else:
                        # Store as pending and read more
                        pending_buffer.extend(process_buffer)
                        
                        if len(pending_buffer) > 5 * 1024 * 1024:  # 5MB safety limit
                            warnings.append(
                                f"Warning: Part {part_number} contains a very long line exceeding 5MB. "
                                f"Line was split to prevent excessive memory usage."
                            )
                            
                            write_size = min(available_space, len(pending_buffer))
                            current_output_file.write(bytes(pending_buffer[:write_size]))
                            total_bytes_read += write_size
                            file_has_content = True
                            
                            # Finalize current file
                            current_output_file.close()
                            if file_has_content:
                                output_files.append(output_path)
                            
                            # Create new output file
                            part_number += 1
                            output_path = self._generate_output_filename(part_number)
                            current_output_file = open(output_path, 'wb')
                            current_file_size = 0
                            
                            pending_buffer = bytearray(pending_buffer[write_size:])
                        
                        continue
                else:
                    # Chunk fits within max_size
                    current_output_file.write(process_buffer)
                    current_file_size += process_len
                    total_bytes_read += process_len
                    file_has_content = True
                
                self.progress_reporter.update(total_bytes_read)
            
            # Handle remaining data
            if pending_buffer:
                if current_file_size + len(pending_buffer) > self.config.max_size:
                    current_output_file.close()
                    if file_has_content:
                        output_files.append(output_path)
                    
                    part_number += 1
                    output_path = self._generate_output_filename(part_number)
                    current_output_file = open(output_path, 'wb')
                
                current_output_file.write(bytes(pending_buffer))
                total_bytes_read += len(pending_buffer)
                file_has_content = True
            
            # Finalize last file
            current_output_file.close()
            if file_has_content:
                output_files.append(output_path)
            
            self.progress_reporter.update(total_bytes_read)
        
        return SplitResult(
            files_created=len(output_files),
            total_size_processed=total_bytes_read,
            output_files=output_files,
            warnings=warnings
        )
    
    def _generate_output_filename(self, part_number: int) -> Path:
        """Generates the output filename for the given part number."""
        input_filename = self.config.input_file.name
        
        output_dir = self.config.output_dir or self.config.input_file.parent
        
        # Split filename into name and extension
        if '.' in input_filename:
            parts = input_filename.rsplit('.', 1)
            base_name = parts[0]
            extension = parts[1]
        else:
            base_name = input_filename
            extension = ""
        
        # Generate output filename
        if not extension:
            output_filename = f"{base_name}_part_{part_number:03d}"
        else:
            output_filename = f"{base_name}_part_{part_number:03d}.{extension}"
        
        return output_dir / output_filename
    
    def _display_final_summary(self, result: SplitResult) -> None:
        """Displays the final summary of a splitting operation."""
        if self.config.quiet_mode:
            return
        
        print("\nSplitting complete!")
        print(f"Created {result.files_created} output files")
        print(f"Total size processed: {result.total_size_processed} bytes")
        
        if self.config.line_aware:
            print("Mode: Line-aware (preserving line boundaries)")
        
        # Display warnings
        if result.warnings:
            print("\nWarnings:")
            for warning in result.warnings:
                print(f"  - {warning}")
        
        # Display output files
        if result.files_created <= 10:
            print("\nOutput files:")
            for file_path in result.output_files:
                print(f"  - {file_path}")


# ============================================================================
# Main Entry Point
# ============================================================================

def print_help():
    """Prints help information for the file splitter."""
    help_text = """
=====================================================================
                        File Splitter v0.1.0                              
                                                                          
Split large text files into smaller files with specified maximum size
======================================================================

USAGE:
  python file_splitter.py <input_file> <max_size> [OPTIONS]

REQUIRED ARGUMENTS:
  input_file          Path to the input file to split
  max_size            Maximum size for each output file (e.g., 10MB, 1024KB, 512B)

OPTIONAL ARGUMENTS:
  -o, --output-dir    Output directory for split files (default: same as input)
  -f, --force         Overwrite existing files without confirmation
  -q, --quiet         Suppress progress output and run silently
  -l, --line-aware    Split at line boundaries to preserve line integrity
  -h, --help          Show this help message

SIZE FORMATS:
  1MB, 512MB          Megabytes (1024 × 1024 bytes)
  1M, 512M            Megabytes (shorthand)
  1KB, 512KB          Kilobytes (1024 bytes)
  1K, 512K            Kilobytes (shorthand)
  1024B, 1024         Bytes (explicit or numeric)

EXAMPLES:
  Basic splitting:
    python file_splitter.py server.log 10MB

  Split with custom output directory:
    python file_splitter.py data.txt 1024KB -o ./output/

  Force overwrite existing files:
    python file_splitter.py large.txt 5M --force

  Run quietly with line-aware mode:
    python file_splitter.py text.txt 1MB --line-aware -q

  Combine all options:
    python file_splitter.py document.txt 2MB -o ./chunks -f -l

NOTES:
  • Line-aware mode preserves complete lines and requires minimum size of 1024 bytes
  • Use --force to skip confirmation when output files already exist
  • Use --quiet to suppress all output messages
  • Exit codes: 0 (success), 1 (error)
"""
    print(help_text)


def main():
    """Main entry point for the file splitter."""
    # Check if no arguments provided
    if len(sys.argv) == 1:
        print_help()
        sys.exit(0)
    
    parser = argparse.ArgumentParser(
        description="Split large text files into smaller files with specified maximum size",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
        epilog="""
EXAMPLES:
  Split a large log file into 10MB chunks:
    python file_splitter.py server.log 10MB

  Split a data file into 1024KB parts in a specific directory:
    python file_splitter.py data.txt 1024KB -o ./output/

  Force overwrite existing files without confirmation:
    python file_splitter.py large.txt 5M --force

  Run quietly with minimal output:
    python file_splitter.py document.txt 2MB --quiet

  Split text file preserving line boundaries:
    python file_splitter.py text.txt 1MB --line-aware
        """
    )
    
    parser.add_argument("input_file", help="Path to the input file to split")
    parser.add_argument("max_size", help="Maximum size for each output file (e.g., 10MB, 1024KB, 512B)")
    parser.add_argument("-o", "--output-dir", help="Output directory for split files")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing files without confirmation")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output and run silently")
    parser.add_argument("-l", "--line-aware", action="store_true", help="Split at line boundaries to preserve line integrity")
    
    try:
        args = parser.parse_args()
    except SystemExit as e:
        # Catch argument parsing errors and show help
        if e.code != 0:
            print("\nUse '-h' or '--help' for more information.", file=sys.stderr)
        sys.exit(e.code)
    
    try:
        # Parse maximum size
        max_size = Config.parse_size(args.max_size)
        
        # Create configuration
        config = Config.new(
            Path(args.input_file),
            max_size,
            Path(args.output_dir) if args.output_dir else None,
            args.force,
            args.quiet,
            args.line_aware
        )
        
        if not args.quiet:
            print("File Splitter v0.1.0")
            print(f"Input file: {config.input_file}")
            print(f"Max size: {config.max_size} bytes")
            if config.output_dir:
                print(f"Output directory: {config.output_dir}")
            if args.force:
                print("Force overwrite: enabled")
            if args.line_aware:
                print("Line-aware splitting: enabled")
            print()
        
        # Create and execute splitter
        splitter = FileSplitter(config)
        result = splitter.split()
        
        # Display success summary
        if not args.quiet:
            print(f"\n✓ File splitting completed successfully!")
            print()
            print("Summary:")
            print(f"  Files created: {result.files_created}")
            print(f"  Total size processed: {result.total_size_processed} bytes ({result.total_size_processed / (1024.0 * 1024.0):.2f} MB)")
            
            if result.warnings:
                print()
                print("Warnings:")
                for warning in result.warnings:
                    print(f"  - {warning}")
            
            if result.output_files:
                print()
                print("Output files:")
                for i, file_path in enumerate(result.output_files, 1):
                    file_size = file_path.stat().st_size if file_path.exists() else 0
                    print(f"  {i}: {file_path} ({file_size} bytes)")
    
    except (FileNotFoundError, PermissionDeniedError, InvalidSizeError, FileAlreadyExistsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
