complete -c paraninfodl -f

complete -c paraninfodl -l quality    -r -d 'JPEG quality 1-95 (0 = lossless)'
complete -c paraninfodl -l text-layer    -d 'Add selectable text layer to PDF'
complete -c paraninfodl -l keep-pages    -d 'Keep page images after building PDF'
complete -c paraninfodl -l output  -s o -F -d 'Output path for PDF'
complete -c paraninfodl -l jobs       -r -d 'Parallel download workers'
complete -c paraninfodl -l no-cache      -d 'Re-download all pages ignoring cache'
complete -c paraninfodl -l version       -d 'Show version and exit'
complete -c paraninfodl -l help    -s h  -d 'Show help'
