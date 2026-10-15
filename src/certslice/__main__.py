import sys
from certslice.cli import main

if len(sys.argv) == 1:
    from certslice.gui import launch_gui

    launch_gui()
    sys.exit(0)
else:
    sys.exit(main())
