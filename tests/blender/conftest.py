"""Keep pytest out of this directory.

Scripts here run inside Blender and import ``bpy``, which does not exist in a
plain interpreter, so a bare ``pytest tests`` aborts during collection without
this.
"""

collect_ignore_glob = ["*.py"]
