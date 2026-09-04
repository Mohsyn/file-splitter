# file-splitter
A fast and reliable Python tool for splitting large text files into smaller, more manageable files. Supports various size units (bytes, KB, MB) and line-aware splitting to preserve line integrity.

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
